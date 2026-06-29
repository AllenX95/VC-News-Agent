from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import ARCHIVE_DIR, BACKUP_DIR, DB_PATH, SECRET_KEY_PATH, current_proxy_info, normalize_proxy_mode
from .database import SessionLocal, get_db
from .models import (
    BackupRecord,
    ContentItem,
    DailySummary,
    Entity,
    LLMConfig,
    LLMLog,
    LLMTask,
    Prompt,
    Source,
    SystemSetting,
    TagDefinition,
)
from .security import decrypt_value, encrypt_value, mask_secret
from .seed import LLM_TASKS, ensure_llm_defaults
from .services import (
    BackupService,
    CrawlService,
    DailySummaryService,
    FinancingService,
    LLMService,
    WeeklyCrawlService,
    apply_configured_proxy_settings,
    add_long_log,
    add_session_log,
    archive_content,
    attach_entity,
    attach_tag,
    configured_proxy_settings,
    crawl_progress,
    db_now,
    get_setting,
    query_content,
    set_setting,
)
from .utils import json_loads


router = APIRouter(prefix="/api/v1", tags=["api-v1"])
weekly_crawl_lock = threading.Lock()

SOURCE_CATEGORY_ORDER = {
    "venture_media": 0,
    "ai_media": 1,
    "tech_business_media": 2,
    "official_news": 3,
    "official_research": 4,
    "ai_research_signal": 5,
    "ai_product_signal": 6,
    "startup_directory": 7,
    "github": 8,
    "product_hunt": 9,
    "hacker_news": 10,
}


def _dt(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _source_sort_key(source: Source) -> tuple[int, str]:
    return (SOURCE_CATEGORY_ORDER.get(source.source_category, 99), source.source_name)


def _source_payload(source: Source) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "source_name": source.source_name,
        "source_category": source.source_category,
        "source_url": source.source_url,
        "access_method": source.access_method,
        "priority": source.priority,
        "crawl_frequency": source.crawl_frequency,
        "enabled": source.enabled,
        "include_keywords": source.include_keywords,
        "exclude_keywords": source.exclude_keywords,
        "login_required": source.login_required,
        "paid_required": source.paid_required,
        "requires_js": source.requires_js,
        "crawl_risk": source.crawl_risk,
        "list_page_limit": source.list_page_limit,
        "item_limit_per_run": source.item_limit_per_run,
        "timeout_seconds": source.timeout_seconds,
        "parser_config": source.parser_config,
        "last_success_at": _dt(source.last_success_at),
        "error_count": source.error_count,
        "created_at": _dt(source.created_at),
        "updated_at": _dt(source.updated_at),
    }


def _tag_payload(tag: Any) -> dict[str, Any]:
    return {
        "tag_id": getattr(tag, "tag_id", None),
        "tag_key": tag.tag_key,
        "tag_value": tag.tag_value,
        "display_name_cn": getattr(tag, "display_name_cn", None),
        "display_name_en": getattr(tag, "display_name_en", None),
        "aliases": getattr(tag, "aliases", None),
        "enabled": getattr(tag, "enabled", True),
        "confidence": getattr(tag, "confidence", None),
        "source": getattr(tag, "source", None),
    }


def _entity_payload(entity: Entity) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "entity_type": entity.entity_type,
        "canonical_name": entity.canonical_name,
        "display_name": entity.display_name,
        "aliases": entity.aliases,
        "enabled": entity.enabled,
        "created_at": _dt(entity.created_at),
        "updated_at": _dt(entity.updated_at),
    }


def _content_list_payload(item: ContentItem) -> dict[str, Any]:
    display_time = item.publish_time or item.crawl_time
    return {
        "content_id": item.content_id,
        "source_id": item.source_id,
        "title": item.title,
        "url": item.url,
        "canonical_url": item.canonical_url,
        "source_name": item.source_name,
        "source_category": item.source_category,
        "publish_time": _dt(item.publish_time),
        "crawl_time": _dt(item.crawl_time),
        "display_time": _dt(display_time),
        "publish_time_status": item.publish_time_status,
        "summary": item.summary,
        "language": item.language,
        "word_count": item.word_count,
        "extraction_status": item.extraction_status,
        "llm_status": item.llm_status,
        "ai_related": item.ai_related,
        "full_content_cached": item.full_content_cached,
        "full_content_saved": item.full_content_saved,
        "archive_object_path": item.archive_object_path,
        "is_favorite": item.is_favorite,
        "favorited_at": _dt(item.favorited_at),
        "created_at": _dt(item.created_at),
        "updated_at": _dt(item.updated_at),
        "tags": [_tag_payload(tag) for tag in item.tags],
        "entities": [
            {
                **_entity_payload(rel.entity),
                "confidence": rel.confidence,
                "source": rel.source,
                "content_entity_id": rel.content_entity_id,
            }
            for rel in item.entities
        ],
    }


