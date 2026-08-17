from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_agent.automation_status import AutomationStatusReader


class AutomationStatusReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp_dir.name) / "runs"
        self.reader = AutomationStatusReader(self.runtime)
        self.target_date = "2026-08-17"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_run(self, *, status: str, html: bool = True, warnings: list[str] | None = None) -> Path:
        artifacts_root = self.runtime / "artifacts"
        report_root = self.runtime / "report"
        artifacts_root.mkdir(parents=True, exist_ok=True)
        report_root.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, str] = {}
        if html:
            report = report_root / "20260817-daily-report.html"
            report.write_text("<html><body>daily</body></html>", encoding="utf-8")
            artifacts["html"] = str(report)
        manifest = artifacts_root / "20260817-run-1-run-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": "run-1",
                    "target_date": self.target_date,
                    "status": status,
                    "started_at": "2026-08-17T10:00:00+08:00",
                    "finished_at": "2026-08-17T10:10:00+08:00",
                    "counts": {"included_items": 4, "technology": 2},
                    "warnings": warnings or [],
                    "artifacts": artifacts,
                }
            ),
            encoding="utf-8",
        )
        (artifacts_root / "20260817-latest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "target_date": self.target_date,
                    "run_id": "run-1",
                    "status": status,
                    "manifest_path": str(manifest),
                }
            ),
            encoding="utf-8",
        )
        return report_root

    def test_missing_runtime_returns_explainable_missing_state(self) -> None:
        status = self.reader.get_status(self.target_date)

        self.assertEqual(status["status"], "missing")
        self.assertFalse(status["running"])
        self.assertIsNone(status["latest"])
        self.assertEqual(status["counts"], {})
        with self.assertRaises(FileNotFoundError):
            self.reader.resolve_latest_html(self.target_date)

    def test_running_lock_is_reported_without_mutating_lock(self) -> None:
        self.runtime.mkdir(parents=True)
        lock = self.runtime / ".daily-run.lock"
        payload = {
            "pid": os.getpid(),
            "run_id": "run-active",
            "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        }
        lock.write_text(json.dumps(payload), encoding="utf-8")

        status = self.reader.get_status(self.target_date)

        self.assertEqual(status["status"], "running")
        self.assertTrue(status["running"])
        self.assertEqual(status["lock"]["run_id"], "run-active")
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["run_id"], "run-active")

    def test_success_resolves_html_and_exposes_counts(self) -> None:
        runtime = self._write_run(status="success")

        status = self.reader.get_status(self.target_date)
        report = self.reader.resolve_latest_html(self.target_date)

        self.assertEqual(status["status"], "success")
        self.assertTrue(status["html_available"])
        self.assertEqual(status["counts"]["included_items"], 4)
        self.assertEqual(report, runtime / "20260817-daily-report.html")

    def test_partial_report_keeps_warning_and_html_entry(self) -> None:
        self._write_run(status="partial", warnings=["one source failed"])

        status = self.reader.get_status(self.target_date)

        self.assertEqual(status["status"], "partial")
        self.assertTrue(status["html_available"])
        self.assertIn("one source failed", status["warnings"])

    def test_manifest_path_traversal_is_never_served(self) -> None:
        artifacts_root = self.runtime / "artifacts"
        artifacts_root.mkdir(parents=True)
        outside = Path(self.temp_dir.name) / "outside.html"
        outside.write_text("<html>outside</html>", encoding="utf-8")
        (artifacts_root / "20260817-latest.json").write_text(
            json.dumps(
                {
                    "target_date": self.target_date,
                    "manifest_path": str(Path(self.temp_dir.name) / ".." / "outside-manifest.json"),
                }
            ),
            encoding="utf-8",
        )

        status = self.reader.get_status(self.target_date)

        self.assertIn(status["status"], {"missing", "corrupt"})
        self.assertFalse(status["html_available"])
        with self.assertRaises((FileNotFoundError, PermissionError)):
            self.reader.resolve_latest_html(self.target_date)


if __name__ == "__main__":
    unittest.main()
