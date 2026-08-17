from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_agent.daily_report_llm import enrich_daily_report
from ai_agent.database import Base
from ai_agent.models import LLMConfig, LLMLog, LLMTask, Prompt
from ai_agent.security import encrypt_value
from ai_agent.seed import seed_all


def _report() -> dict:
    return {
        "schema_version": "1.0",
        "template_version": "daily-v1",
        "report_date": "2026-08-17",
        "generated_at": "2026-08-17T10:00:00+08:00",
        "headline": "AI VC Daily",
        "executive_summary": "确定性摘要",
        "stats": {
            "sources_attempted": 1,
            "sources_succeeded": 1,
            "raw_items": 1,
            "included_items": 1,
            "failed_sources": 0,
        },
        "sections": [
            {
                "key": "technology",
                "title": "技术进展",
                "groups": [
                    {
                        "title": "基础模型",
                        "items": [
                            {
                                "content_id": 1,
                                "title": "模型发布",
                                "summary": "原始摘要",
                                "why_it_matters": "原始提示",
                                "source": "可信来源",
                                "url": "https://source.example/article",
                                "published_at": "2026-08-17T09:00:00+08:00",
                                "category": "technology",
                                "item_type": "content",
                            }
                        ],
                    }
                ],
            },
            {"key": "industry", "title": "产业新闻", "groups": []},
            {"key": "funding", "title": "融资新闻", "groups": []},
        ],
        "warnings": [],
        "generation_mode": "deterministic",
    }


class DailyReportLLMTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.config = LLMConfig(
            config_name="Fake provider",
            provider_type="openai",
            encrypted_base_url=encrypt_value("https://llm.example/v1"),
            encrypted_api_key=encrypt_value("test-api-key"),
            encrypted_model_name=encrypt_value("test-model"),
            enabled=True,
        )
        self.prompt = Prompt(
            prompt_name="Daily report prompt",
            task_name="generate_daily_investment_report",
            prompt_text="Return JSON",
            enabled=True,
        )
        self.db.add_all([self.config, self.prompt])
        self.db.flush()
        self.task = LLMTask(
            task_name="generate_daily_investment_report",
            llm_config_id=self.config.llm_config_id,
            prompt_id=self.prompt.prompt_id,
            enabled=True,
        )
        self.db.add(self.task)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_seed_binds_first_enabled_provider_and_preserves_prompt_edit(self) -> None:
        # Use a fresh database so seed defaults, rather than the setUp task,
        # exercise the same binding path used by a real installation.
        self.db.query(LLMTask).delete()
        self.db.query(Prompt).delete()
        self.db.commit()
        seed_all(self.db)

        task = self.db.scalar(
            select(LLMTask).where(LLMTask.task_name == "generate_daily_investment_report")
        )
        self.assertIsNotNone(task)
        self.assertEqual(task.llm_config_id, self.config.llm_config_id)
        prompt = self.db.get(Prompt, task.prompt_id)
        self.assertIsNotNone(prompt)
        prompt.prompt_text = "用户编辑过的日报 Prompt"
        self.db.commit()

        seed_all(self.db)
        self.assertEqual(self.db.get(Prompt, prompt.prompt_id).prompt_text, "用户编辑过的日报 Prompt")

    def test_success_merges_whitelisted_fields_and_ignores_forged_url(self) -> None:
        output = {
            "executive_summary": "LLM 摘要",
            "item_updates": [
                {
                    "content_id": 1,
                    "summary": "增强后的摘要",
                    "why_it_matters": "增强后的提示",
                    "category": "industry",
                    "theme": "企业服务",
                    "url": "https://attacker.example/forged",
                    "source": "伪造来源",
                }
            ],
        }
        with patch("ai_agent.services.LLMService._call_model", return_value=json.dumps(output)):
            result = enrich_daily_report(self.db, _report())

        self.assertEqual(result.generation_mode, "llm")
        self.assertEqual(result.report["executive_summary"], "LLM 摘要")
        section = next(section for section in result.report["sections"] if section["key"] == "industry")
        item = section["groups"][0]["items"][0]
        self.assertEqual(item["summary"], "增强后的摘要")
        self.assertEqual(item["why_it_matters"], "增强后的提示")
        self.assertEqual(item["url"], "https://source.example/article")
        self.assertEqual(item["source"], "可信来源")
        self.assertEqual(result.model_name, "test-model")
        log = self.db.scalar(
            select(LLMLog).where(LLMLog.task_name == "generate_daily_investment_report")
        )
        self.assertIsNotNone(log)
        self.assertEqual(log.status, "success")
        self.assertEqual(log.model_name, "test-model")
        self.assertEqual(log.prompt_id, self.prompt.prompt_id)

    def test_unknown_id_returns_original_report_with_partial_warning(self) -> None:
        output = {
            "executive_summary": "不应被采用",
            "item_updates": [{"content_id": 999, "summary": "未知条目"}],
        }
        original = _report()
        with patch("ai_agent.services.LLMService._call_model", return_value=json.dumps(output)):
            result = enrich_daily_report(self.db, original)

        self.assertEqual(result.generation_mode, "partial")
        self.assertEqual(result.report["executive_summary"], original["executive_summary"])
        self.assertEqual(result.report["sections"], original["sections"])
        self.assertTrue(any("未知条目" in warning for warning in result.warnings))

    def test_provider_failure_returns_original_report_with_partial_warning(self) -> None:
        original = _report()
        with patch(
            "ai_agent.services.LLMService._call_model",
            side_effect=RuntimeError("fake provider down"),
        ):
            result = enrich_daily_report(self.db, original)

        self.assertEqual(result.generation_mode, "partial")
        self.assertEqual(result.report["sections"], original["sections"])
        self.assertTrue(any("调用失败" in warning for warning in result.warnings))
        log = self.db.scalar(
            select(LLMLog).where(LLMLog.task_name == "generate_daily_investment_report")
        )
        self.assertIsNotNone(log)
        self.assertEqual(log.status, "failed")

    def test_missing_binding_keeps_deterministic_status(self) -> None:
        self.db.delete(self.task)
        self.db.commit()
        original = _report()
        result = enrich_daily_report(self.db, original)

        self.assertEqual(result.generation_mode, "deterministic")
        self.assertEqual(result.report["sections"], original["sections"])
        self.assertEqual(result.warnings, [])


if __name__ == "__main__":
    unittest.main()
