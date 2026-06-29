from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_agent.database import Base
from ai_agent.api_v1 import select_local_directory
from ai_agent.models import ContentItem, ContentTag, LLMConfig, Source
from ai_agent.services import CrawlService, FinancingService, LLMService, attach_entity, attach_tag


class FinancingFilterTests(unittest.TestCase):
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

    def _item(self, title: str, ai_related: bool | None) -> ContentItem:
        item = ContentItem(
            source_id=self.source_id,
            title=title,
            url=f"https://example.com/{title}",
            canonical_url=f"https://example.com/{title}",
            source_name="测试源",
            source_category="venture_media",
            ai_related=ai_related,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def test_filter_requires_all_three_llm_signals(self) -> None:
        high = self._item("AI公司完成A轮融资", True)
        attach_tag(self.db, high, "content_type", "financing", "llm")
        attach_tag(self.db, high, "ai_financing_relevance", "high", "llm")

        keyword_only = self._item("普通公司完成融资", False)
        attach_tag(self.db, keyword_only, "content_type", "financing", "llm")

        incidental = self._item("产品发布并回顾历史融资", True)
        attach_tag(self.db, incidental, "content_type", "company_news", "llm")
        attach_tag(self.db, incidental, "ai_financing_relevance", "none", "llm")
        self.db.commit()

        rows = self.db.scalars(select(ContentItem).where(FinancingService().content_filter())).all()

        self.assertEqual([item.content_id for item in rows], [high.content_id])

    def test_manual_exclusion_removes_from_financing_filter(self) -> None:
        item = self._item("AI公司完成融资但误识别", True)
        attach_tag(self.db, item, "content_type", "financing", "llm")
        attach_tag(self.db, item, "ai_financing_relevance", "high", "llm")
        self.db.commit()

        result = FinancingService().exclude_content_ids(self.db, [item.content_id])
        rows = self.db.scalars(select(ContentItem).where(FinancingService().content_filter())).all()

        self.assertEqual(result["excluded"], 1)
        self.assertEqual(rows, [])

    @patch("ai_agent.services.db_now", return_value=datetime(2026, 6, 20, 12, 0))
    def test_query_items_filters_selected_date_without_latest_fallback(self, _now: Mock) -> None:
        previous = self._item("上一日公司完成融资", True)
        previous.crawl_time = datetime(2026, 6, 19, 9, 0)
        previous.summary = "上一日公司完成A轮融资。"
        current = self._item("当日公司完成融资", True)
        current.crawl_time = datetime(2026, 6, 20, 9, 0)
        current.summary = "当日公司完成A轮融资。"
        for item in (previous, current):
            attach_tag(self.db, item, "content_type", "financing", "llm")
            attach_tag(self.db, item, "ai_financing_relevance", "high", "llm")
        self.db.commit()

        items, label, window_date = FinancingService().query_items(self.db, target_date=date(2026, 6, 19))
        empty_items, empty_label, empty_window_date = FinancingService().query_items(
            self.db,
            target_date=date(2026, 6, 18),
        )

        self.assertEqual(window_date, "2026-06-19")
        self.assertEqual(label, "2026-06-19 融资信息")
        self.assertEqual([item["content_id"] for item in items], [previous.content_id])
        self.assertEqual(empty_window_date, "2026-06-18")
        self.assertEqual(empty_label, "2026-06-18 融资信息")
        self.assertEqual(empty_items, [])
    def test_reprocessing_replaces_financing_relevance(self) -> None:
        item = self._item("AI融资新闻", True)
        attach_tag(self.db, item, "ai_financing_relevance", "none", "llm")
        self.db.flush()

        CrawlService()._apply_llm_data(
            self.db,
            item,
            {
                "ai_related": True,
                "content_type": "financing",
                "ai_financing_relevance": "high",
            },
        )
        self.db.flush()

        values = self.db.scalars(
            select(ContentTag.tag_value).where(
                ContentTag.content_id == item.content_id,
                ContentTag.tag_key == "ai_financing_relevance",
            )
        ).all()
        self.assertEqual(values, ["high"])

    @patch("ai_agent.services.db_now", return_value=datetime(2026, 6, 17, 9, 0))
    @patch("ai_agent.services.LLMService.classify_ai_financing_relevance")
    @patch("ai_agent.services.LLMService.ai_financing_relevance_ready", return_value=True)
    @patch("ai_agent.services.LLMService.content_metadata_ready", return_value=False)
    def test_identify_this_week_marks_llm_high_relevance(
        self,
        _metadata_ready: Mock,
        _financing_ready: Mock,
        classify: Mock,
        _now: Mock,
    ) -> None:
        item = self._item("具身智能公司灵巧手完成A轮融资", None)
        item.crawl_time = datetime(2026, 6, 16, 10, 0)
        item.summary = "灵巧手完成超亿元A轮融资，由测试资本领投。"
        classify.return_value = {
            "ai_related": True,
            "content_type": "financing",
            "ai_financing_relevance": "high",
            "entities": [
                {"type": "company", "name": "灵巧手"},
                {"type": "investor", "name": "测试资本"},
            ],
        }
        self.db.commit()

        result = FinancingService().identify_this_week(self.db)

        values = self.db.scalars(
            select(ContentTag.tag_value).where(
                ContentTag.content_id == item.content_id,
                ContentTag.tag_key == "ai_financing_relevance",
            )
        ).all()
        self.assertEqual(result["high"], 1)
        self.assertEqual(values, ["high"])

    @patch("ai_agent.services.db_now", return_value=datetime(2026, 6, 17, 9, 0))
    def test_previous_week_report_dedupes_events_and_writes_markdown(self, _now: Mock) -> None:
        first = self._item("灵巧手完成A轮融资，金额超亿元", True)
        first.crawl_time = datetime(2026, 6, 10, 9, 0)
        first.summary = "灵巧手是一家具身智能创业公司，完成超亿元A轮融资，由测试资本领投。"
        attach_tag(self.db, first, "content_type", "financing", "llm")
        attach_tag(self.db, first, "ai_financing_relevance", "high", "llm")
        attach_entity(self.db, first, "company", "灵巧手", "llm")
        attach_entity(self.db, first, "investor", "测试资本", "llm")

        duplicate = self._item("灵巧手获超亿元A轮融资", True)
        duplicate.crawl_time = datetime(2026, 6, 11, 9, 0)
        duplicate.summary = "具身智能公司灵巧手获超亿元A轮融资，测试资本领投。"
        attach_tag(self.db, duplicate, "content_type", "financing", "llm")
        attach_tag(self.db, duplicate, "ai_financing_relevance", "high", "llm")
        attach_entity(self.db, duplicate, "company", "灵巧手", "llm")
        attach_entity(self.db, duplicate, "investor", "测试资本", "llm")
        self.db.commit()

        llm_markdown = """# 上周融资报告

灵巧手完成A轮融资，金额超亿元，由测试资本参与。
- https://example.com/灵巧手完成A轮融资，金额超亿元
"""

        output_path = Path.cwd() / "test-output" / "weekly-report-test.md"
        with patch.object(Path, "mkdir"), patch.object(Path, "write_text") as write_text, patch.object(
            LLMService, "generate_financing_report", return_value=llm_markdown
        ) as generate_report:
            result = FinancingService().generate_previous_week_report(self.db, str(output_path))
        report_text = result["markdown"]

        self.assertEqual(result["event_count"], 1)
        write_text.assert_called_once_with(report_text, encoding="utf-8")
        self.assertIn("灵巧手", report_text)
        self.assertIn("A轮", report_text)
        self.assertIn("超亿元", report_text)
        self.assertIn("测试资本", report_text)
        _, task_name, payload = generate_report.call_args.args
        self.assertEqual(task_name, "generate_previous_week_financing_report")
        self.assertEqual(payload["period"]["start_date"], "2026-06-08")
        self.assertEqual(payload["period"]["end_date"], "2026-06-14")
        self.assertEqual(len(payload["news_items"]), 1)
        event = payload["news_items"][0]
        self.assertEqual(event["related_count"], 1)
        self.assertEqual(len(event["related_reports"]), 2)
        self.assertTrue(all(item["url"].startswith("https://example.com/") for item in event["related_reports"]))

    @patch("ai_agent.services.db_now", return_value=datetime(2026, 6, 17, 9, 0))
    def test_previous_week_report_surfaces_llm_failure_without_writing_file(self, _now: Mock) -> None:
        item = self._item("Fallback AI company raises Series A", True)
        item.crawl_time = datetime(2026, 6, 10, 9, 0)
        item.summary = "Fallback AI company completed a Series A financing led by Test Capital."
        attach_tag(self.db, item, "content_type", "financing", "llm")
        attach_tag(self.db, item, "ai_financing_relevance", "high", "llm")
        self.db.commit()

        output_path = Path.cwd() / "test-output" / "weekly-report-fallback.md"
        with patch.object(Path, "mkdir") as mkdir, patch.object(Path, "write_text") as write_text, patch.object(
            LLMService, "generate_financing_report", side_effect=RuntimeError("model returned empty report")
        ), self.assertRaisesRegex(RuntimeError, "model returned empty report"):
            FinancingService().generate_previous_week_report(self.db, str(output_path))

        mkdir.assert_not_called()
        write_text.assert_not_called()

    @patch("ai_agent.services.db_now", return_value=datetime(2026, 6, 17, 9, 0))
    def test_current_week_report_ends_on_today_and_excludes_other_weeks(self, _now: Mock) -> None:
        previous = self._item("旧周公司完成融资", True)
        previous.crawl_time = datetime(2026, 6, 14, 9, 0)
        previous.summary = "旧周公司在上一周完成融资。"

        current = self._item("本周公司完成A轮融资", True)
        current.crawl_time = datetime(2026, 6, 16, 9, 0)
        current.summary = "本周公司在本周完成A轮融资。"

        future = self._item("未来公司完成融资", True)
        future.crawl_time = datetime(2026, 6, 18, 9, 0)
        future.summary = "未来公司在程序打开日之后完成融资。"

        for item in (previous, current, future):
            attach_tag(self.db, item, "content_type", "financing", "llm")
            attach_tag(self.db, item, "ai_financing_relevance", "high", "llm")
        self.db.commit()

        llm_markdown = "# 本周融资报告\n\n本周公司完成A轮融资。\n"
        output_path = Path.cwd() / "test-output" / "current-week-report-test.md"
        with patch.object(Path, "mkdir"), patch.object(Path, "write_text") as write_text, patch.object(
            LLMService, "generate_financing_report", return_value=llm_markdown
        ) as generate_report:
            result = FinancingService().generate_current_week_report(self.db, str(output_path))
        report_text = result["markdown"]

        self.assertEqual(result["week_start"], "2026-06-15")
        self.assertEqual(result["week_end"], "2026-06-17")
        self.assertEqual(result["period_label"], "本周")
        self.assertEqual(result["raw_count"], 1)
        self.assertEqual(result["event_count"], 1)
        self.assertIn("本周公司", report_text)
        self.assertNotIn("旧周公司", report_text)
        self.assertNotIn("未来公司", report_text)
        write_text.assert_called_once_with(report_text, encoding="utf-8")
        _, task_name, payload = generate_report.call_args.args
        self.assertEqual(task_name, "generate_current_week_financing_report")
        self.assertEqual(payload["period"]["start_date"], "2026-06-15")
        self.assertEqual(payload["period"]["end_date"], "2026-06-17")
        self.assertEqual([item["title"] for item in payload["news_items"]], ["本周公司完成A轮融资"])

    @patch("ai_agent.api_v1.subprocess.run")
    def test_browser_directory_selector_returns_selected_path(self, run: Mock) -> None:
        run.return_value = Mock(returncode=0, stdout="C:\\Reports\n", stderr="")

        result = select_local_directory({"initial_path": "C:\\Existing"})

        self.assertEqual(result, {"path": "C:\\Reports"})
        self.assertEqual(run.call_args.kwargs["env"]["VC_NEWS_INITIAL_DIR"], "C:\\Existing")

    @patch("ai_agent.services.time.sleep")
    def test_llm_call_retries_transient_network_error(self, _sleep: Mock) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        config = LLMConfig(
            config_name="test",
            provider_type="openai_compatible",
            max_retries=1,
            timeout_seconds=10,
        )

        with patch("ai_agent.services.requests.post", side_effect=[requests.ConnectionError("temporary"), response]) as retried_post:
            result = LLMService()._call_model(
                config,
                "https://example.com/v1",
                "key",
                "model",
                "prompt",
                "{}",
                concurrency_limit=1,
            )
            retried_post.side_effect = [response]
            plain_result = LLMService()._call_model(
                config,
                "https://example.com/v1",
                "key",
                "model",
                "prompt",
                "{}",
                concurrency_limit=1,
                require_json=False,
            )

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(plain_result, '{"ok": true}')
        self.assertEqual(retried_post.call_count, 3)
        self.assertIn("response_format", retried_post.call_args_list[1].kwargs["json"])
        self.assertNotIn("response_format", retried_post.call_args.kwargs["json"])


if __name__ == "__main__":
    unittest.main()
