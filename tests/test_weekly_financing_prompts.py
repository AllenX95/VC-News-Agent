from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_agent.api_v1 import create_llm_config, delete_llm_config, llm, update_llm_config, update_prompt
from ai_agent.database import Base
from ai_agent.models import LLMConfig, LLMLog, LLMTask, Prompt
from ai_agent.seed import seed_all
from ai_agent.security import decrypt_value
from ai_agent.services import LLMService


class WeeklyFinancingPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_seed_creates_weekly_tasks_and_preserves_manual_prompt_edits(self) -> None:
        seed_all(self.db)

        current_task = self.db.scalar(
            select(LLMTask).where(LLMTask.task_name == "generate_current_week_financing_report")
        )
        previous_task = self.db.scalar(
            select(LLMTask).where(LLMTask.task_name == "generate_previous_week_financing_report")
        )
        self.assertIsNotNone(current_task)
        self.assertIsNotNone(previous_task)

        current_prompt = self.db.get(Prompt, current_task.prompt_id)
        previous_prompt = self.db.get(Prompt, previous_task.prompt_id)
        self.assertIn("# 本周融资报告", current_prompt.prompt_text)
        self.assertIn("# 上周融资报告", previous_prompt.prompt_text)
        self.assertIn("不得改写、缩短、生成新链接", current_prompt.prompt_text)

        update_prompt(
            current_prompt.prompt_id,
            {
                "prompt_name": current_prompt.prompt_name,
                "task_name": current_prompt.task_name,
                "prompt_text": "用户手动编辑的本周融资 Prompt",
                "enabled": True,
            },
            self.db,
        )
        seed_all(self.db)

        refreshed_prompt = self.db.get(Prompt, current_prompt.prompt_id)
        self.assertEqual(refreshed_prompt.prompt_text, "用户手动编辑的本周融资 Prompt")

    def test_llm_page_repairs_missing_weekly_prompts_and_tasks(self) -> None:
        result = llm(self.db)

        prompts_by_task = {prompt["task_name"]: prompt for prompt in result["prompts"]}
        task_rows = {row["task_name"]: row for row in result["task_rows"]}
        for task_name in (
            "generate_current_week_financing_report",
            "generate_previous_week_financing_report",
        ):
            self.assertIn(task_name, prompts_by_task)
            self.assertIsNotNone(task_rows[task_name]["task"])
            self.assertEqual(task_rows[task_name]["task"]["prompt_id"], prompts_by_task[task_name]["prompt_id"])


    def test_generate_financing_report_uses_bound_prompt_and_model(self) -> None:
        config = LLMConfig(config_name="Report model", provider_type="openai", enabled=True)
        prompt = Prompt(
            prompt_name="Current week report",
            task_name="generate_current_week_financing_report",
            prompt_text="System financing report prompt",
            enabled=True,
        )
        self.db.add_all([config, prompt])
        self.db.flush()
        task = LLMTask(
            task_name="generate_current_week_financing_report",
            llm_config_id=config.llm_config_id,
            prompt_id=prompt.prompt_id,
            enabled=True,
        )
        self.db.add(task)
        self.db.flush()

        assets = (task, config, prompt, "https://example.com/v1", "test-key", "test-model")
        payload = {
            "period": {"start_date": "2026-06-15", "end_date": "2026-06-17"},
            "news_items": [{"title": "融资新闻", "url": "https://example.com/article"}],
        }
        service = LLMService()
        with patch.object(service, "_task_assets", return_value=assets), patch.object(
            service, "_call_model", return_value="# 本周融资报告"
        ) as call_model:
            result = service.generate_financing_report(
                self.db,
                "generate_current_week_financing_report",
                payload,
            )
            call_model.return_value = '{"report":"# Wrapped report"}'
            wrapped_result = service.generate_financing_report(
                self.db,
                "generate_current_week_financing_report",
                payload,
            )

        self.assertEqual(result, "# 本周融资报告")
        call_args = call_model.call_args.args
        self.assertEqual(call_args[3], "test-model")
        self.assertEqual(call_args[4], "System financing report prompt")
        self.assertEqual(json.loads(call_args[5]), payload)
        self.assertFalse(call_model.call_args.kwargs["require_json"])
        self.assertEqual(wrapped_result, "# Wrapped report")
        log = self.db.scalar(select(LLMLog).where(LLMLog.task_name == task.task_name))
        self.assertEqual(log.model_name, "test-model")
        self.assertEqual(log.status, "success")

    def test_generate_financing_report_returns_output_when_log_write_fails(self) -> None:
        config = LLMConfig(config_name="Report model", provider_type="openai", enabled=True)
        prompt = Prompt(
            prompt_name="Current week report",
            task_name="generate_current_week_financing_report",
            prompt_text="System financing report prompt",
            enabled=True,
        )
        self.db.add_all([config, prompt])
        self.db.flush()
        task = LLMTask(
            task_name="generate_current_week_financing_report",
            llm_config_id=config.llm_config_id,
            prompt_id=prompt.prompt_id,
            enabled=True,
        )
        self.db.add(task)
        self.db.flush()

        assets = (task, config, prompt, "https://example.com/v1", "test-key", "test-model")
        service = LLMService()
        with patch.object(service, "_task_assets", return_value=assets), patch.object(
            service, "_call_model", return_value="# Report"
        ), patch("ai_agent.services.get_int_setting", return_value=2), patch.object(
            self.db, "flush", side_effect=RuntimeError("database is locked")
        ):
            result = service.generate_financing_report(
                self.db,
                "generate_current_week_financing_report",
                {"news_items": []},
            )

        self.assertEqual(result, "# Report")

    def test_llm_config_provider_alias_is_normalized_to_openai(self) -> None:
        config = LLMConfig(config_name="DeepSeek Flash", provider_type="openai_compatible", enabled=True)
        self.db.add(config)
        self.db.commit()

        result = llm(self.db)
        payload = next(item for item in result["configs"] if item["llm_config_id"] == config.llm_config_id)

        self.assertEqual(payload["provider_type"], "openai")
        self.assertEqual(self.db.get(LLMConfig, config.llm_config_id).provider_type, "openai")

    def test_llm_config_can_be_updated_without_reentering_api_key(self) -> None:
        created = create_llm_config(
            {
                "config_name": "DeepSeek Flash",
                "provider_type": "openai_compatible",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "original-key",
                "model_name": "deepseek-v4-flash",
                "timeout_seconds": 30,
                "max_retries": 2,
                "context_window_tokens": 1000000,
                "enabled": True,
            },
            self.db,
        )["config"]

        updated = update_llm_config(
            created["llm_config_id"],
            {
                "config_name": "DeepSeek Flash Updated",
                "provider_type": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "",
                "model_name": "deepseek-v4-flash",
                "timeout_seconds": 45,
                "max_retries": 1,
                "context_window_tokens": 900000,
                "enabled": False,
            },
            self.db,
        )["config"]
        config = self.db.get(LLMConfig, created["llm_config_id"])

        self.assertEqual(updated["provider_type"], "openai")
        self.assertEqual(updated["config_name"], "DeepSeek Flash Updated")
        self.assertEqual(updated["timeout_seconds"], 45)
        self.assertEqual(updated["context_window_tokens"], 900000)
        self.assertFalse(updated["enabled"])
        self.assertEqual(decrypt_value(config.encrypted_api_key), "original-key")

    def test_delete_llm_config_deletes_unreferenced_and_disables_logged_config(self) -> None:
        unreferenced = LLMConfig(config_name="Unused", provider_type="openai", enabled=True)
        logged = LLMConfig(config_name="Logged", provider_type="openai", enabled=True)
        self.db.add_all([unreferenced, logged])
        self.db.flush()
        task = LLMTask(task_name="temporary_task", llm_config_id=logged.llm_config_id, enabled=True)
        self.db.add(task)
        self.db.add(LLMLog(task_name="connection_test", llm_config_id=logged.llm_config_id, status="success"))
        self.db.commit()

        deleted = delete_llm_config(unreferenced.llm_config_id, self.db)
        disabled = delete_llm_config(logged.llm_config_id, self.db)

        self.assertEqual(deleted["action"], "deleted")
        self.assertIsNone(self.db.get(LLMConfig, unreferenced.llm_config_id))
        self.assertEqual(disabled["action"], "disabled")
        self.assertFalse(self.db.get(LLMConfig, logged.llm_config_id).enabled)
        self.assertIsNone(self.db.get(LLMTask, task.llm_task_id).llm_config_id)


if __name__ == "__main__":
    unittest.main()