def _content_detail_payload(item: ContentItem) -> dict[str, Any]:
    payload = _content_list_payload(item)
    payload["cache"] = (
        {
            "content_id": item.cache.content_id,
            "clean_text": item.cache.clean_text,
            "cached_at": _dt(item.cache.cached_at),
            "expire_at": _dt(item.cache.expire_at),
        }
        if item.cache
        else None
    )
    return payload


def _summary_payload(summary: DailySummary, include_body: bool = False) -> dict[str, Any]:
    payload = {
        "summary_id": summary.summary_id,
        "summary_date": summary.summary_date,
        "generated_at": _dt(summary.generated_at),
        "total_items": summary.total_items,
        "successful_items": summary.successful_items,
        "partial_items": summary.partial_items,
        "failed_items": summary.failed_items,
        "source_counts": json_loads(summary.source_counts_json, {}),
        "llm_summary_status": summary.llm_summary_status,
        "llm_summary_text": summary.llm_summary_text,
    }
    if include_body:
        payload["sections"] = json_loads(summary.sections_json, {})
        payload["markdown_text"] = summary.markdown_text or ""
    return payload


def _backup_payload(backup: BackupRecord) -> dict[str, Any]:
    return {
        "backup_id": backup.backup_id,
        "backup_type": backup.backup_type,
        "backup_path": backup.backup_path,
        "status": backup.status,
        "integrity_status": backup.integrity_status,
        "message": backup.message,
        "created_at": _dt(backup.created_at),
    }


def _prompt_payload(prompt: Prompt) -> dict[str, Any]:
    return {
        "prompt_id": prompt.prompt_id,
        "prompt_name": prompt.prompt_name,
        "task_name": prompt.task_name,
        "prompt_text": prompt.prompt_text,
        "enabled": prompt.enabled,
        "created_at": _dt(prompt.created_at),
        "updated_at": _dt(prompt.updated_at),
    }


LLM_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai_compatible": "openai",
    "openai-compatible": "openai",
    "openai compatible": "openai",
    "anthropic": "anthropic",
}


def _normalize_llm_provider(value: Any) -> str:
    provider = str(value or "openai").strip().lower()
    return LLM_PROVIDER_ALIASES.get(provider, provider)


def _normalize_legacy_llm_providers(db: Session) -> bool:
    changed = False
    configs = db.scalars(select(LLMConfig)).all()
    for config in configs:
        normalized = _normalize_llm_provider(config.provider_type)
        if normalized != config.provider_type:
            config.provider_type = normalized
            changed = True
    return changed


def _llm_config_payload(config: LLMConfig) -> dict[str, Any]:
    api_key = decrypt_value(config.encrypted_api_key)
    return {
        "llm_config_id": config.llm_config_id,
        "config_name": config.config_name,
        "provider_type": _normalize_llm_provider(config.provider_type),
        "base_url": decrypt_value(config.encrypted_base_url),
        "model_name": decrypt_value(config.encrypted_model_name),
        "api_key_masked": mask_secret(api_key),
        "enabled": config.enabled,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "context_window_tokens": config.context_window_tokens,
        "created_at": _dt(config.created_at),
        "updated_at": _dt(config.updated_at),
    }


def _llm_log_payload(log: LLMLog) -> dict[str, Any]:
    return {
        "llm_log_id": log.llm_log_id,
        "task_name": log.task_name,
        "llm_config_id": log.llm_config_id,
        "prompt_id": log.prompt_id,
        "content_id": log.content_id,
        "model_name": log.model_name,
        "latency_ms": log.latency_ms,
        "status": log.status,
        "error_message": log.error_message,
        "created_at": _dt(log.created_at),
    }


def _settings_payload(db: Session) -> dict[str, str]:
    return {row.setting_key: row.setting_value for row in db.scalars(select(SystemSetting)).all()}


def _parse_date_string(value: str) -> str:
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid date, expected YYYY-MM-DD") from None


def _parse_query_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Date must use YYYY-MM-DD format") from exc


def _run_crawl_background(run_timestamp: datetime | None = None) -> None:
    run_timestamp = run_timestamp or db_now()
    with SessionLocal() as db:
        CrawlService().run_all_sources(db, manual=True, run_timestamp=run_timestamp)


def _run_weekly_crawl_background(date_strings: list[str]) -> None:
    if not weekly_crawl_lock.acquire(blocking=False):
        return
    try:
        for date_str in date_strings:
            target_date = datetime.fromisoformat(date_str).date()
            run_timestamp = datetime.combine(target_date, dt_time(hour=12))
            try:
                with SessionLocal() as db:
                    add_long_log(db, "weekly_crawl_date_start", f"开始补抓 {date_str}")
                    add_session_log(db, "weekly_crawl_date_start", f"开始补抓 {date_str}")
                    db.commit()
                    CrawlService().run_all_sources(db, manual=True, run_timestamp=run_timestamp)
            except Exception as exc:  # noqa: BLE001
                with SessionLocal() as db:
                    add_long_log(db, "weekly_crawl_date_failed", f"{date_str} 补抓失败：{exc}", "error")
                    add_session_log(db, "weekly_crawl_date_failed", f"{date_str} 补抓失败：{exc}", "error")
                    db.commit()
    finally:
        weekly_crawl_lock.release()


