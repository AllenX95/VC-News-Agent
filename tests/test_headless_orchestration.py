from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from ai_agent.headless import main
from ai_agent.orchestration import (
    EXIT_LOCK,
    EXIT_PARTIAL,
    EXIT_RENDER,
    EXIT_SCHEMA,
    EXIT_SKIPPED,
    DailyRunOptions,
    DailyRunOrchestrator,
)


class FakeDB:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeSummary:
    def generate(self, db, target_date):
        return {"markdown": f"# Daily {target_date}\n"}


def _report(_db, target_date, warnings=None):
    return {
        "schema_version": "1.0",
        "template_version": "daily-v1",
        "report_date": target_date,
        "sections": [
            {"key": key, "title": title, "groups": []}
            for key, title in (("technology", "技术进展"), ("industry", "产业新闻"), ("funding", "融资新闻"))
        ],
        "warnings": list(warnings or []),
    }


def _validate(data):
    return []


def _render(data, path):
    path = Path(path)
    path.write_text("<html><body>daily</body></html>", encoding="utf-8")
    return path


def _runner(tmp_path, *, crawl=None, report=_report, validate=_validate, render=_render, enricher=None):
    return DailyRunOrchestrator(
        runtime_root=tmp_path / "runs",
        db_factory=FakeDB,
        create_db_fn=lambda: None,
        seed_fn=lambda _db: None,
        apply_proxy_fn=lambda _db: None,
        crawl_adapter=crawl or (lambda _db, _target_date: {"status": "success", "new_items": 3, "total_items": 5}),
        report_builder=report,
        report_validator=validate,
        report_renderer=render,
        report_enricher=enricher
        or (
            lambda _db, value: SimpleNamespace(
                report=value,
                generation_mode="deterministic",
                warnings=[],
                metadata={"task_name": "generate_daily_investment_report"},
            )
        ),
        summary_adapter=FakeSummary(),
        now_fn=lambda: datetime(2026, 8, 17, 10, 0, 0),
    )


class HeadlessOrchestrationTests(unittest.TestCase):
    def test_daily_success_writes_manifest_latest_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = _runner(Path(directory))
            result = runner.run(DailyRunOptions())

            self.assertEqual(result.status, "success")
            self.assertEqual(result.exit_code, 0)
            self.assertIsNotNone(result.manifest_path)
            self.assertTrue(result.manifest_path.exists())
            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["stages"]["html_render"], "success")
            self.assertTrue((Path(directory) / "runs" / "2026-08-17" / "latest.json").exists())
            self.assertTrue(Path(result.artifacts["html"]).exists())
            self.assertTrue(Path(result.artifacts["markdown"]).read_text(encoding="utf-8").startswith("# Daily"))

    def test_successful_date_is_idempotently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = _runner(Path(directory))
            first = runner.run(DailyRunOptions())
            second = runner.run(DailyRunOptions())

            self.assertEqual(first.status, "success")
            self.assertEqual(second.status, "skipped_already_success")
            self.assertEqual(second.exit_code, EXIT_SKIPPED)
            self.assertEqual(second.run_id, first.run_id)

    def test_llm_enrichment_metadata_is_written_to_manifest(self):
        def enrich(_db, value):
            return SimpleNamespace(
                report={**value, "executive_summary": "LLM summary"},
                generation_mode="llm",
                warnings=[],
                metadata={
                    "task_name": "generate_daily_investment_report",
                    "model_name": "test-model",
                    "provider_type": "openai",
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            result = _runner(Path(directory), enricher=enrich).run(DailyRunOptions())

            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["stages"]["enrichment"], "success")
            self.assertEqual(payload["report_generation"]["generation_mode"], "llm")
            self.assertEqual(payload["report_generation"]["model_name"], "test-model")

    def test_partial_crawl_keeps_html_and_updates_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = _runner(Path(directory), crawl=lambda _db, _date: {"status": "partial", "failed_items": 1})
            result = runner.run(DailyRunOptions())

            self.assertEqual(result.status, "partial")
            self.assertEqual(result.exit_code, EXIT_PARTIAL)
            self.assertTrue(result.warnings)
            latest = json.loads(
                (Path(directory) / "runs" / "2026-08-17" / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest["status"], "partial")
            self.assertTrue(Path(result.artifacts["html"]).exists())

    def test_lock_conflict_does_not_update_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runs"
            runtime.mkdir(parents=True)
            (runtime / ".daily-run.lock").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "run_id": "other",
                        "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            runner = _runner(Path(directory))
            result = runner.run(DailyRunOptions(force=True))

            self.assertEqual(result.status, "lock_conflict")
            self.assertEqual(result.exit_code, EXIT_LOCK)
            self.assertFalse((runtime / "2026-08-17" / "latest.json").exists())
            self.assertEqual(
                json.loads((runtime / ".daily-run.lock").read_text(encoding="utf-8"))["run_id"], "other"
            )

    def test_schema_failure_does_not_render(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = _runner(Path(directory), validate=lambda _data: ["missing sections"])
            result = runner.run(DailyRunOptions())

            self.assertEqual(result.status, "report_data_invalid")
            self.assertEqual(result.exit_code, EXIT_SCHEMA)
            self.assertNotIn("html", result.artifacts)
            self.assertFalse(list((Path(directory) / "runs" / "2026-08-17" / result.run_id).glob("daily.html")))

    def test_render_failure_keeps_report_data_but_not_html(self):
        def fail_render(_data, _path):
            raise RuntimeError("renderer unavailable")

        with tempfile.TemporaryDirectory() as directory:
            runner = _runner(Path(directory), render=fail_render)
            result = runner.run(DailyRunOptions())

            self.assertEqual(result.status, "html_render_failed")
            self.assertEqual(result.exit_code, EXIT_RENDER)
            self.assertIn("report_data", result.artifacts)
            self.assertNotIn("html", result.artifacts)

    def test_cli_outputs_machine_readable_final_line(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = _runner(Path(directory))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["daily", "--date", "2026-08-17"], orchestrator=runner)

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue().strip().splitlines()[-1])
            self.assertEqual(payload["status"], "success")
            self.assertTrue(payload["manifest_path"])


if __name__ == "__main__":
    unittest.main()
