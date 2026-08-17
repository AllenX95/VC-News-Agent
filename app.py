from __future__ import annotations

import argparse
import os
import secrets
import threading
import time

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ai_agent.api_v1 import router as api_v1_router
from ai_agent.config import scheduler_mode_info
from ai_agent.database import SessionLocal, create_db
from ai_agent.run_lock import RunLock, run_lock_path
from ai_agent.seed import seed_all
from ai_agent.services import (
    CrawlService,
    add_session_log,
    app_scheduler,
    apply_configured_proxy_settings,
    clear_session_logs,
)


app = FastAPI(title="AI 投资情报 Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://tauri.localhost",
        "tauri://localhost",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunLockWriteGuardMiddleware(BaseHTTPMiddleware):
    """Reject conflicting GUI writes while a shared daily run is active.

    Every POST, PATCH, and DELETE under ``/api/v1`` atomically acquires the
    same cross-process lock used by Headless. Synchronous writes release it
    when the response is produced; background crawl routes explicitly transfer
    ownership to their background task. GET/HEAD and ``/shutdown`` remain
    available while a run is active.
    """

    @staticmethod
    def _is_guarded_write(request: Request) -> bool:
        path = request.url.path
        method = request.method.upper()
        is_api_v1 = path == "/api/v1" or path.startswith("/api/v1/")
        return is_api_v1 and method in {"POST", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if not self._is_guarded_write(request):
            return await call_next(request)

        owner_id = f"gui-{os.getpid()}-{secrets.token_hex(6)}"
        run_lock = RunLock(run_lock_path(), owner_id, kind="gui")
        try:
            acquired, owner = run_lock.acquire()
        except Exception:  # noqa: BLE001
            acquired, owner = False, None
        if not acquired:
            return JSONResponse(
                status_code=423,
                content={
                    "detail": "Daily run or another GUI write is in progress; API writes are temporarily unavailable.",
                    "code": "RUN_LOCKED",
                    "lock_owner": owner,
                },
            )

        request.state.run_lock = run_lock
        request.state.keep_run_lock = False
        try:
            return await call_next(request)
        finally:
            if not bool(getattr(request.state, "keep_run_lock", False)):
                run_lock.release()


app.add_middleware(RunLockWriteGuardMiddleware)
app.include_router(api_v1_router)


def startup_catchup_disabled() -> bool:
    value = os.environ.get("VC_NEWS_DISABLE_STARTUP_CATCHUP", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def effective_scheduler_mode() -> dict[str, str]:
    """Return the process scheduler mode and its configuration source."""

    return scheduler_mode_info()


def shutdown_process() -> None:
    time.sleep(0.4)
    try:
        scheduler = getattr(app_scheduler, "scheduler", None)
        if scheduler is not None and getattr(scheduler, "running", False):
            scheduler.shutdown(wait=False)
    finally:
        os._exit(0)


@app.on_event("startup")
def on_startup() -> None:
    scheduler_info = effective_scheduler_mode()
    scheduler_mode = scheduler_info["mode"]
    create_db()
    with SessionLocal() as db:
        seed_all(db)
        proxy_info = apply_configured_proxy_settings(db)
        clear_session_logs(db)
        add_session_log(
            db,
            "proxy_config",
            f"网络代理 mode={proxy_info.get('mode')} source={proxy_info.get('source')} "
            f"http={proxy_info.get('http') or '-'} https={proxy_info.get('https') or '-'}",
        )
        add_session_log(
            db,
            "scheduler_mode",
            f"调度模式 mode={scheduler_info['mode']} source={scheduler_info['source']}",
        )
        if scheduler_mode != "internal" or startup_catchup_disabled():
            if scheduler_mode != "internal":
                message = f"调度模式 {scheduler_mode} 已跳过内部调度器和启动补抓"
            else:
                message = "桌面端启动已跳过自动补抓"
            add_session_log(db, "startup_catchup_skipped", message)
        db.commit()
        CrawlService().mark_stale_not_configured_as_pending(db)
    if scheduler_mode == "internal":
        app_scheduler.start()
        if not startup_catchup_disabled():
            threading.Thread(target=app_scheduler.run_startup_catchup_if_needed, daemon=True).start()


@app.get("/")
def root() -> dict[str, str]:
    return {"ok": "true", "app_id": "ai-investment-agent", "api": "/api/v1"}


@app.get("/api/app-info")
def legacy_app_info() -> dict[str, str]:
    return {"app_id": "ai-investment-agent", "name": "AI 投资情报 Agent"}


@app.post("/shutdown")
def shutdown(background_tasks: BackgroundTasks) -> dict[str, bool]:
    background_tasks.add_task(shutdown_process)
    return {"ok": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default=os.environ.get("VC_NEWS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VC_NEWS_PORT", "8011")))
    parser.add_argument("--no-open-browser", action="store_true")
    args, _ = parser.parse_known_args()
    uvicorn.run("app:app", host=args.host, port=args.port, reload=False)