def _prepare_manual_crawl_progress() -> datetime:
    run_timestamp = db_now()
    with SessionLocal() as db:
        total_sources = db.scalar(select(func.count(Source.source_id)).where(Source.enabled.is_(True))) or 0
    crawl_progress.start(total_sources, "Manual crawl queued", run_timestamp)
    return run_timestamp


@router.get("/health")
def health() -> dict[str, Any]:
    integrity = "missing"
    journal_mode = ""
    if DB_PATH.exists():
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
    return {
        "ok": integrity == "ok",
        "app_id": "ai-investment-agent",
        "name": "AI Investment Intelligence Agent",
        "backend": "fastapi",
        "data": {
            "db_path": str(DB_PATH),
            "db_exists": DB_PATH.exists(),
            "db_size": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
            "secret_key_path": str(SECRET_KEY_PATH),
            "secret_key_exists": SECRET_KEY_PATH.exists(),
            "backup_dir": str(BACKUP_DIR),
            "archive_dir": str(ARCHIVE_DIR),
            "integrity": integrity,
            "journal_mode": journal_mode,
        },
    }


@router.get("/app-info")
def app_info() -> dict[str, str]:
    return {"app_id": "ai-investment-agent", "name": "AI Investment Intelligence Agent", "api_version": "v1"}


