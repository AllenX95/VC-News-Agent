"""LLM enrichment for the deterministic daily investment report.

The report data builder owns provenance and the HTML renderer owns display.  This
module is intentionally the only seam between those two components and an LLM:
the model may suggest editorial text and classification changes, but it can
never introduce a source URL, source name, or new report identifier.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from .report_data import REPORT_CATEGORIES, validate_report_data
from .services import LLMService


DAILY_REPORT_TASK_NAME = "generate_daily_investment_report"
_VALID_CATEGORIES = frozenset(key for key, _title in REPORT_CATEGORIES)
_ALLOWED_UPDATE_FIELDS = frozenset({"summary", "why_it_matters", "category", "theme"})
_IDENTIFIER_FIELDS = frozenset({"content_id", "event_id"})


@dataclass
class DailyReportEnrichmentResult:
    """Outcome of one best-effort daily report enrichment attempt.

    ``report`` is always renderable when the deterministic input was renderable.
    Metadata intentionally contains only database IDs and the model name; no
    decrypted API key, endpoint, request header, or prompt body is exposed.
    """

    report: dict[str, Any]
    generation_mode: str
    warnings: list[str] = field(default_factory=list)
    model_name: str | None = None
    prompt_id: int | None = None
    llm_config_id: int | None = None
    task_name: str = DAILY_REPORT_TASK_NAME

    @property
    def enriched_report(self) -> dict[str, Any]:
        """Alias useful to adapters that distinguish input from output."""

        return self.report

    @property
    def metadata(self) -> dict[str, Any]:
        """Return non-sensitive task metadata for a manifest or UI."""

        return {
            "task_name": self.task_name,
            "model_name": self.model_name,
            "prompt_id": self.prompt_id,
            "llm_config_id": self.llm_config_id,
        }


def _append_warning(report: dict[str, Any], warning: str) -> None:
    existing = report.get("warnings")
    values = list(existing) if isinstance(existing, list) else []
    values.extend(str(value) for value in [warning] if str(value).strip())
    report["warnings"] = list(dict.fromkeys(values))


def _result(
    deterministic_report: dict[str, Any],
    generation_mode: str,
    warning: str | None = None,
    *,
    model_name: str | None = None,
    prompt_id: int | None = None,
    llm_config_id: int | None = None,
) -> DailyReportEnrichmentResult:
    report = deepcopy(deterministic_report)
    report["generation_mode"] = generation_mode
    warnings: list[str] = []
    if warning:
        _append_warning(report, warning)
        warnings.append(warning)
    else:
        existing = report.get("warnings")
        if isinstance(existing, list):
            warnings = [str(value) for value in existing if str(value).strip()]
    return DailyReportEnrichmentResult(
        report=report,
        generation_mode=generation_mode,
        warnings=warnings,
        model_name=model_name,
        prompt_id=prompt_id,
        llm_config_id=llm_config_id,
    )


def _item_key(item: dict[str, Any]) -> tuple[str, int] | None:
    content_id = item.get("content_id")
    event_id = item.get("event_id")
    has_content = isinstance(content_id, int) and not isinstance(content_id, bool)
    has_event = isinstance(event_id, int) and not isinstance(event_id, bool)
    if has_content == has_event:
        return None
    if has_content and content_id > 0:
        return ("content_id", content_id)
    if has_event and event_id > 0:
        return ("event_id", event_id)
    return None


def _report_items(report: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    items: dict[tuple[str, int], dict[str, Any]] = {}
    for section in report.get("sections", []) if isinstance(report.get("sections"), list) else []:
        if not isinstance(section, dict):
            continue
        for group in section.get("groups", []) if isinstance(section.get("groups"), list) else []:
            if not isinstance(group, dict):
                continue
            for item in group.get("items", []) if isinstance(group.get("items"), list) else []:
                if not isinstance(item, dict):
                    continue
                key = _item_key(item)
                if key is not None:
                    items[key] = item
    return items


def _validate_model_output(
    output: Any,
    known_items: dict[tuple[str, int], dict[str, Any]],
) -> tuple[str | None, list[tuple[tuple[str, int], dict[str, Any]]], str | None]:
    """Validate only the control structure; unknown fields are ignored.

    A known item may carry an untrusted ``url`` or ``source`` field.  Those
    fields deliberately do not participate in the returned update dictionary.
    The update can therefore safely be merged while preserving deterministic
    provenance.
    """

    if not isinstance(output, dict):
        return None, [], "daily report LLM 返回值不是 JSON 对象"
    if "executive_summary" not in output or "item_updates" not in output:
        return None, [], "daily report LLM 返回值缺少 executive_summary 或 item_updates"

    executive_summary = output.get("executive_summary")
    updates = output.get("item_updates")
    if not isinstance(executive_summary, str) or not isinstance(updates, list):
        return None, [], "daily report LLM 返回值字段类型非法"

    seen: set[tuple[str, int]] = set()
    validated: list[tuple[tuple[str, int], dict[str, Any]]] = []
    for index, raw_update in enumerate(updates):
        if not isinstance(raw_update, dict):
            return None, [], f"daily report LLM item_updates[{index}] 不是对象"
        identifiers = [field for field in _IDENTIFIER_FIELDS if field in raw_update]
        if len(identifiers) != 1:
            return None, [], f"daily report LLM item_updates[{index}] 必须包含一个 content_id 或 event_id"
        identifier = identifiers[0]
        value = raw_update.get(identifier)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return None, [], f"daily report LLM item_updates[{index}] ID 非法"
        key = (identifier, value)
        if key not in known_items:
            return None, [], f"daily report LLM 返回未知条目 ID：{identifier}={value}"
        if key in seen:
            return None, [], f"daily report LLM 重复更新条目：{identifier}={value}"
        seen.add(key)

        safe_update: dict[str, Any] = {}
        for field_name in _ALLOWED_UPDATE_FIELDS:
            if field_name not in raw_update:
                continue
            field_value = raw_update[field_name]
            if field_name in {"summary", "why_it_matters", "theme"}:
                if not isinstance(field_value, str):
                    return None, [], f"daily report LLM item_updates[{index}].{field_name} 类型非法"
                if field_name == "theme" and not field_value.strip():
                    return None, [], f"daily report LLM item_updates[{index}].theme 不能为空"
                safe_update[field_name] = field_value.strip() if field_name != "summary" else field_value.strip()
            elif field_name == "category":
                if not isinstance(field_value, str) or field_value not in _VALID_CATEGORIES:
                    return None, [], f"daily report LLM item_updates[{index}].category 非法"
                safe_update[field_name] = field_value
        # A URL/source or other unauthorized field is intentionally discarded.
        validated.append((key, safe_update))

    return executive_summary, validated, None


def _sort_item(item: dict[str, Any]) -> tuple[str, str, int]:
    identifier = item.get("content_id", item.get("event_id", 0))
    return (str(item.get("published_at") or ""), str(item.get("title") or ""), int(identifier or 0))


def _rebuild_sections(report: dict[str, Any], updates: list[tuple[tuple[str, int], dict[str, Any]]]) -> None:
    update_map = dict(updates)
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        key: {} for key, _title in REPORT_CATEGORIES
    }
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        section_key = section.get("key")
        if section_key not in _VALID_CATEGORIES:
            continue
        for group in section.get("groups", []):
            if not isinstance(group, dict):
                continue
            original_theme = group.get("title")
            if not isinstance(original_theme, str) or not original_theme.strip():
                original_theme = "其他"
            for original_item in group.get("items", []):
                if not isinstance(original_item, dict):
                    continue
                item = deepcopy(original_item)
                key = _item_key(item)
                update = update_map.get(key, {}) if key is not None else {}
                category = update.get("category", section_key)
                theme = update.get("theme", original_theme)
                item.update({field: value for field, value in update.items() if field != "theme"})
                item["category"] = category
                bucket = grouped[category].setdefault(theme, [])
                bucket.append(item)

    sections: list[dict[str, Any]] = []
    for key, title in REPORT_CATEGORIES:
        groups = [
            {"title": theme, "items": sorted(items, key=_sort_item)}
            for theme, items in sorted(grouped[key].items(), key=lambda value: value[0])
        ]
        sections.append({"key": key, "title": title, "groups": groups})
    report["sections"] = sections


def enrich_daily_report(
    db: Session,
    deterministic_report: dict[str, Any],
) -> DailyReportEnrichmentResult:
    """Best-effort LLM enrichment for one already-built daily report.

    This interface never raises for missing configuration, provider failures or
    untrusted model output.  It returns a deterministic or partial result so a
    caller can still render and deliver the daily report.
    """

    base_report = deepcopy(deterministic_report)
    deterministic_errors = validate_report_data(base_report)
    if deterministic_errors:
        warning = "daily report deterministic data 校验失败：" + "; ".join(deterministic_errors[:3])
        return _result(base_report, "partial", warning)

    service = LLMService()
    assets = service._task_assets(db, DAILY_REPORT_TASK_NAME)
    if not assets:
        return _result(base_report, "deterministic")
    task, config, prompt, _base_url, _api_key, model_name = assets

    payload = {"report": deepcopy(base_report)}
    known_items = _report_items(base_report)
    try:
        output = service.call_structured_json_task(db, DAILY_REPORT_TASK_NAME, payload)
    except Exception as exc:  # noqa: BLE001
        warning = f"daily report LLM 调用失败，已降级为 partial：{str(exc)[:220]}"
        return _result(
            base_report,
            "partial",
            warning,
            model_name=model_name,
            prompt_id=prompt.prompt_id,
            llm_config_id=config.llm_config_id,
        )

    executive_summary, updates, validation_error = _validate_model_output(output, known_items)
    if validation_error:
        return _result(
            base_report,
            "partial",
            validation_error,
            model_name=model_name,
            prompt_id=prompt.prompt_id,
            llm_config_id=config.llm_config_id,
        )

    enriched = deepcopy(base_report)
    if executive_summary is not None and executive_summary.strip():
        enriched["executive_summary"] = executive_summary.strip()
    _rebuild_sections(enriched, updates)
    schema_errors = validate_report_data(enriched)
    if schema_errors:
        warning = "daily report LLM 合并后 schema 校验失败：" + "; ".join(schema_errors[:3])
        return _result(
            base_report,
            "partial",
            warning,
            model_name=model_name,
            prompt_id=prompt.prompt_id,
            llm_config_id=config.llm_config_id,
        )

    enriched["generation_mode"] = "llm"
    warnings = enriched.get("warnings") if isinstance(enriched.get("warnings"), list) else []
    return DailyReportEnrichmentResult(
        report=enriched,
        generation_mode="llm",
        warnings=[str(value) for value in warnings if str(value).strip()],
        model_name=model_name,
        prompt_id=prompt.prompt_id,
        llm_config_id=config.llm_config_id,
    )


__all__ = ["DAILY_REPORT_TASK_NAME", "DailyReportEnrichmentResult", "enrich_daily_report"]
