"""Read-only status and report discovery for automated daily runs.

The headless runner writes user-facing HTML under ``report/`` and supporting
date-prefixed artifacts under ``artifacts/``.  This module is the
small read-side seam used by the HTTP API and the desktop UI.  It intentionally
does not create directories, repair pointers, or mutate lock files.

All paths read from JSON are treated as untrusted input.  A pointer can only
resolve to a file below ``runtime_root``; this also protects against symlinks
that lead outside of the runtime directory.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .run_lock import read_run_lock


TIMEZONE = ZoneInfo("Asia/Shanghai")
_DATE_FORMAT = "%Y-%m-%d"


class AutomationStatusReader:
    """Read daily automation state without taking part in orchestration.

    ``runtime_root`` follows :class:`ai_agent.orchestration.DailyRunOrchestrator`:
    explicit argument, ``VC_NEWS_RUNTIME_DIR``/``VC_NEWS_RUNS_DIR``, then
    ``data/runs``.  The public methods intentionally have no write side effects.
    """

    def __init__(self, runtime_root: str | Path | None = None) -> None:
        if runtime_root is None:
            configured_root = os.environ.get("VC_NEWS_RUNTIME_DIR") or os.environ.get("VC_NEWS_RUNS_DIR")
            if configured_root:
                runtime_root = configured_root
            else:
                from .config import DATA_DIR

                runtime_root = DATA_DIR / "runs"
        self.runtime_root = Path(runtime_root).expanduser().resolve(strict=False)

    def get_status(self, target_date: str | date | datetime | None = None) -> dict[str, Any]:
        """Return a JSON-safe snapshot for one target date.

        Missing or malformed files are represented by ``status`` and ``error``
        fields instead of escaping as filesystem/JSON exceptions.  A malformed
        lock is still reported as present/running because it is safer to avoid
        declaring a run idle while a writer may be active.
        """

        normalized_date, date_error = self._normalize_date(target_date)
        if date_error:
            return self._base_status(str(target_date or ""), status="invalid", error=date_error)

        assert normalized_date is not None
        date_token = normalized_date.replace("-", "")
        artifacts_root = self.runtime_root / "artifacts"

        lock = self._read_lock()
        pointer_payload: dict[str, Any] | None = None
        pointer_error: str | None = None
        latest_path = artifacts_root / f"{date_token}-latest.json"
        if latest_path.exists():
            pointer_payload, pointer_error = self._read_json_object(latest_path, "latest pointer")

        manifest_path: Path | None = None
        manifest_payload: dict[str, Any] | None = None
        manifest_error: str | None = None

        if pointer_payload is not None:
            manifest_path, manifest_error = self._manifest_from_pointer(
                pointer_payload, artifacts_root, normalized_date
            )
            if manifest_path is not None:
                manifest_payload, manifest_error = self._read_json_object(manifest_path, "run manifest")

        # A missing/corrupt pointer must not hide a usable run.  This mirrors
        # the orchestrator's defensive fallback while applying stricter path
        # checks to every candidate.
        if manifest_payload is None:
            fallback = self._find_manifest_candidates(artifacts_root, normalized_date)
            for candidate in fallback:
                payload, candidate_error = self._read_json_object(candidate, "run manifest")
                if payload is None:
                    manifest_error = candidate_error or manifest_error
                    continue
                manifest_path = candidate
                manifest_payload = payload
                manifest_error = None
                break

        running_from_manifest = bool(manifest_payload and str(manifest_payload.get("status") or "").lower() == "running")
        running = bool(lock.get("present")) or running_from_manifest
        latest = self._latest_payload(manifest_payload, manifest_path, normalized_date)
        html_path = self._html_path(manifest_payload, manifest_path)
        html_available = html_path is not None

        if running:
            status = "running"
        elif latest is not None:
            status = str(latest.get("status") or "success")
        elif pointer_error or manifest_error:
            status = "corrupt"
        else:
            status = "missing"

        warnings = self._strings((manifest_payload or {}).get("warnings"))
        errors: list[str] = []
        if pointer_error:
            errors.append(pointer_error)
        if manifest_error and manifest_payload is None:
            errors.append(manifest_error)
        if (manifest_payload is not None) and not html_available and status in {"success", "partial"}:
            warnings.append("HTML 日报文件不存在或路径不在 runtime root 内")
        if lock.get("error"):
            warnings.append(str(lock["error"]))
        warnings = list(dict.fromkeys(warnings))

        counts = self._counts((manifest_payload or {}).get("counts"))
        result: dict[str, Any] = self._base_status(normalized_date, status=status, error="; ".join(errors) or None)
        result.update(
            {
                "running": running,
                "lock": lock,
                "latest": latest,
                "latest_status": latest.get("status") if latest else None,
                "latest_run_id": latest.get("run_id") if latest else None,
                "started_at": latest.get("started_at") if latest else None,
                "finished_at": latest.get("finished_at") if latest else None,
                "updated_at": (pointer_payload or {}).get("updated_at"),
                "counts": counts,
                "warnings": warnings,
                "html_available": html_available,
            }
        )
        return result

    def resolve_latest_html(self, target_date: str | date | datetime | None = None) -> Path:
        """Resolve the latest safe HTML artifact.

        ``FileNotFoundError`` means no report is currently available.  A
        ``PermissionError`` means a report pointer attempted to leave the
        runtime root.  Callers can map these exceptions to 404/409/400 as
        appropriate without ever serving an untrusted path.
        """

        normalized_date, date_error = self._normalize_date(target_date)
        if date_error or normalized_date is None:
            raise ValueError(date_error or "invalid target date")
        date_token = normalized_date.replace("-", "")
        artifacts_root = self.runtime_root / "artifacts"

        # Resolve the same manifest selected by get_status, but do not expose
        # internal paths in the API payload.
        pointer_payload, pointer_error = self._read_json_object(
            artifacts_root / f"{date_token}-latest.json", "latest pointer"
        )
        manifest_path: Path | None = None
        manifest_payload: dict[str, Any] | None = None
        traversal_detected = False
        if pointer_payload is not None:
            manifest_path, manifest_error = self._manifest_from_pointer(
                pointer_payload, artifacts_root, normalized_date
            )
            if manifest_error and "runtime root" in manifest_error:
                traversal_detected = True
            if manifest_path is not None:
                manifest_payload, _ = self._read_json_object(manifest_path, "run manifest")

        if manifest_payload is None:
            for candidate in self._find_manifest_candidates(artifacts_root, normalized_date):
                payload, _ = self._read_json_object(candidate, "run manifest")
                if payload is not None:
                    manifest_path, manifest_payload = candidate, payload
                    break
        if manifest_payload is None:
            if traversal_detected:
                raise PermissionError("manifest path is outside runtime root")
            if pointer_error:
                raise FileNotFoundError(pointer_error)
            raise FileNotFoundError(f"no daily report for {normalized_date}")

        html_path = self._html_path(manifest_payload, manifest_path)
        if html_path is None:
            # Distinguish an unsafe artifact path from an absent artifact.
            artifact = (manifest_payload.get("artifacts") or {}).get("html")
            if artifact:
                candidate = self._safe_path_from_value(artifact, manifest_path.parent if manifest_path else self.runtime_root)
                if candidate is None:
                    raise PermissionError("HTML artifact path is outside runtime root")
            raise FileNotFoundError(f"HTML daily report is unavailable for {normalized_date}")
        return html_path

    # ---- read helpers -------------------------------------------------

    def _base_status(self, target_date: str, *, status: str, error: str | None = None) -> dict[str, Any]:
        return {
            "status": status,
            "target_date": target_date,
            "running": False,
            "lock": {"present": False, "status": "missing"},
            "latest": None,
            "latest_status": None,
            "latest_run_id": None,
            "started_at": None,
            "finished_at": None,
            "updated_at": None,
            "counts": {},
            "warnings": [],
            "html_available": False,
            "error": error,
        }

    def _normalize_date(self, value: str | date | datetime | None) -> tuple[str | None, str | None]:
        if value is None or value == "":
            return datetime.now(TIMEZONE).date().strftime(_DATE_FORMAT), None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=TIMEZONE)
            return value.astimezone(TIMEZONE).date().strftime(_DATE_FORMAT), None
        if isinstance(value, date):
            return value.strftime(_DATE_FORMAT), None
        raw = str(value).strip()
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            return None, "target_date must use YYYY-MM-DD"
        if parsed.strftime(_DATE_FORMAT) != raw:
            return None, "target_date must use YYYY-MM-DD"
        return raw, None

    def _safe_join(self, root: Path, *parts: str) -> Path | None:
        try:
            candidate = (root.joinpath(*parts)).resolve(strict=False)
            candidate.relative_to(root)
            return candidate
        except (OSError, ValueError):
            return None

    def _safe_path_from_value(self, value: Any, base: Path) -> Path | None:
        if not isinstance(value, (str, os.PathLike)):
            return None
        raw = Path(value).expanduser()
        candidate = raw if raw.is_absolute() else base / raw
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.runtime_root)
            return resolved
        except (OSError, ValueError):
            return None

    def _read_json_object(self, path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
        safe_path = self._safe_path_from_value(path, self.runtime_root)
        if safe_path is None:
            return None, f"{label} path is outside runtime root"
        path = safe_path
        if not path.exists():
            return None, None
        try:
            if not path.is_file():
                return None, f"{label} is not a file"
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return None, f"{label} is unreadable: {exc}"
        if not isinstance(payload, dict):
            return None, f"{label} must contain a JSON object"
        return payload, None

    def _manifest_from_pointer(
        self, payload: dict[str, Any], runtime_root: Path, target_date: str
    ) -> tuple[Path | None, str | None]:
        if str(payload.get("target_date") or target_date) != target_date:
            return None, "latest pointer target_date does not match request"
        raw = payload.get("manifest_path") or payload.get("manifest")
        if not raw:
            return None, "latest pointer has no manifest_path"
        path = self._safe_path_from_value(raw, runtime_root)
        if path is None:
            return None, "manifest path is outside runtime root"
        return path, None

    def _find_manifest_candidates(self, runtime_root: Path, target_date: str) -> list[Path]:
        if not runtime_root.is_dir():
            return []
        candidates: list[Path] = []
        try:
            date_token = target_date.replace("-", "")
            for path in runtime_root.glob(f"{date_token}-*-run-manifest.json"):
                safe = self._safe_path_from_value(path, runtime_root)
                if safe is None or not safe.is_file():
                    continue
                candidates.append(safe)
            candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        except OSError:
            return []
        return candidates

    def _latest_payload(
        self, payload: dict[str, Any] | None, manifest_path: Path | None, target_date: str
    ) -> dict[str, Any] | None:
        if payload is None:
            return None
        status = str(payload.get("status") or "unknown")
        latest: dict[str, Any] = {
            "status": status,
            "target_date": str(payload.get("target_date") or target_date),
            "run_id": str(payload.get("run_id") or "") or None,
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "stages": payload.get("stages") if isinstance(payload.get("stages"), dict) else {},
            "counts": self._counts(payload.get("counts")),
            "warnings": self._strings(payload.get("warnings")),
            "error": payload.get("error"),
        }
        return latest

    def _html_path(self, payload: dict[str, Any] | None, manifest_path: Path | None) -> Path | None:
        if payload is None:
            return None
        if str(payload.get("status") or "").lower() not in {"success", "partial"}:
            return None
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, dict):
            return None
        path = self._safe_path_from_value(artifacts.get("html"), manifest_path.parent if manifest_path else self.runtime_root)
        if path is None or not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            return None
        return path

    def _read_lock(self) -> dict[str, Any]:
        try:
            result = dict(read_run_lock(self.runtime_root))
        except Exception as exc:  # noqa: BLE001
            return {"present": True, "status": "unreadable", "error": f"lock status is unreadable: {exc}"}
        owner = result.get("owner")
        active = bool(result.get("active"))
        result["present"] = active
        if isinstance(owner, dict):
            result.setdefault("run_id", owner.get("run_id") or owner.get("owner_id"))
            result.setdefault("pid", owner.get("pid"))
            result.setdefault("started_at", owner.get("started_at"))
        return result

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item) for item in value if str(item).strip()]

    @staticmethod
    def _counts(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        counts: dict[str, int] = {}
        for key, item in value.items():
            if isinstance(item, bool):
                continue
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            counts[str(key)] = number
        return counts


__all__ = ["AutomationStatusReader"]