@router.get("/dashboard")
def dashboard(
    request: Request,
    selected_date: str | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current_date = db_now().date()
    if selected_date:
        try:
            dashboard_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Date must use YYYY-MM-DD format") from exc
    else:
        dashboard_date = current_date

    selected_date = dashboard_date.isoformat()
    day_start = datetime.combine(dashboard_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    total_contents = db.scalar(select(func.count(ContentItem.content_id))) or 0
    today_contents = (
        db.scalar(select(func.count(ContentItem.content_id)).where(ContentItem.crawl_time >= day_start, ContentItem.crawl_time < day_end))
        or 0
    )
    enabled_sources = db.scalar(select(func.count(Source.source_id)).where(Source.enabled.is_(True))) or 0
    failed_sources = db.scalar(select(func.count(Source.source_id)).where(Source.error_count > 0)) or 0
    last_summary = db.scalar(select(DailySummary).order_by(DailySummary.generated_at.desc()))
    sources = sorted(db.scalars(select(Source).where(Source.enabled.is_(True))).all(), key=_source_sort_key)

    source_groups = []
    page_size = 10
    for source in sources:
        page_param = f"source_{source.source_id}_page"
        current_page = _int(request.query_params.get(page_param), 1, 1)
        total_count = (
            db.scalar(
                select(func.count(ContentItem.content_id)).where(
                    ContentItem.source_id == source.source_id,
                    ContentItem.crawl_time >= day_start,
                    ContentItem.crawl_time < day_end,
                )
            )
            or 0
        )
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        current_page = min(current_page, total_pages)
        offset = (current_page - 1) * page_size
        items = db.scalars(
            select(ContentItem)
            .where(
                ContentItem.source_id == source.source_id,
                ContentItem.crawl_time >= day_start,
                ContentItem.crawl_time < day_end,
            )
            .order_by(func.coalesce(ContentItem.publish_time, ContentItem.crawl_time).desc(), ContentItem.content_id.desc())
            .offset(offset)
            .limit(page_size)
        ).all()
        source_groups.append(
            {
                "source": _source_payload(source),
                "content_items": [_content_list_payload(item) for item in items],
                "total_count": total_count,
                "page": current_page,
                "per_page": page_size,
                "total_pages": total_pages,
                "page_offset": offset,
            }
        )

    return {
        "today": selected_date,
        "selected_date": selected_date,
        "current_date": current_date.isoformat(),
        "total_contents": total_contents,
        "today_contents": today_contents,
        "enabled_sources": enabled_sources,
        "failed_sources": failed_sources,
        "last_summary": _summary_payload(last_summary) if last_summary else None,
        "last_auto_crawl_date": get_setting(db, "last_auto_crawl_date", ""),
        "daily_crawl_time": get_setting(db, "daily_crawl_time", "10:00"),
        "source_window_label": "今日内容" if dashboard_date == current_date else f"{selected_date} 内容",
        "source_groups": source_groups,
    }


@router.post("/crawl/run")
def run_crawl(background_tasks: BackgroundTasks) -> dict[str, Any]:
    if crawl_progress.is_running():
        return {"ok": False, "message": "Crawl is already running", "progress": crawl_progress.snapshot()}
    run_timestamp = _prepare_manual_crawl_progress()
    background_tasks.add_task(_run_crawl_background, run_timestamp)
    return {
        "ok": True,
        "message": "Crawl started",
        "run_timestamp": run_timestamp.isoformat(timespec="seconds"),
        "progress": crawl_progress.snapshot(),
    }


@router.get("/crawl/status")
def crawl_status() -> dict[str, Any]:
    return crawl_progress.snapshot()


@router.get("/crawl/week-status")
def crawl_week_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return WeeklyCrawlService().status(db)


@router.post("/crawl/week-backfill")
def crawl_week_backfill(background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> dict[str, Any]:
    if weekly_crawl_lock.locked():
        return {"ok": False, "message": "本周补抓正在运行", "progress": crawl_progress.snapshot()}
    if crawl_progress.is_running():
        return {"ok": False, "message": "抓取任务正在运行", "progress": crawl_progress.snapshot()}

    status = WeeklyCrawlService().status(db)
    missing_dates = list(status.get("missing_dates") or [])
    if not missing_dates:
        return {"ok": True, "message": "本周从周一到今天均已有抓取记录", "week": status}

    total_sources = db.scalar(select(func.count(Source.source_id)).where(Source.enabled.is_(True))) or 0
    crawl_progress.start(total_sources * len(missing_dates), f"本周补抓已排队：{len(missing_dates)} 天", db_now())
    background_tasks.add_task(_run_weekly_crawl_background, missing_dates)
    return {
        "ok": True,
        "message": f"本周补抓已开始：{', '.join(missing_dates)}",
        "missing_dates": missing_dates,
        "progress": crawl_progress.snapshot(),
    }


@router.get("/financing")
def financing(
    selected_date: str | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    target_date = _parse_query_date(selected_date)
    service = FinancingService()
    financing_items, financing_window_label, window_date = service.query_items(db, limit=100, target_date=target_date)
    counts = service.counts(db, target_date=target_date)
    return {
        "financing_items": financing_items,
        "financing_window_label": financing_window_label,
        "selected_date": window_date,
        "current_date": db_now().date().isoformat(),
        "selected_count": counts["selected_count"],
        "today_count": counts["today_count"],
        "total_count": counts["total_count"],
        "available_dates": service.available_dates(db),
        "weekly_report_dir": service.report_location(db),
    }


@router.post("/financing/exclude")
def exclude_financing(
    data: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = data or {}
    raw_ids = payload.get("content_ids")
    if raw_ids is None:
        raw_ids = [payload.get("content_id")]
    elif not isinstance(raw_ids, list):
        raw_ids = [raw_ids]

    content_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            content_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if content_id > 0:
            content_ids.append(content_id)

    if not content_ids:
        raise HTTPException(status_code=400, detail="content_ids is required")

    result = FinancingService().exclude_content_ids(db, content_ids)
    if result["excluded"] == 0:
        raise HTTPException(status_code=404, detail="Content not found")
    return {"ok": True, **result}


@router.post("/financing/identify-this-week")
def identify_this_week_financing(
    data: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    limit = _int((data or {}).get("limit"), 300, 1, 1000)
    result = FinancingService().identify_this_week(db, limit=limit)
    return {"ok": True, "result": result}


@router.post("/financing/weekly-report")
def generate_weekly_financing_report(
    data: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    output_location = str((data or {}).get("output_dir") or (data or {}).get("output_path") or "").strip()
    try:
        result = FinancingService().generate_previous_week_report(db, output_location or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "report": result}


@router.post("/financing/current-week-report")
def generate_current_week_financing_report(
    data: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    output_location = str((data or {}).get("output_dir") or (data or {}).get("output_path") or "").strip()
    try:
        result = FinancingService().generate_current_week_report(db, output_location or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "report": result}


@router.post("/system/select-directory")
def select_local_directory(data: dict[str, Any] | None = Body(default=None)) -> dict[str, str]:
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Folder selection is currently supported on Windows only")

    initial_path = str((data or {}).get("initial_path") or "").strip()
    env = os.environ.copy()
    env["VC_NEWS_INITIAL_DIR"] = initial_path
    env["VC_NEWS_DIALOG_TITLE"] = "选择周报保存文件夹"
    script = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:VC_NEWS_DIALOG_TITLE
$dialog.ShowNewFolderButton = $true
if ($env:VC_NEWS_INITIAL_DIR -and (Test-Path -LiteralPath $env:VC_NEWS_INITIAL_DIR -PathType Container)) {
    $dialog.SelectedPath = $env:VC_NEWS_INITIAL_DIR
}
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Output $dialog.SelectedPath
    }
} finally {
    $dialog.Dispose()
}
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Cannot open folder selector: {error}") from error

    if result.returncode != 0:
        detail = result.stderr.strip() or "Folder selector failed"
        raise HTTPException(status_code=500, detail=detail)

    selected_path = next((line.strip() for line in reversed(result.stdout.splitlines()) if line.strip()), "")
    return {"path": selected_path}


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)) -> dict[str, Any]:
    sources = db.scalars(select(Source).order_by(Source.source_category, Source.source_id)).all()
    return {"sources": [_source_payload(source) for source in sources]}


@router.post("/sources")
def create_source(data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    source = Source(
        source_name=str(data.get("source_name") or "").strip(),
        source_category=str(data.get("source_category") or "ai_media").strip(),
        source_url=str(data.get("source_url") or "").strip(),
        access_method=str(data.get("access_method") or "auto").strip(),
        priority=str(data.get("priority") or "P0").strip(),
        enabled=_bool(data.get("enabled"), True),
        requires_js=_bool(data.get("requires_js"), False),
        crawl_risk=str(data.get("crawl_risk") or "low").strip(),
        list_page_limit=_int(data.get("list_page_limit"), 50, 1),
        item_limit_per_run=_int(data.get("item_limit_per_run"), 20, 1),
        timeout_seconds=_int(data.get("timeout_seconds"), 25, 1),
    )
    if not source.source_name or not source.source_url:
        raise HTTPException(400, "source_name and source_url are required")
    db.add(source)
    add_long_log(db, "source_created", f"Created source: {source.source_name}")
    db.commit()
    db.refresh(source)
    return {"ok": True, "source": _source_payload(source)}


@router.get("/sources/{source_id}")
def get_source(source_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    return {"source": _source_payload(source)}


@router.patch("/sources/{source_id}")
def update_source(source_id: int, data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    for key in ("source_name", "source_category", "source_url", "access_method", "priority", "crawl_risk"):
        if key in data:
            setattr(source, key, str(data.get(key) or "").strip())
    for key in ("enabled", "requires_js"):
        if key in data:
            setattr(source, key, _bool(data.get(key), getattr(source, key)))
    for key, default in (("list_page_limit", 50), ("item_limit_per_run", 20), ("timeout_seconds", 25)):
        if key in data:
            setattr(source, key, _int(data.get(key), default, 1))
    add_long_log(db, "source_updated", f"Updated source: {source.source_name}")
    db.commit()
    db.refresh(source)
    return {"ok": True, "source": _source_payload(source)}


@router.post("/sources/{source_id}/toggle")
def toggle_source(source_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    source.enabled = not source.enabled
    db.commit()
    return {"ok": True, "source": _source_payload(source)}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    content_count = db.scalar(select(func.count(ContentItem.content_id)).where(ContentItem.source_id == source_id)) or 0
    if content_count:
        source.enabled = False
        action = "disabled"
        add_long_log(db, "source_disabled_instead_delete", f"Disabled source with history: {source.source_name}")
    else:
        action = "deleted"
        add_long_log(db, "source_deleted", f"Deleted source: {source.source_name}")
        db.delete(source)
    db.commit()
    return {"ok": True, "action": action}


@router.post("/sources/{source_id}/crawl")
def crawl_source(source_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    run = CrawlService().run_source(db, source)
    return {
        "ok": run.status == "success",
        "run": {
            "crawl_run_id": run.crawl_run_id,
            "status": run.status,
            "total_items": run.total_items,
            "new_items": run.new_items,
            "failed_items": run.failed_items,
            "message": run.message,
        },
    }


@router.get("/content")
def list_content(
    q: str = "",
    source_id: int | None = None,
    status: str = "",
    favorite: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sources = sorted(db.scalars(select(Source)).all(), key=_source_sort_key)
    contents = query_content(db, q=q, source_id=source_id, status=status, favorite=favorite)
    return {
        "contents": [_content_list_payload(item) for item in contents],
        "sources": [_source_payload(source) for source in sources],
        "filters": {"q": q, "source_id": source_id, "status": status, "favorite": favorite},
    }


@router.get("/content/{content_id}")
def get_content(content_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    content = db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    return {"content": _content_detail_payload(content)}


@router.patch("/content/{content_id}")
def update_content(content_id: int, data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    content = db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    if "title" in data:
        content.title = str(data.get("title") or "")
    if "summary" in data:
        content.summary = str(data.get("summary") or "")
    if "extraction_status" in data:
        content.extraction_status = str(data.get("extraction_status") or "processed")
    db.commit()
    db.refresh(content)
    return {"ok": True, "content": _content_detail_payload(content)}


@router.post("/content/{content_id}/favorite")
def favorite_content(content_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    content = db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    content.is_favorite = not content.is_favorite
    content.favorited_at = db_now() if content.is_favorite else None
    db.commit()
    return {"ok": True, "is_favorite": content.is_favorite}


@router.post("/content/{content_id}/archive")
def archive_content_api(content_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    content = db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    archive_content(db, content)
    db.commit()
    return {"ok": True, "archive_object_path": content.archive_object_path}


@router.post("/content/{content_id}/tags")
def add_tag_to_content(content_id: int, data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    content = db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    attach_tag(db, content, str(data.get("tag_key") or ""), str(data.get("tag_value") or ""), "manual", 1.0)
    db.commit()
    return {"ok": True, "content": _content_detail_payload(content)}


@router.post("/content/{content_id}/entities")
def add_entity_to_content(content_id: int, data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    content = db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    attach_entity(db, content, str(data.get("entity_type") or ""), str(data.get("entity_name") or ""), "manual", 1.0)
    db.commit()
    return {"ok": True, "content": _content_detail_payload(content)}


@router.post("/content/{content_id}/recrawl")
def recrawl_content_source(content_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    content = db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(404, "Content not found")
    run = CrawlService().run_source(db, content.source)
    return {"ok": run.status == "success", "message": run.message}


@router.get("/summaries")
def list_summaries(db: Session = Depends(get_db)) -> dict[str, Any]:
    summaries = db.scalars(select(DailySummary).order_by(DailySummary.summary_date.desc())).all()
    return {"summaries": [_summary_payload(summary) for summary in summaries]}


@router.post("/summaries/generate")
def generate_summary(data: dict[str, Any] | None = Body(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    target_date = None
    if data:
        raw_date = str(data.get("summary_date") or data.get("date") or "").strip()
        target_date = _parse_date_string(raw_date) if raw_date else None
    summary = DailySummaryService().generate(db, target_date=target_date)
    return {"ok": True, "summary": _summary_payload(summary, include_body=True)}


@router.get("/summaries/{summary_date}")
def get_summary(summary_date: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    summary = db.scalar(select(DailySummary).where(DailySummary.summary_date == summary_date))
    if not summary:
        raise HTTPException(404, "Summary not found")
    return {"summary": _summary_payload(summary, include_body=True)}


@router.get("/summaries/{summary_date}/markdown", response_class=PlainTextResponse)
def get_summary_markdown(summary_date: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    summary = db.scalar(select(DailySummary).where(DailySummary.summary_date == summary_date))
    if not summary:
        raise HTTPException(404, "Summary not found")
    return PlainTextResponse(summary.markdown_text or "", media_type="text/markdown; charset=utf-8")


@router.get("/taxonomy")
def taxonomy(limit_entities: int = 300, db: Session = Depends(get_db)) -> dict[str, Any]:
    limit_entities = _int(limit_entities, 300, 1, 2000)
    tags = db.scalars(select(TagDefinition).order_by(TagDefinition.tag_key, TagDefinition.tag_value)).all()
    entities = db.scalars(select(Entity).order_by(Entity.entity_type, Entity.canonical_name).limit(limit_entities)).all()
    return {"tags": [_tag_payload(tag) for tag in tags], "entities": [_entity_payload(entity) for entity in entities]}


@router.post("/taxonomy/tags")
def create_taxonomy_tag(data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    tag = TagDefinition(
        tag_key=str(data.get("tag_key") or "").strip(),
        tag_value=str(data.get("tag_value") or "").strip(),
        display_name_cn=str(data.get("display_name_cn") or data.get("tag_value") or "").strip(),
        display_name_en=str(data.get("display_name_en") or data.get("tag_value") or "").strip(),
        enabled=_bool(data.get("enabled"), True),
    )
    if not tag.tag_key or not tag.tag_value:
        raise HTTPException(400, "tag_key and tag_value are required")
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"ok": True, "tag": _tag_payload(tag)}


@router.post("/taxonomy/tags/{tag_id}/toggle")
def toggle_taxonomy_tag(tag_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    tag = db.get(TagDefinition, tag_id)
    if not tag:
        raise HTTPException(404, "Tag not found")
    tag.enabled = not tag.enabled
    db.commit()
    return {"ok": True, "tag": _tag_payload(tag)}


@router.patch("/taxonomy/entities/{entity_id}")
def update_entity(entity_id: int, data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    entity = db.get(Entity, entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    if "canonical_name" in data:
        entity.canonical_name = str(data.get("canonical_name") or "").strip()
        entity.display_name = entity.canonical_name
    if "entity_type" in data:
        entity.entity_type = str(data.get("entity_type") or "").strip()
    if "aliases" in data:
        entity.aliases = str(data.get("aliases") or "").strip()
    db.commit()
    db.refresh(entity)
    return {"ok": True, "entity": _entity_payload(entity)}


@router.get("/llm")
def llm(db: Session = Depends(get_db)) -> dict[str, Any]:
    changed = ensure_llm_defaults(db)
    changed = _normalize_legacy_llm_providers(db) or changed
    if changed:
        db.commit()
    CrawlService().mark_stale_not_configured_as_pending(db)
    configs = db.scalars(select(LLMConfig).order_by(LLMConfig.llm_config_id)).all()
    prompts = db.scalars(select(Prompt).order_by(Prompt.prompt_id)).all()
    tasks = {task.task_name: task for task in db.scalars(select(LLMTask)).all()}
    llm_status_counts = {
        status or "unknown": count
        for status, count in db.execute(
            select(ContentItem.llm_status, func.count(ContentItem.content_id)).group_by(ContentItem.llm_status)
        ).all()
    }
    task_rows = []
    for task_name, display_name, description in LLM_TASKS:
        task = tasks.get(task_name)
        task_rows.append(
            {
                "task": {
                    "llm_task_id": task.llm_task_id,
                    "task_name": task.task_name,
                    "llm_config_id": task.llm_config_id,
                    "prompt_id": task.prompt_id,
                    "enabled": task.enabled,
                }
                if task
                else None,
                "task_name": task_name,
                "display_name": display_name,
                "description": description,
            }
        )
    recent_connection_tests = db.scalars(
        select(LLMLog).where(LLMLog.task_name == "connection_test").order_by(LLMLog.created_at.desc()).limit(5)
    ).all()
    return {
        "configs": [_llm_config_payload(config) for config in configs],
        "prompts": [_prompt_payload(prompt) for prompt in prompts],
        "task_rows": task_rows,
        "llm_status_counts": llm_status_counts,
        "llm_pending_count": llm_status_counts.get("not_configured", 0) + llm_status_counts.get("pending", 0),
        "recent_connection_tests": [_llm_log_payload(log) for log in recent_connection_tests],
    }


@router.post("/llm/configs")
def create_llm_config(data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    provider_type = _normalize_llm_provider(data.get("provider_type"))
    if provider_type not in {"openai", "anthropic"}:
        raise HTTPException(400, "provider_type must be openai or anthropic")
    config = LLMConfig(
        config_name=str(data.get("config_name") or "").strip(),
        provider_type=provider_type,
        encrypted_base_url=encrypt_value(str(data.get("base_url") or "").strip()),
        encrypted_api_key=encrypt_value(str(data.get("api_key") or "").strip()),
        encrypted_model_name=encrypt_value(str(data.get("model_name") or "").strip()),
        enabled=_bool(data.get("enabled"), True),
        timeout_seconds=_int(data.get("timeout_seconds"), 60, 1),
        max_retries=_int(data.get("max_retries"), 1, 0),
        context_window_tokens=_int(data.get("context_window_tokens"), 1_000_000, 1),
    )
    if not config.config_name:
        raise HTTPException(400, "config_name is required")
    db.add(config)
    db.commit()
    db.refresh(config)
    CrawlService().mark_stale_not_configured_as_pending(db)
    return {"ok": True, "config": _llm_config_payload(config)}


@router.patch("/llm/configs/{config_id}")
def update_llm_config(config_id: int, data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    config = db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(404, "LLM config not found")
    if "config_name" in data:
        config.config_name = str(data.get("config_name") or "").strip()
    if "provider_type" in data:
        provider_type = _normalize_llm_provider(data.get("provider_type"))
        if provider_type not in {"openai", "anthropic"}:
            raise HTTPException(400, "provider_type must be openai or anthropic")
        config.provider_type = provider_type
    if "base_url" in data:
        config.encrypted_base_url = encrypt_value(str(data.get("base_url") or "").strip())
    if "model_name" in data:
        config.encrypted_model_name = encrypt_value(str(data.get("model_name") or "").strip())
    if "api_key" in data:
        api_key = str(data.get("api_key") or "").strip()
        if api_key:
            config.encrypted_api_key = encrypt_value(api_key)
    if "enabled" in data:
        config.enabled = _bool(data.get("enabled"), config.enabled)
    if "timeout_seconds" in data:
        config.timeout_seconds = _int(data.get("timeout_seconds"), config.timeout_seconds or 60, 1)
    if "max_retries" in data:
        config.max_retries = _int(data.get("max_retries"), config.max_retries or 1, 0)
    if "context_window_tokens" in data:
        config.context_window_tokens = _int(data.get("context_window_tokens"), config.context_window_tokens or 1_000_000, 1)
    if not config.config_name:
        raise HTTPException(400, "config_name is required")
    db.commit()
    db.refresh(config)
    CrawlService().mark_stale_not_configured_as_pending(db)
    return {"ok": True, "config": _llm_config_payload(config)}


@router.delete("/llm/configs/{config_id}")
def delete_llm_config(config_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    config = db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(404, "LLM config not found")
    for task in db.scalars(select(LLMTask).where(LLMTask.llm_config_id == config_id)).all():
        task.llm_config_id = None
    log_count = db.scalar(select(func.count(LLMLog.llm_log_id)).where(LLMLog.llm_config_id == config_id)) or 0
    if log_count:
        config.enabled = False
        action = "disabled"
        add_long_log(db, "llm_config_disabled_instead_delete", f"Disabled LLM config with logs: {config.config_name}")
    else:
        action = "deleted"
        add_long_log(db, "llm_config_deleted", f"Deleted LLM config: {config.config_name}")
        db.delete(config)
    db.commit()
    CrawlService().mark_stale_not_configured_as_pending(db)
    return {"ok": True, "action": action}


@router.post("/llm/configs/{config_id}/test")
def test_llm_config(config_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    config = db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(404, "LLM config not found")
    result = LLMService().test_config(db, config)
    return {"ok": bool(result.get("ok")), "result": result}


@router.post("/llm/prompts")
def create_prompt(data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    prompt = Prompt(
        prompt_name=str(data.get("prompt_name") or "").strip(),
        task_name=str(data.get("task_name") or "process_content_metadata").strip(),
        prompt_text=str(data.get("prompt_text") or ""),
        enabled=_bool(data.get("enabled"), True),
    )
    if not prompt.prompt_name or not prompt.prompt_text:
        raise HTTPException(400, "prompt_name and prompt_text are required")
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    CrawlService().mark_stale_not_configured_as_pending(db)
    return {"ok": True, "prompt": _prompt_payload(prompt)}


@router.patch("/llm/prompts/{prompt_id}")
def update_prompt(
    prompt_id: int,
    data: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    prompt = db.get(Prompt, prompt_id)
    if not prompt:
        raise HTTPException(404, "Prompt not found")
    if "prompt_name" in data:
        prompt.prompt_name = str(data.get("prompt_name") or "").strip()
    if "task_name" in data:
        prompt.task_name = str(data.get("task_name") or "").strip()
    if "prompt_text" in data:
        prompt.prompt_text = str(data.get("prompt_text") or "")
    if "enabled" in data:
        prompt.enabled = _bool(data.get("enabled"), prompt.enabled)
    if not prompt.prompt_name or not prompt.task_name or not prompt.prompt_text:
        raise HTTPException(400, "prompt_name, task_name and prompt_text are required")
    db.commit()
    db.refresh(prompt)
    return {"ok": True, "prompt": _prompt_payload(prompt)}


@router.post("/llm/reprocess-not-configured")
def reprocess_not_configured(data: dict[str, Any] | None = Body(default=None), db: Session = Depends(get_db)) -> dict[str, Any]:
    data = data or {}
    limit = _int(data.get("limit"), 20, 1, 100)
    result = CrawlService().reprocess_not_configured_content(db, limit=limit)
    return {"ok": True, "result": result}


@router.patch("/llm/tasks/bulk")
def update_llm_tasks_bulk(data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = data.get("tasks") or []
    updated = 0
    for row in rows:
        task_id = _int(row.get("llm_task_id"), 0)
        task = db.get(LLMTask, task_id)
        if not task:
            continue
        task.llm_config_id = _int(row.get("llm_config_id"), 0) or None
        task.prompt_id = _int(row.get("prompt_id"), 0) or None
        task.enabled = _bool(row.get("enabled"), task.enabled)
        updated += 1
    db.commit()
    CrawlService().mark_stale_not_configured_as_pending(db)
    return {"ok": True, "updated": updated}


@router.get("/settings")
def settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    backups = db.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(20)).all()
    proxy_settings = configured_proxy_settings(db)
    return {
        "settings": _settings_payload(db),
        "backups": [_backup_payload(backup) for backup in backups],
        "proxy_settings": proxy_settings,
        "proxy_info": current_proxy_info(proxy_settings["mode"], "current"),
    }


@router.patch("/settings")
def update_settings(data: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    if "daily_crawl_time" in data:
        set_setting(db, "daily_crawl_time", str(data.get("daily_crawl_time") or "10:00"))
    if "content_cache_hours" in data:
        set_setting(db, "content_cache_hours", str(_int(data.get("content_cache_hours"), 48, 1)))
    if "daily_summary_use_llm" in data:
        set_setting(db, "daily_summary_use_llm", "true" if _bool(data.get("daily_summary_use_llm")) else "false")
    if "daily_summary_max_items" in data:
        set_setting(db, "daily_summary_max_items", str(_int(data.get("daily_summary_max_items"), 50, 1)))
    if "source_parallelism" in data:
        set_setting(db, "source_parallelism", str(_int(data.get("source_parallelism"), 4, 1, 12)))
    if "llm_parallelism" in data:
        set_setting(db, "llm_parallelism", str(_int(data.get("llm_parallelism"), 2, 1, 6)))
    if "network_proxy_mode" in data:
        proxy_mode = normalize_proxy_mode(str(data.get("network_proxy_mode") or "off"))
        if proxy_mode not in {"off", "system", "custom"}:
            raise HTTPException(400, "network_proxy_mode must be off, system or custom")
        set_setting(db, "network_proxy_mode", proxy_mode)
    if "network_proxy_url" in data:
        set_setting(db, "network_proxy_url", str(data.get("network_proxy_url") or "").strip())
    if "network_proxy_no_proxy" in data:
        set_setting(db, "network_proxy_no_proxy", str(data.get("network_proxy_no_proxy") or "").strip())
    db.flush()
    proxy_settings = configured_proxy_settings(db)
    if proxy_settings["mode"] == "custom" and not proxy_settings["url"]:
        raise HTTPException(400, "network_proxy_url is required when custom proxy is enabled")
    db.commit()
    proxy_info = apply_configured_proxy_settings(db)

    from .services import app_scheduler

    app_scheduler.refresh()
    return {"ok": True, "settings": _settings_payload(db), "proxy_info": proxy_info}


@router.post("/settings/backup")
def create_backup(db: Session = Depends(get_db)) -> dict[str, Any]:
    backup = BackupService().create_backup(db, "manual")
    return {"ok": backup.status == "success", "backup": _backup_payload(backup)}
