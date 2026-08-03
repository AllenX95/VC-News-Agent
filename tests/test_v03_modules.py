from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_agent.database import Base
from ai_agent.financing_events import FinancingEventCatalog
from ai_agent.models import ContentItem, ContentTag, FinancingEvent, Source, TagDefinition
from ai_agent.reports import ReportWorkspace
from ai_agent.services import attach_tag
from ai_agent.utils import json_loads
from ai_agent.v03_contracts import (
    BuildEventRequest,
    ExportReportCommand,
    GenerateReportCommand,
    ReportPreviewRequest,
    ReviseEventCommand,
    ReviseReportCommand,
    SaveWatchCommand,
    UpdateWatchCommand,
)
from ai_agent.watchlist import WatchConflictError, Watchlist


class FakeGenerator:
    def __init__(self, markdown: str = "# 测试报告\n") -> None:
        self.markdown = markdown
        self.calls = 0

    def generate(self, db: Session, report_type: str, start: date, end: date, inputs: list[dict]) -> dict:
        self.calls += 1
        return {"markdown": self.markdown, "prompt_id": 7, "model_name": "fake-model"}


class FailingGenerator:
    def generate(self, db: Session, report_type: str, start: date, end: date, inputs: list[dict]) -> dict:
        raise RuntimeError("fake LLM failed")


