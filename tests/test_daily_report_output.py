from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_agent.database import Base
from ai_agent.html_renderer import render_daily_html
from ai_agent.models import ContentItem, EventContent, FinancingEvent, Source
from ai_agent.report_data import build_daily_report_data, validate_report_data


class DailyReportOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.day = date(2026, 8, 17)
        self.tech_source = Source(
            source_name="Tech Source",
            source_category="official_research",
            source_url="https://source.test/tech",
            enabled=True,
        )
        self.industry_source = Source(
            source_name="Industry Source",
            source_category="company_news",
            source_url="https://source.test/industry",
            enabled=True,
        )
        self.finance_source = Source(
            source_name="Finance Source",
            source_category="venture_media",
            source_url="https://source.test/finance",
            enabled=True,
        )
        self.db.add_all([self.tech_source, self.industry_source, self.finance_source])
        self.db.flush()

        self.tech_content = ContentItem(
            source_id=self.tech_source.source_id,
            title='<AI & Agents> launches a model',
            url="https://source.test/tech/article?a=1&b=2",
            canonical_url="https://source.test/tech/article?a=1&b=2",
            source_name=self.tech_source.source_name,
            source_category=self.tech_source.source_category,
            publish_time=datetime(2026, 8, 17, 9, 30),
            publish_time_status="exact",
            crawl_time=datetime(2026, 8, 17, 10, 0),
            summary='<script>alert("escaped")</script>',
        )
        self.industry_content = ContentItem(
            source_id=self.industry_source.source_id,
            title="Enterprise market update",
            url="https://source.test/industry/article",
            canonical_url="https://source.test/industry/article",
            source_name=self.industry_source.source_name,
            source_category=self.industry_source.source_category,
            publish_time=None,
            publish_time_status="missing",
            crawl_time=datetime(2026, 8, 17, 11, 0),
            summary="A company update.",
        )
        self.finance_primary = ContentItem(
            source_id=self.finance_source.source_id,
            title="Acme raises Series A",
            url="https://source.test/finance/acme-primary",
            canonical_url="https://source.test/finance/acme-primary",
            source_name=self.finance_source.source_name,
            source_category=self.finance_source.source_category,
            publish_time=datetime(2026, 8, 17, 8, 0),
            publish_time_status="exact",
            crawl_time=datetime(2026, 8, 17, 8, 30),
            summary="Acme announced a financing round.",
        )
        self.finance_secondary = ContentItem(
            source_id=self.finance_source.source_id,
            title="Acme financing is reported by another source",
            url="https://source.test/finance/acme-secondary",
            canonical_url="https://source.test/finance/acme-secondary",
            source_name="Finance Source 2",
            source_category=self.finance_source.source_category,
            publish_time=datetime(2026, 8, 17, 8, 15),
            publish_time_status="exact",
            crawl_time=datetime(2026, 8, 17, 8, 30),
            summary="A second report of the same round.",
        )
        self.db.add_all([self.tech_content, self.industry_content, self.finance_primary, self.finance_secondary])
        self.db.flush()
        self.event = FinancingEvent(
            event_type="financing",
            event_title="Acme closes Series A",
            company_name="Acme",
            company_name_normalized="acme",
            announced_date=self.day,
            financing_round="Series A",
            amount_original="$10m",
            currency="USD",
            event_summary="Acme announced its Series A.",
            confidence=0.95,
            review_status="confirmed",
        )
        self.db.add(self.event)
        self.db.flush()
        self.db.add_all(
            [
                EventContent(event_id=self.event.event_id, content_id=self.finance_primary.content_id, is_primary_source=True),
                EventContent(event_id=self.event.event_id, content_id=self.finance_secondary.content_id, is_primary_source=False),
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_report_has_three_sections_and_merges_financing_sources(self) -> None:
        report = build_daily_report_data(self.db, self.day.isoformat())

        self.assertEqual([section["key"] for section in report["sections"]], ["technology", "industry", "funding"])
        self.assertEqual(validate_report_data(report), [])
        funding = next(section for section in report["sections"] if section["key"] == "funding")
        funding_item = funding["groups"][0]["items"][0]
        self.assertEqual(funding_item["event_id"], self.event.event_id)
        self.assertEqual(funding_item["url"], self.finance_primary.url)
        self.assertEqual(len(funding_item["sources"]), 2)
        self.assertEqual(report["stats"]["included_items"], 3)

    def test_empty_sections_are_retained_and_html_is_escaped_and_traceable(self) -> None:
        empty_report = build_daily_report_data(self.db, "2026-08-18")
        self.assertEqual(validate_report_data(empty_report), [])
        self.assertEqual([len(section["groups"]) for section in empty_report["sections"]], [0, 0, 0])

        report = build_daily_report_data(self.db, self.day.isoformat())
        with TemporaryDirectory() as directory:
            output = render_daily_html(report, Path(directory) / "daily.html")
            html = output.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("[data-report-item]")
        self.assertEqual(len(cards), report["stats"]["included_items"])
        self.assertTrue(all(card.get("data-content-id") or card.get("data-event-id") for card in cards))
        self.assertIn("&lt;AI &amp; Agents&gt;", html)
        self.assertIn("&lt;script&gt;alert(&#34;escaped&#34;)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(\"escaped\")</script>", html)
        self.assertIsNotNone(soup.select_one('[data-content-id="%s"]' % self.tech_content.content_id))
        self.assertIsNotNone(soup.select_one('[data-event-id="%s"]' % self.event.event_id))
        self.assertNotIn("<link", html.lower())


if __name__ == "__main__":
    unittest.main()
