from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import app as app_module
from ai_agent import api_v1
from ai_agent.run_lock import RunLock, read_run_lock
from starlette.requests import Request
from starlette.responses import PlainTextResponse


def _request(method: str, path: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
    }
    return Request(scope)


async def _call_guard(guard: app_module.RunLockWriteGuardMiddleware, method: str, path: str):
    async def call_next(_request: Request):
        return PlainTextResponse("passed", status_code=204)

    return await guard.dispatch(_request(method, path), call_next)


class RunLockTests(unittest.TestCase):
    def test_conflict_and_release_are_process_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".daily-run.lock"
            first = RunLock(path, "run-a")
            second = RunLock(path, "run-b")

            acquired, owner = first.acquire()
            self.assertTrue(acquired)
            self.assertIsNone(owner)

            acquired, owner = second.acquire()
            self.assertFalse(acquired)
            self.assertEqual(owner["owner_id"], "run-a")
            status = read_run_lock(directory)
            self.assertTrue(status["active"])
            self.assertEqual(status["owner"]["run_id"], "run-a")

            first.release()
            self.assertFalse(read_run_lock(directory)["active"])

    def test_old_or_dead_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".daily-run.lock"
            old = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
            path.write_text(
                json.dumps({"pid": os.getpid(), "run_id": "old", "started_at": old}),
                encoding="utf-8",
            )

            replacement = RunLock(path, "run-new", max_runtime_seconds=60)
            acquired, owner = replacement.acquire()

            self.assertTrue(acquired)
            self.assertIsNone(owner)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["owner_id"], "run-new")
            replacement.release()

    def test_owner_identifier_is_bounded_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".daily-run.lock"
            lock = RunLock(path, "api_key=top-secret-value")
            acquired, _owner = lock.acquire()

            self.assertTrue(acquired)
            payload = path.read_text(encoding="utf-8")
            self.assertNotIn("top-secret-value", payload)
            self.assertLessEqual(len(json.loads(payload)["owner_id"]), 128)
            lock.release()


class RunLockWriteGuardTests(unittest.TestCase):
    def test_active_lock_allows_get_and_blocks_api_writes(self) -> None:
        guard = app_module.RunLockWriteGuardMiddleware(app_module.app)
        active_lock = Mock()
        active_lock.acquire.return_value = (False, {"kind": "headless"})

        with patch.object(app_module, "RunLock", return_value=active_lock):
            get_response = asyncio.run(_call_guard(guard, "GET", "/api/v1/dashboard"))
            post_response = asyncio.run(_call_guard(guard, "POST", "/api/v1/sources"))

        self.assertEqual(get_response.status_code, 204)
        self.assertEqual(post_response.status_code, 423)
        self.assertEqual(post_response.body[0:1], b"{")
        self.assertIn(b"RUN_LOCKED", post_response.body)

    def test_free_api_write_holds_and_releases_lock(self) -> None:
        guard = app_module.RunLockWriteGuardMiddleware(app_module.app)
        free_lock = Mock()
        free_lock.acquire.return_value = (True, None)

        with patch.object(app_module, "RunLock", return_value=free_lock):
            response = asyncio.run(_call_guard(guard, "PATCH", "/api/v1/settings"))

        self.assertEqual(response.status_code, 204)
        free_lock.release.assert_called_once_with()

    def test_shutdown_and_non_api_writes_are_not_guarded(self) -> None:
        guard = app_module.RunLockWriteGuardMiddleware(app_module.app)
        shutdown_response = asyncio.run(_call_guard(guard, "POST", "/shutdown"))
        legacy_response = asyncio.run(_call_guard(guard, "POST", "/api/app-info"))

        self.assertEqual(shutdown_response.status_code, 204)
        self.assertEqual(legacy_response.status_code, 204)

    def test_weekly_background_releases_transferred_lock(self) -> None:
        transferred = Mock()

        api_v1._run_weekly_crawl_background([], transferred)

        transferred.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
