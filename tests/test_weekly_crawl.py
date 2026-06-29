from __future__ import annotations

from datetime import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_agent.database import Base
from ai_agent.models import ContentItem, CrawlRun, DailySummary, Source
from ai_agent.services import WeeklyCrawlService


class WeeklyCrawlServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        source = Source(
            source_name="测试源",
            source_category="venture_media",
            source_url="https://example.com/",
        )
        self.db.add(source)
        self.db.flush()
        self.source_id = source.source_id

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _content(self, title: str, crawled_at: datetime) -> None:
        self.db.add(
            ContentItem(
                source_id=self.source_id,
                title=title,
                url=f"https://example.com/{title}",
                canonical_url=f"https://example.com/{title}",
                source_name="测试源",
                source_category="venture_media",
                crawl_time=crawled_at,
            )
        )

    def test_target_dates_start_on_monday_and_end_on_target_day(self) -> None:
        dates = WeeklyCrawlService().target_dates(datetime(2026, 6, 17, 9, 0))

        self.assertEqual(dates, ["2026-06-15", "2026-06-16", "2026-06-17"])

    def test_status_marks_completed_parent_runs_as_crawled(self) -> None:
        self.db.add(
            CrawlRun(
                source_id=None,
                started_at=datetime(2026, 6, 15, 12, 0),
                finished_at=datetime(2026, 6, 15, 12, 30),
                status="partial_success",
                new_items=3,
                failed_items=1,
                message="新增 3 条，失败 1 项",
            )
        )
        self.db.add(
            CrawlRun(
                source_id=None,
                started_at=datetime(2026, 6, 16, 12, 0),
                finished_at=datetime(2026, 6, 16, 12, 5),
                status="failed",
                new_items=0,
                failed_items=1,
                message="抓取失败",
            )
        )
        self._content("周一内容", datetime(2026, 6, 15, 13, 0))
        self.db.add(DailySummary(summary_date="2026-06-15", total_items=1))
        self.db.commit()

        status = WeeklyCrawlService().status(self.db, datetime(2026, 6, 17, 9, 0))

        self.assertEqual(status["week_start"], "2026-06-15")
        self.assertEqual(status["today"], "2026-06-17")
        self.assertEqual(status["missing_dates"], ["2026-06-16", "2026-06-17"])
        monday = next(day for day in status["days"] if day["date"] == "2026-06-15")
        tuesday = next(day for day in status["days"] if day["date"] == "2026-06-16")
        self.assertTrue(monday["crawled"])
        self.assertTrue(monday["has_summary"])
        self.assertEqual(monday["content_count"], 1)
        self.assertFalse(tuesday["crawled"])
        self.assertEqual(tuesday["run_status"], "failed")


if __name__ == "__main__":
    unittest.main()
