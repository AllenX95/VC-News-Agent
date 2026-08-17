"""Headless daily-run orchestration.

``DailyRunOrchestrator`` is the application-facing seam for a complete daily
run.  GUI/API adapters and the command line entrypoint should call ``health`` or
``run`` instead of coordinating the individual services themselves.

The module deliberately keeps all filesystem writes in one run directory and
uses atomic replacement for JSON, Markdown and HTML artifacts.  It does not
persist provider credentials or decrypted configuration in the manifest.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from .report_data import (
    build_daily_report_data,
    validate_report_data,
)
from .html_renderer import render_daily_html
from .run_lock import DEFAULT_MAX_RUNTIME_SECONDS, RunLock
from .daily_report_llm import enrich_daily_report


EXIT_SUCCESS = 0
EXIT_SKIPPED = 1
EXIT_PARTIAL = 2
EXIT_PREFLIGHT = 10
EXIT_PIPELINE = 20
EXIT_SCHEMA = 30
EXIT_RENDER = 40
EXIT_LOCK = 50

TIMEZONE = ZoneInfo("Asia/Shanghai")
MANIFEST_VERSION = "1.0"
TEMPLATE_VERSION = "daily-v1"
DEFAULT_MAX_RUNTIME_SECONDS = 6 * 60 * 60

_SENSITIVE_ERROR_RE = re.compile(
    r"(?is)(?:api[_ -]?key|authorization|password|secret|access[_ -]?token)\s*[:=]\s*[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_RE = re.compile(r"(?i)\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}\b")


@dataclass(slots=True)
class DailyRunOptions:
    """Non-business controls for one daily report run."""

    target_date: str | date | None = None
    force: bool = False
    skip_crawl: bool = False
    output_root: str | Path | None = None

    def normalized_target_date(self, now: datetime | None = None) -> str:
        value = self.target_date
        if value is None or value == "":
            current = now or datetime.now(TIMEZONE)
            if current.tzinfo is None:
                current = current.replace(tzinfo=TIMEZONE)
            return current.astimezone(TIMEZONE).date().isoformat()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=TIMEZONE)
            return value.astimezone(TIMEZONE).date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        parsed = date.fromisoformat(str(value))
        return parsed.isoformat()


@dataclass(slots=True)
class RunResult:
    """Stable result returned by ``DailyRunOrchestrator.run``."""

    run_id: str
    status: str
    exit_code: int
    target_date: str
    manifest_path: Path | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    stages: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.manifest_path is not None:
            result["manifest_path"] = str(self.manifest_path)
        return result

    def __getitem__(self, key: str) -> Any:
        """Allow thin adapters to treat a result like a JSON object."""

        return self.to_dict()[key]


class DailyRunOrchestrator:
    """Run a daily crawl/report/render pipeline behind two small interfaces."""

    def __init__(
        self,
        *,
        runtime_root: str | Path | None = None,
        runtime_dir: str | Path | None = None,
        output_root: str | Path | None = None,
        db_factory: Callable[[], Any] | None = None,
        session_factory: Callable[[], Any] | None = None,
        create_db_fn: Callable[[], Any] | None = None,
        seed_fn: Callable[[Any], Any] | None = None,
        apply_proxy_fn: Callable[[Any], Any] | None = None,
        crawl_adapter: Any | None = None,
        crawl_service: Any | None = None,
        crawl: Any | None = None,
        report: Any | None = None,
        report_contract: Any | None = None,
        report_builder: Callable[..., Any] | None = None,
        report_validator: Callable[[Any], Any] | None = None,
        report_renderer: Callable[[Any, str | Path], Any] | None = None,
        html_renderer: Callable[[Any, str | Path], Any] | None = None,
        render: Any | None = None,
        report_enricher: Callable[[Any, dict[str, Any]], Any] | None = None,
        summary_adapter: Any | None = None,
        summary_service: Any | None = None,
        now_fn: Callable[[], datetime] | None = None,
        max_runtime_seconds: int | None = None,
        lock_path: str | Path | None = None,
    ) -> None:
        from .config import DATA_DIR

        configured_root = os.environ.get("VC_NEWS_RUNTIME_DIR") or os.environ.get("VC_NEWS_RUNS_DIR")
        root = runtime_root or runtime_dir or output_root or configured_root or (DATA_DIR / "runs")
        self.runtime_root = Path(root).expanduser()
        self._db_factory = db_factory or session_factory
        self._create_db = create_db_fn
        self._seed = seed_fn
        self._apply_proxy = apply_proxy_fn
        self._crawl = crawl_adapter if crawl_adapter is not None else crawl_service if crawl_service is not None else crawl
        contract = report_contract if report_contract is not None else report
        self._build_report = report_builder or _adapter_method(contract, "build_daily_report_data")
        self._validate_report = report_validator or _adapter_method(contract, "validate_report_data")
        self._render_report = report_renderer or html_renderer or render or _adapter_method(contract, "render_daily_html")
        self._enrich_report = report_enricher
        self._summary = summary_adapter if summary_adapter is not None else summary_service
        self._now = now_fn or (lambda: datetime.now(TIMEZONE))
        self._max_runtime_seconds = max_runtime_seconds or int(
            os.environ.get("VC_NEWS_MAX_RUNTIME_SECONDS", DEFAULT_MAX_RUNTIME_SECONDS)
        )
        self._lock_path_override = Path(lock_path) if lock_path is not None else None

    def health(self) -> dict[str, Any]:
        """Check local prerequisites without crawling or invoking an LLM."""

        checks: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        checks["python"] = {"status": "success", "version": f"{os.sys.version_info.major}.{os.sys.version_info.minor}"}

        runtime = self.runtime_root
        try:
            runtime.mkdir(parents=True, exist_ok=True)
            probe = runtime / f".health-{os.getpid()}-{secrets.token_hex(4)}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            checks["runtime"] = {"status": "success"}
        except Exception as exc:  # noqa: BLE001
            message = _safe_error(exc)
            checks["runtime"] = {"status": "failed", "error": message}
            errors.append(f"runtime: {message}")

        try:
            self._ensure_database(create_only=True)
            checks["database"] = {"status": "success"}
        except Exception as exc:  # noqa: BLE001
            message = _safe_error(exc)
            checks["database"] = {"status": "failed", "error": message}
            errors.append(f"database: {message}")

        try:
            from .config import SECRET_KEY_PATH

            checks["secret_key"] = {"status": "success" if SECRET_KEY_PATH.exists() else "missing"}
            if not SECRET_KEY_PATH.exists():
                errors.append("secret_key: key file is missing")
        except Exception as exc:  # noqa: BLE001
            message = _safe_error(exc)
            checks["secret_key"] = {"status": "failed", "error": message}
            errors.append(f"secret_key: {message}")

        try:
            # Importing the contract is intentionally lightweight; no network or
            # model call is made here.  The report data and renderer modules are
            # the concrete worker contract for this application.
            from . import html_renderer, report_data  # noqa: F401

            checks["report_contract"] = {"status": "success"}
        except Exception as exc:  # noqa: BLE001
            message = _safe_error(exc)
            checks["report_contract"] = {"status": "failed", "error": message}
            errors.append(f"report_contract: {message}")

        status = "success" if not errors else "preflight_failed"
        return {
            "status": status,
            "healthy": not errors,
            "exit_code": EXIT_SUCCESS if not errors else EXIT_PREFLIGHT,
            "checks": checks,
            "warnings": [],
            "errors": errors,
        }

    def run(self, options: DailyRunOptions | None = None) -> RunResult:
        """Execute one synchronous run and return a stable result."""

        options = options or DailyRunOptions()
        try:
            target_date = options.normalized_target_date(self._now())
        except (TypeError, ValueError, OverflowError) as exc:
            return self._unpersisted_result(
                status="preflight_failed",
                exit_code=EXIT_PREFLIGHT,
                target_date=str(options.target_date or ""),
                error=_safe_error(exc),
            )

        root = Path(options.output_root).expanduser() if options.output_root else self.runtime_root
        artifacts_root = root / "artifacts"
        report_root = root / "report"
        try:
            root.mkdir(parents=True, exist_ok=True)
            artifacts_root.mkdir(parents=True, exist_ok=True)
            report_root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            return self._unpersisted_result(
                status="preflight_failed",
                exit_code=EXIT_PREFLIGHT,
                target_date=target_date,
                error=_safe_error(exc),
            )

        if not options.force:
            previous = self._find_successful_manifest(artifacts_root, target_date)
            if previous is not None:
                return self._result_from_manifest(previous, skipped=True)

        run_id = self._new_run_id()
        date_token = target_date.replace("-", "")
        file_prefix = f"{date_token}-{run_id}"
        manifest_path = artifacts_root / f"{file_prefix}-run-manifest.json"
        log_path = artifacts_root / f"{file_prefix}-run.log"
        started_at = self._timestamp()
        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_VERSION,
            "run_id": run_id,
            "command": "daily",
            "target_date": target_date,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "stages": {
                "preflight": "running",
                "crawl": "pending",
                "enrichment": "pending",
                "report_data": "pending",
                "html_render": "pending",
                "markdown": "pending",
            },
            "counts": {},
            "artifacts": {"manifest": str(manifest_path), "log": str(log_path)},
            "warnings": [],
            "error": None,
        }
        try:
            _atomic_write_json(manifest_path, manifest)
            _atomic_write_text(log_path, f"{started_at} run started\n")
        except Exception as exc:  # noqa: BLE001
            return self._unpersisted_result(
                run_id=run_id,
                status="preflight_failed",
                exit_code=EXIT_PREFLIGHT,
                target_date=target_date,
                error=_safe_error(exc),
                manifest_path=manifest_path,
            )

        lock_path = self._lock_path_override or (root / ".daily-run.lock")
        lock = RunLock(lock_path, run_id, max_runtime_seconds=self._max_runtime_seconds)
        try:
            acquired, owner = lock.acquire()
        except Exception as exc:  # noqa: BLE001
            error = _safe_error(exc)
            manifest["status"] = "preflight_failed"
            manifest["finished_at"] = self._timestamp()
            manifest["stages"]["preflight"] = "failed"
            manifest["error"] = error
            self._write_manifest_safely(manifest_path, manifest)
            return RunResult(
                run_id=run_id,
                status="preflight_failed",
                exit_code=EXIT_PREFLIGHT,
                target_date=target_date,
                manifest_path=manifest_path,
                artifacts={"manifest": str(manifest_path), "log": str(log_path)},
                error=error,
                stages=dict(manifest["stages"]),
            )
        if not acquired:
            manifest["status"] = "lock_conflict"
            manifest["finished_at"] = self._timestamp()
            manifest["stages"]["preflight"] = "failed"
            manifest["error"] = "another daily run is active"
            manifest["lock_owner"] = _safe_lock_owner(owner)
            self._write_manifest_safely(manifest_path, manifest)
            return RunResult(
                run_id=run_id,
                status="lock_conflict",
                exit_code=EXIT_LOCK,
                target_date=target_date,
                manifest_path=manifest_path,
                artifacts={"manifest": str(manifest_path), "log": str(log_path)},
                error="another daily run is active",
                stages=dict(manifest["stages"]),
            )

        warnings: list[str] = []
        counts: dict[str, int] = {}
        artifacts: dict[str, str] = {"manifest": str(manifest_path), "log": str(log_path)}
        status = "success"
        exit_code = EXIT_SUCCESS
        error: str | None = None
        try:
            self._log(log_path, "preflight started")
            try:
                self._prepare_database()
            except Exception as exc:  # noqa: BLE001
                status, exit_code, error = "preflight_failed", EXIT_PREFLIGHT, _safe_error(exc)
                manifest["stages"]["preflight"] = "failed"
                raise _RunAbort from exc
            manifest["stages"]["preflight"] = "success"
            self._write_manifest_safely(manifest_path, manifest)

            with self._session_scope() as db:
                crawl_result: Any = None
                historical_target = target_date != self._as_bj(self._now()).date().isoformat()
                if options.skip_crawl or historical_target:
                    manifest["stages"]["crawl"] = "skipped"
                    self._log(log_path, "crawl skipped by option" if options.skip_crawl else "crawl skipped for historical target")
                else:
                    manifest["stages"]["crawl"] = "running"
                    self._write_manifest_safely(manifest_path, manifest)
                    try:
                        crawl_result = self._run_crawl(db, target_date)
                        crawl_status, crawl_counts, crawl_warnings = _crawl_summary(crawl_result)
                        counts.update(crawl_counts)
                        warnings.extend(crawl_warnings)
                        manifest["stages"]["crawl"] = crawl_status
                        if crawl_status == "partial":
                            status = "partial"
                            warnings.append("one or more information sources failed")
                        elif crawl_status == "failed":
                            status, exit_code, error = "pipeline_failed", EXIT_PIPELINE, "crawl service returned failed"
                            raise _RunAbort
                    except _RunAbort:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        status, exit_code, error = "pipeline_failed", EXIT_PIPELINE, _safe_error(exc)
                        manifest["stages"]["crawl"] = "failed"
                        raise _RunAbort from exc

                report_warnings = list(warnings)
                manifest["stages"]["report_data"] = "running"
                self._write_manifest_safely(manifest_path, manifest)
                try:
                    report_data = self._build_daily_report(db, target_date, report_warnings)
                except Exception as exc:  # noqa: BLE001
                    status, exit_code, error = "pipeline_failed", EXIT_PIPELINE, _safe_error(exc)
                    manifest["stages"]["report_data"] = "failed"
                    raise _RunAbort from exc

                manifest["stages"]["enrichment"] = "running"
                self._write_manifest_safely(manifest_path, manifest)
                try:
                    enrichment = self._enrich_daily_report(db, report_data)
                    report_data = enrichment.report
                    generation_mode = str(enrichment.generation_mode or "deterministic")
                    enrichment_warnings = [str(item) for item in (enrichment.warnings or []) if str(item).strip()]
                    warnings.extend(item for item in enrichment_warnings if item not in warnings)
                    manifest["report_generation"] = {
                        "generation_mode": generation_mode,
                        **{
                            str(key): value
                            for key, value in enrichment.metadata.items()
                            if value is not None
                        },
                    }
                    if generation_mode == "llm":
                        manifest["stages"]["enrichment"] = "success"
                    elif generation_mode == "partial":
                        manifest["stages"]["enrichment"] = "partial"
                        status = "partial"
                    else:
                        manifest["stages"]["enrichment"] = "skipped"
                except Exception as exc:  # noqa: BLE001
                    # Enrichment is best effort. The deterministic report remains
                    # the trusted fallback and is still schema-validated below.
                    warning = f"daily report LLM enrichment unavailable: {_safe_error(exc)}"
                    warnings.append(warning)
                    if isinstance(report_data, dict):
                        report_data["generation_mode"] = "partial"
                        existing = report_data.get("warnings")
                        report_data["warnings"] = list(dict.fromkeys([*(existing if isinstance(existing, list) else []), warning]))
                    manifest["stages"]["enrichment"] = "partial"
                    manifest["report_generation"] = {"generation_mode": "partial"}
                    status = "partial"

                try:
                    validation_errors = self._validate_daily_report(report_data)
                except Exception as exc:  # noqa: BLE001
                    status, exit_code, error = "report_data_invalid", EXIT_SCHEMA, _safe_error(exc)
                    manifest["stages"]["report_data"] = "failed"
                    raise _RunAbort from exc
                if validation_errors:
                    status, exit_code, error = "report_data_invalid", EXIT_SCHEMA, _safe_error("; ".join(validation_errors[:5]))
                    manifest["stages"]["report_data"] = "failed"
                    raise _RunAbort
                manifest["stages"]["report_data"] = "success"
                if isinstance(report_data, dict):
                    report_warnings = report_data.get("warnings") or []
                    if isinstance(report_warnings, list):
                        warnings.extend(str(item) for item in report_warnings if str(item) not in warnings)
                    stats = report_data.get("stats")
                    if isinstance(stats, dict):
                        counts.update({str(k): int(v) for k, v in stats.items() if isinstance(v, (int, float))})
                report_data_path = artifacts_root / f"{file_prefix}-report-data.json"
                try:
                    _atomic_write_json(report_data_path, report_data)
                except Exception as exc:  # noqa: BLE001
                    status, exit_code, error = "report_data_invalid", EXIT_SCHEMA, _safe_error(exc)
                    raise _RunAbort from exc
                artifacts["report_data"] = str(report_data_path)

                manifest["stages"]["markdown"] = "running"
                try:
                    markdown = self._render_daily_markdown(db, target_date)
                    markdown_path = artifacts_root / f"{file_prefix}-daily-report.md"
                    _atomic_write_text(markdown_path, markdown)
                    artifacts["markdown"] = str(markdown_path)
                    manifest["stages"]["markdown"] = "success"
                except Exception as exc:  # noqa: BLE001
                    manifest["stages"]["markdown"] = "partial"
                    warnings.append(f"markdown artifact unavailable: {_safe_error(exc)}")

                manifest["stages"]["html_render"] = "running"
                self._write_manifest_safely(manifest_path, manifest)
                html_path = report_root / f"{date_token}-daily-report.html"
                try:
                    rendered = self._render_daily_report(report_data, html_path)
                    html_path = Path(rendered) if rendered else html_path
                    if not html_path.exists():
                        raise FileNotFoundError("HTML renderer did not create an output file")
                    artifacts["html"] = str(html_path)
                    manifest["stages"]["html_render"] = "success"
                except Exception as exc:  # noqa: BLE001
                    status, exit_code, error = "html_render_failed", EXIT_RENDER, _safe_error(exc)
                    manifest["stages"]["html_render"] = "failed"
                    raise _RunAbort from exc

                if warnings and status == "success":
                    status, exit_code = "partial", EXIT_PARTIAL
                elif status == "partial" and exit_code == EXIT_SUCCESS:
                    exit_code = EXIT_PARTIAL
        except _RunAbort:
            pass
        except Exception as exc:  # noqa: BLE001
            status, exit_code, error = "pipeline_failed", EXIT_PIPELINE, _safe_error(exc)
        finally:
            lock.release()

        manifest["status"] = status
        manifest["finished_at"] = self._timestamp()
        manifest["counts"] = counts
        manifest["warnings"] = _unique_strings(warnings)
        manifest["error"] = _safe_error(error) if error else None
        manifest["artifacts"] = {**artifacts}
        self._write_manifest_safely(manifest_path, manifest)
        self._log(log_path, f"run finished status={status} exit_code={exit_code}")
        if status in {"success", "partial"}:
            self._atomic_update_latest(artifacts_root, target_date, run_id, status, manifest_path)

        return RunResult(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            target_date=target_date,
            manifest_path=manifest_path,
            artifacts=artifacts,
            warnings=_unique_strings(warnings),
            error=error,
            stages=dict(manifest["stages"]),
            counts=counts,
        )

    # ---- dependency seams -------------------------------------------------

    def _ensure_database(self, *, create_only: bool = False) -> None:
        if self._create_db is None:
            from .database import create_db

            create_db()
        else:
            self._create_db()
        if create_only:
            with self._session_scope() as db:
                # A scalar query catches a missing/broken schema without doing
                # a crawl or consuming an LLM call.
                if hasattr(db, "execute"):
                    try:
                        from sqlalchemy import text

                        db.execute(text("SELECT 1"))
                    except ImportError:
                        db.execute("SELECT 1")

    def _prepare_database(self) -> None:
        self._ensure_database()
        with self._session_scope() as db:
            if self._seed is None:
                from .seed import seed_all

                seed_all(db)
            else:
                self._seed(db)
            if self._apply_proxy is None:
                from .services import apply_configured_proxy_settings

                apply_configured_proxy_settings(db)
            else:
                self._apply_proxy(db)

    @contextlib.contextmanager
    def _session_scope(self) -> Iterator[Any]:
        factory = self._db_factory
        if factory is None:
            from .database import SessionLocal

            factory = SessionLocal
        candidate = factory()
        if hasattr(candidate, "__enter__") and hasattr(candidate, "__exit__"):
            with candidate as db:
                yield db
            return
        try:
            yield candidate
        finally:
            close = getattr(candidate, "close", None)
            if callable(close):
                close()

    def _run_crawl(self, db: Any, target_date: str) -> Any:
        adapter = self._crawl
        if adapter is None:
            from .services import CrawlService

            adapter = CrawlService()
        method = getattr(adapter, "run_all_sources", None)
        if callable(method):
            return method(db, manual=True, run_timestamp=self._db_timestamp())
        return _invoke_adapter(adapter, db=db, target_date=target_date)

    def _build_daily_report(self, db: Any, target_date: str, warnings: list[str]) -> Any:
        builder = self._build_report or build_daily_report_data
        if not callable(builder):
            builder = _adapter_method(builder, "build_daily_report_data") or build_daily_report_data
        return _invoke_report_builder(builder, db, target_date, warnings)

    def _validate_daily_report(self, report_data: Any) -> list[str]:
        validator = self._validate_report or validate_report_data
        if not callable(validator):
            validator = _adapter_method(validator, "validate_report_data") or validate_report_data
        result = validator(report_data)
        if result is True or result is None:
            return []
        if result is False:
            return ["report data validator returned false"]
        if isinstance(result, dict):
            if result.get("valid") is True:
                return []
            return [str(item) for item in (result.get("errors") or result.get("violations") or ["report data is invalid"])]
        if isinstance(result, (list, tuple, set)):
            return [str(item) for item in result]
        return [] if bool(result) else ["report data is invalid"]

    def _enrich_daily_report(self, db: Any, report_data: dict[str, Any]) -> Any:
        enricher = self._enrich_report or enrich_daily_report
        return enricher(db, report_data)

    def _render_daily_report(self, report_data: Any, output_path: Path) -> Path:
        renderer = self._render_report or render_daily_html
        if not callable(renderer):
            renderer = _adapter_method(renderer, "render_daily_html") or render_daily_html
        rendered = _invoke_renderer(renderer, report_data, output_path)
        return Path(rendered) if rendered else output_path

    def _render_daily_markdown(self, db: Any, target_date: str) -> str:
        adapter = self._summary
        if adapter is None:
            from .services import DailySummaryService

            adapter = DailySummaryService()
        method = getattr(adapter, "generate", None)
        if callable(method):
            summary = method(db, target_date=target_date)
            markdown = getattr(summary, "markdown_text", None)
            if markdown:
                return str(markdown)
            if isinstance(summary, dict):
                return str(summary.get("markdown") or summary.get("markdown_text") or "")
            if isinstance(summary, str):
                return summary
        result = _invoke_adapter(adapter, db=db, target_date=target_date)
        return str(result or "")

    # ---- persistence helpers ---------------------------------------------

    def _find_successful_manifest(self, artifacts_root: Path, target_date: str) -> Path | None:
        date_token = target_date.replace("-", "")
        latest = artifacts_root / f"{date_token}-latest.json"
        candidates: list[Path] = []
        if latest.exists():
            try:
                payload = json.loads(latest.read_text(encoding="utf-8"))
                manifest = Path(str(payload.get("manifest_path") or ""))
                if not manifest.is_absolute():
                    manifest = artifacts_root / manifest
                candidates.append(manifest)
            except (OSError, ValueError, TypeError):
                pass
        candidates.extend(
            sorted(
                artifacts_root.glob(f"{date_token}-*-run-manifest.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
        seen: set[Path] = set()
        for manifest in candidates:
            manifest = manifest.resolve()
            if manifest in seen or not manifest.exists():
                continue
            seen.add(manifest)
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if payload.get("status") == "success":
                return manifest
        return None

    def _result_from_manifest(self, path: Path, *, skipped: bool) -> RunResult:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            return RunResult(
                run_id="",
                status="preflight_failed",
                exit_code=EXIT_PREFLIGHT,
                target_date="",
                manifest_path=path,
                error=_safe_error(exc),
            )
        artifacts = {
            str(key): str(value)
            for key, value in (payload.get("artifacts") or {}).items()
            if value is not None
        }
        return RunResult(
            run_id=str(payload.get("run_id") or ""),
            status="skipped_already_success" if skipped else str(payload.get("status") or "success"),
            exit_code=EXIT_SKIPPED if skipped else _exit_code_for_status(str(payload.get("status") or "success")),
            target_date=str(payload.get("target_date") or ""),
            manifest_path=path,
            artifacts=artifacts,
            warnings=[str(item) for item in (payload.get("warnings") or [])],
            error=payload.get("error"),
            stages=dict(payload.get("stages") or {}),
            counts={str(k): int(v) for k, v in (payload.get("counts") or {}).items() if isinstance(v, (int, float))},
        )

    def _atomic_update_latest(
        self, artifacts_root: Path, target_date: str, run_id: str, status: str, manifest_path: Path
    ) -> None:
        latest = artifacts_root / f"{target_date.replace('-', '')}-latest.json"
        payload = {
            "schema_version": MANIFEST_VERSION,
            "target_date": target_date,
            "run_id": run_id,
            "status": status,
            "manifest_path": str(manifest_path.resolve()),
            "updated_at": self._timestamp(),
        }
        try:
            _atomic_write_json(latest, payload)
        except OSError:
            # Failure to update a convenience pointer must not corrupt the
            # completed run or make a valid HTML unavailable.
            return

    def _write_manifest_safely(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            _atomic_write_json(path, payload)
        except OSError:
            return

    def _log(self, path: Path, line: str) -> None:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{self._timestamp()} {_safe_error(line)}\n")
        except OSError:
            return

    def _new_run_id(self) -> str:
        return f"{self._as_bj(self._now()).strftime('%Y%m%dT%H%M%S%z')}-{secrets.token_hex(3)}"

    def _timestamp(self) -> str:
        return self._as_bj(self._now()).isoformat(timespec="seconds")

    def _db_timestamp(self) -> datetime:
        return self._as_bj(self._now()).replace(tzinfo=None)

    @staticmethod
    def _as_bj(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=TIMEZONE)
        return value.astimezone(TIMEZONE)

    def _unpersisted_result(
        self,
        *,
        status: str,
        exit_code: int,
        target_date: str,
        error: str,
        run_id: str = "",
        manifest_path: Path | None = None,
    ) -> RunResult:
        return RunResult(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            target_date=target_date,
            manifest_path=manifest_path,
            error=_safe_error(error),
        )


class _RunAbort(Exception):
    """Internal control flow after a manifest-worthy stage failure."""


def _adapter_method(adapter: Any | None, name: str) -> Callable[..., Any] | None:
    if adapter is None:
        return None
    method = getattr(adapter, name, None)
    return method if callable(method) else None


def _invoke_adapter(adapter: Any, **kwargs: Any) -> Any:
    if not callable(adapter):
        raise TypeError("adapter must be callable or expose a supported method")
    function = adapter
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(kwargs.get("db"), kwargs.get("target_date"))
    params = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()):
        return function(**kwargs)
    selected = {name: value for name, value in kwargs.items() if name in params}
    positional: list[Any] = []
    keyword: dict[str, Any] = {}
    fallback_values = [value for name, value in (("db", kwargs.get("db")), ("target_date", kwargs.get("target_date"))) if value is not None]
    fallback_index = 0
    for name, parameter in params.items():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if name in selected:
            value = selected[name]
        elif fallback_index < len(fallback_values):
            value = fallback_values[fallback_index]
        elif parameter.default is not inspect.Parameter.empty:
            continue
        else:
            continue
        fallback_index += 1
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            positional.append(value)
        else:
            keyword[name] = value
    return function(*positional, **keyword)


def _invoke_report_builder(builder: Callable[..., Any], db: Any, target_date: str, warnings: list[str]) -> Any:
    try:
        signature = inspect.signature(builder)
    except (TypeError, ValueError):
        return builder(db, target_date, warnings=warnings)
    params = signature.parameters
    kwargs = {}
    if "warnings" in params or any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        kwargs["warnings"] = warnings
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return builder(db, target_date, warnings=warnings)
    return builder(db, target_date, **kwargs)


def _invoke_renderer(renderer: Callable[..., Any], report_data: Any, output_path: Path) -> Any:
    try:
        signature = inspect.signature(renderer)
    except (TypeError, ValueError):
        return renderer(report_data, output_path)
    params = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in params):
        return renderer(report_data, output_path)
    # The worker contract is positional by design; retaining keyword support
    # helps simple fakes with descriptive parameter names.
    if len(params) >= 2:
        return renderer(report_data, output_path)
    return renderer(report_data)


def _crawl_summary(value: Any) -> tuple[str, dict[str, int], list[str]]:
    def read(name: str, default: Any = 0) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    raw_status = str(read("status", "success") or "success").lower()
    status = "partial" if raw_status in {"partial", "partial_success", "warning", "degraded"} else "failed" if raw_status in {"failed", "error"} else "success"
    failed = int(read("failed_items", read("failed_sources", 0)) or 0)
    total = int(read("total_items", read("raw_items", 0)) or 0)
    new_items = int(read("new_items", read("items_created", 0)) or 0)
    succeeded_sources = int(read("sources_succeeded", 0) or 0)
    failed_sources = int(read("sources_failed", 0) or 0)
    if failed and status == "success":
        status = "partial"
    counts = {
        "raw_items": total,
        "items_created": new_items,
        "failed_items": failed,
        "sources_succeeded": succeeded_sources,
        "sources_failed": failed_sources,
    }
    warnings = []
    message = read("message", "")
    if message and status != "success":
        warnings.append(_safe_error(str(message)))
    return status, counts, warnings


def _exit_code_for_status(status: str) -> int:
    return {
        "success": EXIT_SUCCESS,
        "skipped_already_success": EXIT_SKIPPED,
        "partial": EXIT_PARTIAL,
        "preflight_failed": EXIT_PREFLIGHT,
        "pipeline_failed": EXIT_PIPELINE,
        "report_data_invalid": EXIT_SCHEMA,
        "html_render_failed": EXIT_RENDER,
        "lock_conflict": EXIT_LOCK,
    }.get(status, EXIT_PIPELINE)


def _safe_error(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    text = _SENSITIVE_ERROR_RE.sub(lambda match: match.group(0).split("=", 1)[0].split(":", 1)[0] + "=<redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _KEY_RE.sub("<redacted>", text)
    return text[:1000]


def _safe_lock_owner(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value[key]
        for key in ("pid", "run_id", "started_at")
        if key in value and key != "error"
    }


def _unique_strings(values: list[Any]) -> list[str]:
    output: list[str] = []
    for value in values:
        text = _safe_error(value)
        if text and text not in output:
            output.append(text)
    return output


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
