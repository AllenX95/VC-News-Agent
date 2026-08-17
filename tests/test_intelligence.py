from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_agent.database import Base
from ai_agent.intelligence import IntelligenceInbox, score_content
from ai_agent.models import ContentItem, ContentTag, Entity, Source, TagDefinition


class IntelligenceInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        source = Source(
            source_name="AI 媒体",
            source_category="ai_media",
            source_url="https://example.com",
        )
        self.db.add(source)
        self.db.flush()
        content = ContentItem(
            source_id=source.source_id,
            title="某 AI 公司完成 A 轮融资",
            url="https://example.com/news/1",
            canonical_url="https://example.com/news/1",
            source_name=source.source_name,
            source_category=source.source_category,
            summary="公司宣布完成 5000 万元融资。",
            crawl_time=datetime(2026, 8, 3, 10, 0),
            ai_related=True,
        )
        self.db.add(content)
        self.db.flush()
        tag = TagDefinition(tag_key="content_type", tag_value="financing")
        self.db.add(tag)
        self.db.flush()
        self.db.add(ContentTag(content_id=content.content_id, tag_id=tag.tag_id, tag_key=tag.tag_key, tag_value=tag.tag_value))
        self.db.commit()
        self.content_id = content.content_id
        self.inbox = IntelligenceInbox()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_score_is_explainable(self) -> None:
        content = self.db.get(ContentItem, self.content_id)
        assert content is not None
        result = score_content(content)
        self.assertGreaterEqual(result.score, 80)
        self.assertTrue(result.reasons)
        self.assertTrue(any("融资" in reason for reason in result.reasons))

    def test_list_returns_high_value_item_and_reasons(self) -> None:
        result = self.inbox.list(self.db, minimum_score=60)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["content_id"], self.content_id)
        self.assertGreaterEqual(result["items"][0]["relevance_score"], 80)
        self.assertTrue(result["items"][0]["relevance_reasons"])

    def test_review_persists_decision_and_audit(self) -> None:
        result = self.inbox.review(self.db, self.content_id, "follow_up", "需要确认投资方")
        self.assertEqual(result["review_status"], "follow_up")
        self.assertEqual(result["review_note"], "需要确认投资方")
        content = self.db.get(ContentItem, self.content_id)
        assert content is not None
        self.assertEqual(content.review_status, "follow_up")
        self.assertEqual(len(content.reviews), 1)
        self.assertEqual(content.reviews[0].decision, "follow_up")

    def test_reprocess_does_not_reset_manual_review(self) -> None:
        self.inbox.review(self.db, self.content_id, "not_relevant")
        result = self.inbox.reprocess(self.db, self.content_id)
        self.assertEqual(result["review_status"], "ignored")
        self.assertEqual(result["relevance_score"], 20)


if __name__ == "__main__":
    unittest.main()