class V03ModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        source = Source(source_name="测试源", source_category="venture_media", source_url="https://example.com")
        self.db.add(source)
        self.db.flush()
        self.source_id = source.source_id
        self.financing_tag = TagDefinition(tag_key="content_type", tag_value="financing")
        self.relevance_tag = TagDefinition(tag_key="ai_financing_relevance", tag_value="high")
        self.db.add_all([self.financing_tag, self.relevance_tag])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def content(self, title: str, summary: str, day: int = 1) -> ContentItem:
        item = ContentItem(
            source_id=self.source_id,
            title=title,
            url=f"https://example.com/{title}-{day}",
            canonical_url=f"https://example.com/{title}-{day}",
            source_name="测试源",
            source_category="venture_media",
            summary=summary,
            crawl_time=datetime(2026, 8, day, 10, 0),
            ai_related=True,
        )
        self.db.add(item)
        self.db.flush()
        attach_tag(self.db, item, "content_type", "financing", "llm")
        attach_tag(self.db, item, "ai_financing_relevance", "high", "llm")
        return item

    def test_event_candidates_merge_same_financing_and_keep_sources(self) -> None:
        first = self.content("灵巧手完成A轮融资", "灵巧手完成5000万元A轮融资，由测试资本领投。", 1)
        second = self.content("灵巧手获5000万元A轮融资", "灵巧手获5000万元A轮融资，测试资本参与。", 2)
        self.db.commit()

        result = FinancingEventCatalog().build_candidates(
            self.db,
            BuildEventRequest(start_date=date(2026, 8, 1), end_date=date(2026, 8, 3)),
        )
        events = self.db.scalars(select(FinancingEvent)).all()
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["attached"], 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].contents), 2)
        self.assertEqual({relation.content_id for relation in events[0].contents}, {first.content_id, second.content_id})
        self.assertIn("公司名称一致", json_loads(events[0].contents[1].match_reasons_json, []))

    def test_manual_confirmation_is_not_changed_by_future_build(self) -> None:
        item = self.content("Alpha完成A轮融资", "Alpha完成100万美元A轮融资。")
        self.db.commit()
        catalog = FinancingEventCatalog()
        catalog.build_candidates(self.db, BuildEventRequest(start_date=date(2026, 8, 1), end_date=date(2026, 8, 3)))
        event = self.db.scalar(select(FinancingEvent))
        assert event is not None
        catalog.revise(self.db, event.event_id, ReviseEventCommand(review_status="confirmed"))
        another = self.content("Alpha完成B轮融资", "Alpha完成200万美元B轮融资。", 3)
        self.db.commit()
        catalog.build_candidates(self.db, BuildEventRequest(start_date=date(2026, 8, 1), end_date=date(2026, 8, 3)))
        self.assertEqual(len(self.db.scalars(select(FinancingEvent)).all()), 2)
        self.assertEqual(len(event.contents), 1)
        self.assertEqual(another.content_id, self.db.scalar(select(FinancingEvent).order_by(FinancingEvent.event_id.desc())).contents[0].content_id)

    def test_watchlist_rejects_duplicate_active_and_allows_readd_after_completion(self) -> None:
        event = FinancingEvent(
            event_title="测试公司完成A轮融资",
            company_name="测试公司",
            company_name_normalized="测试公司",
            announced_date=date(2026, 8, 1),
            review_status="confirmed",
        )
        self.db.add(event)
        self.db.commit()
        service = Watchlist()
        command = SaveWatchCommand(
            target_type="financing_event",
            target_id=event.event_id,
            status="follow_up",
            priority="high",
            next_review_date=date(2026, 8, 3),
        )
        first = service.save(self.db, command)
        with self.assertRaises(WatchConflictError):
            service.save(self.db, command)
        service.update(self.db, first["watch_id"], UpdateWatchCommand(status="completed"))
        second = service.save(self.db, command)
        self.assertNotEqual(first["watch_id"], second["watch_id"])

    def test_report_preview_uses_confirmed_events_and_versions_are_immutable(self) -> None:
        confirmed = FinancingEvent(
            event_title="已确认公司完成A轮融资",
            company_name="已确认公司",
            company_name_normalized="已确认公司",
            announced_date=date(2026, 8, 2),
            review_status="confirmed",
        )
        pending = FinancingEvent(
            event_title="待确认公司完成A轮融资",
            company_name="待确认公司",
            company_name_normalized="待确认公司",
            announced_date=date(2026, 8, 2),
            review_status="pending",
        )
        self.db.add_all([confirmed, pending])
        self.db.commit()
        workspace = ReportWorkspace(FakeGenerator())
        preview = workspace.preview(
            self.db,
            ReportPreviewRequest(
                report_type="weekly_financing",
                period_start=date(2026, 8, 1),
                period_end=date(2026, 8, 3),
            ),
        )
        self.assertEqual([item["target_id"] for item in preview["inputs"]], [confirmed.event_id])
        report = workspace.generate(
            self.db,
            GenerateReportCommand(
                report_type="weekly_financing",
                period_start=date(2026, 8, 1),
                period_end=date(2026, 8, 3),
            ),
        )
        workspace.revise(self.db, report["report_id"], ReviseReportCommand(markdown_text="# v2"))
        detail = workspace.get(self.db, report["report_id"])
        self.assertEqual(detail["latest_version_number"], 2)
        self.assertEqual(len(detail["versions"]), 2)
        self.assertEqual(detail["versions"][0]["version_number"], 2)
        with tempfile.TemporaryDirectory() as directory:
            exported = workspace.export(
                self.db,
                report["report_id"],
                ExportReportCommand(version_number=1, output_path=str(Path(directory) / "report.md")),
            )
            self.assertEqual(Path(exported["file_path"]).read_text(encoding="utf-8"), "# 测试报告\n")

    def test_report_generation_failure_is_recorded_without_success_version(self) -> None:
        workspace = ReportWorkspace(FailingGenerator())
        with self.assertRaisesRegex(RuntimeError, "fake LLM failed"):
            workspace.generate(
                self.db,
                GenerateReportCommand(
                    report_type="custom",
                    period_start=date(2026, 8, 1),
                    period_end=date(2026, 8, 3),
                ),
            )
        reports = workspace.list(self.db)["items"]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["latest_version_number"], 0)
        self.assertEqual(reports[0]["versions"][0]["generation_status"], "failed")


if __name__ == "__main__":
    unittest.main()
