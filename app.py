from __future__ import annotations

import argparse
import os
import threading
import time

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_agent.api_v1 import router as api_v1_router
from ai_agent.database import SessionLocal, create_db
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
app.include_router(api_v1_router)


def startup_catchup_disabled() -> bool:
    value = os.environ.get("VC_NEWS_DISABLE_STARTUP_CATCHUP", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def shutdown_process() -> None:
    time.sleep(0.4)
    try:
        if app_scheduler.scheduler.running:
            app_scheduler.scheduler.shutdown(wait=False)
    finally:
        os._exit(0)


@app.on_event("startup")
def on_startup() -> None:
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
        if startup_catchup_disabled():
            add_session_log(db, "startup_catchup_skipped", "桌面端启动已跳过自动补抓")
        db.commit()
        CrawlService().mark_stale_not_configured_as_pending(db)
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