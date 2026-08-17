from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import app
from ai_agent.config import (
    DEFAULT_SCHEDULER_MODE,
    SCHEDULER_MODE_ENV_NAME,
    scheduler_mode_info,
    validate_scheduler_mode,
)


class SchedulerModeConfigTests(unittest.TestCase):
    def test_mode_defaults_to_external_with_default_source(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(DEFAULT_SCHEDULER_MODE, "external")
            self.assertEqual(
                scheduler_mode_info(),
                {
                    "mode": "external",
                    "source": "default",
                    "env_name": SCHEDULER_MODE_ENV_NAME,
                },
            )

    def test_environment_mode_is_normalized_and_reports_source(self) -> None:
        with patch.dict(os.environ, {SCHEDULER_MODE_ENV_NAME: " EXTERNAL "}, clear=True):
            info = scheduler_mode_info()

        self.assertEqual(info["mode"], "external")
        self.assertEqual(info["source"], "environment")
        self.assertEqual(validate_scheduler_mode("in-process"), "internal")

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_scheduler_mode("sometimes")


class SchedulerModeStartupTests(unittest.TestCase):
    def _run_startup(
        self,
        mode: str | None = None,
        disable_catchup: bool = False,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        environment = {}
        if mode is not None:
            environment[SCHEDULER_MODE_ENV_NAME] = mode
        if disable_catchup:
            environment["VC_NEWS_DISABLE_STARTUP_CATCHUP"] = "1"

        db = MagicMock()
        session_factory = MagicMock()
        session_factory.return_value.__enter__.return_value = db
        thread_instance = MagicMock()

        with patch.dict(os.environ, environment, clear=True), \
            patch.object(app, "create_db"), \
            patch.object(app, "SessionLocal", session_factory), \
            patch.object(app, "seed_all"), \
            patch.object(app, "apply_configured_proxy_settings", return_value={"mode": "off", "source": "disabled"}), \
            patch.object(app, "clear_session_logs"), \
            patch.object(app, "add_session_log"), \
            patch.object(app, "CrawlService") as crawl_service, \
            patch.object(app.app_scheduler, "start") as scheduler_start, \
            patch.object(app.app_scheduler, "run_startup_catchup_if_needed") as catchup, \
            patch.object(app.threading, "Thread", return_value=thread_instance) as thread_factory:
            app.on_startup()

        # The return values provide the test with the observable behavior and
        # avoid asserting the internal logging implementation.
        return scheduler_start, thread_instance, thread_factory

    def test_internal_starts_scheduler_and_startup_catchup(self) -> None:
        scheduler_start, thread_instance, _thread_factory = self._run_startup("internal")

        scheduler_start.assert_called_once_with()
        thread_instance.start.assert_called_once_with()

    def test_external_does_not_start_scheduler_or_catchup(self) -> None:
        scheduler_start, thread_instance, thread_factory = self._run_startup("external")

        scheduler_start.assert_not_called()
        thread_instance.start.assert_not_called()
        thread_factory.assert_not_called()

    def test_disabled_does_not_start_scheduler_or_catchup(self) -> None:
        scheduler_start, thread_instance, thread_factory = self._run_startup("disabled")

        scheduler_start.assert_not_called()
        thread_instance.start.assert_not_called()
        thread_factory.assert_not_called()

    def test_legacy_disable_catchup_keeps_internal_scheduler_but_skips_catchup(self) -> None:
        scheduler_start, thread_instance, thread_factory = self._run_startup("internal", disable_catchup=True)

        scheduler_start.assert_called_once_with()
        thread_instance.start.assert_not_called()
        thread_factory.assert_not_called()


class ShutdownTests(unittest.TestCase):
    def test_shutdown_is_safe_when_scheduler_is_not_running(self) -> None:
        scheduler = MagicMock(running=False)
        with patch.object(app.app_scheduler, "scheduler", scheduler), \
            patch.object(app.time, "sleep"), \
            patch.object(app.os, "_exit") as exit_process:
            app.shutdown_process()

        scheduler.shutdown.assert_not_called()
        exit_process.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
