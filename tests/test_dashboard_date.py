from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from ai_agent.api_v1 import dashboard
from ai_agent.database import Base
from ai_agent.models import ContentItem, Source


class DashboardDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.source = Source(
            source_name="Test source",
            source_category="venture_media",
            source_url="https://example.com/",
            enabled=True,
        )
        self.db.add(self.source)
        self.db.flush()
        self._add_item("Previous-day news", datetime(2026, 6, 19, 9, 0))
        self._add_item("Current-day news", datetime(2026, 6, 20, 9, 0))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _add_item(self, title: str, crawl_time: datetime) -> None:
        self.db.add(
            ContentItem(
                source_id=self.source.source_id,
                title=title,
                url=f"https://example.com/{title}",
                canonical_url=f"https://example.com/{title}",
                source_name=self.source.source_name,
                source_category=self.source.source_category,
                crawl_time=crawl_time,
            )
        )

    @staticmethod
    def _request() -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/dashboard",
                "query_string": b"",
                "headers": [],
            }
        )

    @patch("ai_agent.api_v1.db_now", return_value=datetime(2026, 6, 20, 12, 0))
    def test_selected_date_filters_counts_and_source_items(self, _mock_now) -> None:
        payload = dashboard(self._request(), selected_date="2026-06-19", db=self.db)

        self.assertEqual(payload["selected_date"], "2026-06-19")
        self.assertEqual(payload["current_date"], "2026-06-20")
        self.assertEqual(payload["today_contents"], 1)
        self.assertEqual(payload["source_groups"][0]["total_count"], 1)
        self.assertEqual(payload["source_groups"][0]["content_items"][0]["title"], "Previous-day news")

    @patch("ai_agent.api_v1.db_now", return_value=datetime(2026, 6, 20, 12, 0))
    def test_date_defaults_to_today(self, _mock_now) -> None:
        payload = dashboard(self._request(), selected_date=None, db=self.db)

        self.assertEqual(payload["selected_date"], "2026-06-20")
        self.assertEqual(payload["today_contents"], 1)
        self.assertEqual(payload["source_groups"][0]["content_items"][0]["title"], "Current-day news")

    def test_invalid_date_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as context:
            dashboard(self._request(), selected_date="2026-02-30", db=self.db)

        self.assertEqual(context.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
