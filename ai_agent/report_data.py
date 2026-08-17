"""Deterministic contract for the daily investment intelligence report.

The first daily-report slice deliberately does not call an LLM.  It turns the
records already persisted by the agent into a small, traceable data contract
which can later be enriched by an LLM adapter without changing the renderer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
import json
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from .config import TZ
from .models import ContentItem, EventContent, FinancingEvent


SCHEMA_VERSION = "1.0"
TEMPLATE_VERSION = "daily-v1"
REPORT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("technology", "技术进展"),
    ("industry", "产业新闻"),
    ("funding", "融资新闻"),
)

_EXACT_TIME_STATUSES = {"exact", "verified", "parsed"}
_FUNDING_TAGS = {"financing", "funding", "investment", "m_and_a", "acquisition"}
# ``venture_media`` is a broad source category in the existing seed data; it
# must not turn every article from a venture publication into a financing item.
# Only an explicitly financing-oriented source category is a sufficient source
# signal.  Generic venture media still needs a financing term in its content.
_FUNDING_SOURCE_PARTS = ("financ", "fund", "investment", "acquisition", "m_and_a", "融资", "投资")
_TECH_SOURCE_PARTS = (
    "ai",
    "tech",
    "research",
    "github",
    "hacker",
    "model",
    "技术",
    "科技",
    "研究",
)
_FUNDING_TERMS = (
    "融资",
    "获投",
    "获得投资",
    "完成投资",
    "战略投资",
    "融资轮",
    "种子轮",
    "天使轮",
    "pre-seed",
    "pre seed",
    "series a",
    "series b",
    "series c",
    "series d",
    "series e",
    "raised",
    "raises",
    "funding",
    "investment",
    "invests",
    "投资人",
    "领投",
    "跟投",
    "并购",
    "收购",
    "估值",
)
_TECH_TERMS = (
    "人工智能",
    "大模型",
    "基础模型",
    "语言模型",
    "llm",
    "模型",
    "agent",
    "智能体",
    "ai infra",
    "ai基础设施",
    "算力",
    "gpu",
    "芯片",
    "半导体",
    "多模态",
    "机器人",
    "具身智能",
    "自动驾驶",
    "算法",
    "推理",
    "训练",
    "benchmark",
    "开源",
    "open source",
    "robotics",
    "semiconductor",
)
_TECH_THEME_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("基础模型", ("大模型", "基础模型", "语言模型", "llm", "model")),
    ("智能体", ("agent", "智能体")),
    ("AI基础设施", ("ai infra", "ai基础设施", "算力", "inference", "推理", "训练")),
    ("芯片与算力", ("芯片", "半导体", "gpu", "semiconductor")),
    ("具身智能", ("具身智能", "机器人", "robotics", "自动驾驶")),
    ("多模态", ("多模态", "multimodal")),
)
_INDUSTRY_THEME_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("政策与监管", ("政策", "监管", "regulation", "法案")),
    ("企业服务", ("企业服务", "saas", "b2b", "enterprise")),
    ("市场与公司动态", ("市场", "商业化", "营收", "公司", "企业")),
)


def _clean_text(value: Any) -> str:
    """Keep source text intact while making card fields compact.

    In particular, this function does not HTML-escape text.  Escaping belongs
    to the Jinja renderer, so the JSON contract remains useful to other
    consumers and HTML escaping can be tested independently.
    """

    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _target_day(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=TZ).date()
        return value.astimezone(TZ).date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("target_date must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid target_date: {value!r}") from exc


def _as_shanghai(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # Existing SQLite records are intentionally stored as local naive
        # datetimes.  Treat them as Asia/Shanghai at the contract boundary.
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def _time_choice(item: ContentItem) -> tuple[datetime | None, str]:
    """Return the report time and a quality marker for a content row."""

    status = _clean_text(getattr(item, "publish_time_status", "")) or "missing"
    publish_time = _as_shanghai(item.publish_time)
    crawl_time = _as_shanghai(item.crawl_time)
    if publish_time is not None and status.lower() in _EXACT_TIME_STATUSES:
        return publish_time, "published_exact"
    if publish_time is not None and status.lower() not in {"missing", "estimated"} and crawl_time is None:
        # Be tolerant of legacy rows whose status was not populated.
        return publish_time, "published"
    if crawl_time is not None:
        return crawl_time, "crawl_time" if publish_time is None else "crawl_time_fallback"
    if publish_time is not None:
        # A malformed legacy row with no crawl time is still more useful with
        # its persisted timestamp than silently disappearing.
        return publish_time, "published_unverified"
    return None, "missing"


def _in_day(value: datetime | None, day: date) -> bool:
    return value is not None and value.date() == day


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _tag_values(item: ContentItem, tag_key: str) -> list[str]:
    values: list[str] = []
    for tag in getattr(item, "tags", []) or []:
        if _clean_text(getattr(tag, "tag_key", "")).lower() == tag_key.lower():
            value = _clean_text(getattr(tag, "tag_value", ""))
            if value and value not in values:
                values.append(value)
    return values


def _text_for(item: ContentItem) -> str:
    return f"{_clean_text(item.title)} {_clean_text(item.summary)}".strip().lower()


def _contains_any(text: str, terms: tuple[str, ...] | list[str] | set[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _classify_content(item: ContentItem) -> str:
    content_types = {value.lower() for value in _tag_values(item, "content_type")}
    if content_types & _FUNDING_TAGS:
        return "funding"

    source_category = _clean_text(getattr(item, "source_category", "")).lower()
    text = _text_for(item)
    if _contains_any(source_category, _FUNDING_SOURCE_PARTS) or _contains_any(text, _FUNDING_TERMS):
        return "funding"
    if _contains_any(source_category, _TECH_SOURCE_PARTS) or _contains_any(text, _TECH_TERMS):
        return "technology"
    return "industry"


def _theme_for(item: ContentItem, category: str) -> str:
    text = _text_for(item)
    for tag in _tag_values(item, "sector"):
        normalized = tag.lower()
        if normalized in {"llm", "ai model"}:
            return "基础模型"
        if "agent" in normalized:
            return "智能体"
        if "infra" in normalized:
            return "AI基础设施"
        if normalized in {"robotics", "embodied ai"}:
            return "具身智能"
        if "semiconductor" in normalized:
            return "芯片与算力"
    terms = _TECH_THEME_TERMS if category == "technology" else _INDUSTRY_THEME_TERMS
    for theme, theme_terms in terms:
        if _contains_any(text, theme_terms):
            return theme
    return "其他技术" if category == "technology" else "其他产业"


def _content_query(db: Session, day: date) -> list[ContentItem]:
    start = datetime.combine(day, time.min)
    end = datetime.combine(day + timedelta(days=1), time.min)
    # The SQL predicate is only an efficient coarse filter.  The final date
    # decision is made in Python after applying publish-time quality rules.
    statement = (
        select(ContentItem)
        .options(selectinload(ContentItem.tags))
        .where(
            or_(
                and_(ContentItem.publish_time.is_not(None), ContentItem.publish_time >= start, ContentItem.publish_time < end),
                and_(ContentItem.crawl_time.is_not(None), ContentItem.crawl_time >= start, ContentItem.crawl_time < end),
            )
        )
        .order_by(ContentItem.content_id.asc())
    )
    return list(db.scalars(statement).all())


def _event_query(db: Session, day: date) -> list[FinancingEvent]:
    statement = (
        select(FinancingEvent)
        .options(selectinload(FinancingEvent.contents).selectinload(EventContent.content))
        .where(FinancingEvent.announced_date == day, FinancingEvent.review_status != "excluded")
        .order_by(FinancingEvent.event_id.asc())
    )
    return list(db.scalars(statement).unique().all())


def _content_item(item: ContentItem, category: str) -> dict[str, Any] | None:
    value, time_quality = _time_choice(item)
    url = _clean_text(getattr(item, "url", "")) or _clean_text(getattr(item, "canonical_url", ""))
    if not url:
        return None
    tags = _tag_values(item, "sector")
    return {
        "content_id": int(item.content_id),
        "title": _clean_text(item.title) or "未命名内容",
        "summary": _clean_text(item.summary) or "暂无摘要，需打开原文核验。",
        "why_it_matters": "基于已入库内容的确定性整理，不代表投资建议。",
        "source": _clean_text(item.source_name) or "未知来源",
        "url": url,
        "published_at": _iso_datetime(value),
        "time_quality": time_quality,
        "tags": tags,
        "confidence": float(item.relevance_confidence) if item.relevance_confidence is not None else None,
        "category": category,
        "item_type": "content",
    }


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        return parsed
    return []


def _event_item(event: FinancingEvent, day: date) -> tuple[dict[str, Any] | None, set[int], set[int]]:
    relations = sorted(
        list(event.contents or []),
        key=lambda relation: (not bool(relation.is_primary_source), int(relation.event_content_id or 0)),
    )
    source_rows: list[dict[str, Any]] = []
    content_ids: set[int] = set()
    source_ids: set[int] = set()
    for relation in relations:
        content = relation.content
        if content is None:
            continue
        content_ids.add(int(content.content_id))
        source_ids.add(int(content.source_id))
        url = _clean_text(getattr(content, "url", "")) or _clean_text(getattr(content, "canonical_url", ""))
        if not url:
            continue
        value, quality = _time_choice(content)
        source_rows.append(
            {
                "content_id": int(content.content_id),
                "title": _clean_text(content.title) or "未命名来源",
                "source": _clean_text(content.source_name) or "未知来源",
                "url": url,
                "published_at": _iso_datetime(value),
                "time_quality": quality,
                "is_primary_source": bool(relation.is_primary_source),
            }
        )
    if not source_rows:
        return None, content_ids, source_ids

    # A URL is persisted data, not a model-produced field.  Keep the first
    # primary source and de-duplicate accidental duplicate links.
    unique_sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for source in source_rows:
        if source["url"] in seen_urls:
            continue
        seen_urls.add(source["url"])
        unique_sources.append(source)
    primary = unique_sources[0]
    announced_at = datetime.combine(event.announced_date or day, time.min, tzinfo=TZ)
    company = _clean_text(event.company_name)
    title = _clean_text(event.event_title) or (f"{company} 融资事件" if company else "融资事件")
    summary = _clean_text(event.event_summary) or title
    amount = _clean_text(event.amount_original)
    round_name = _clean_text(event.financing_round)
    investors = [_clean_text(value) for value in _json_list(event.investors_json) if _clean_text(value)]
    lead_investors = [_clean_text(value) for value in _json_list(event.lead_investors_json) if _clean_text(value)]
    item = {
        "event_id": int(event.event_id),
        "event_type": _clean_text(event.event_type) or "financing",
        "title": title,
        "summary": summary,
        "why_it_matters": "同一融资事件的多篇已入库报道已合并，请通过来源链接核验。",
        "source": primary["source"],
        "url": primary["url"],
        "published_at": _iso_datetime(announced_at),
        "time_quality": "announced_date",
        "company_name": company,
        "financing_round": round_name,
        "amount": amount,
        "amount_original": amount,
        "amount_normalized": event.amount_normalized,
        "currency": _clean_text(event.currency),
        "investors": investors,
        "lead_investors": lead_investors,
        "confidence": float(event.confidence or 0.0),
        "review_status": _clean_text(event.review_status) or "pending",
        "content_ids": sorted(content_ids),
        "sources": unique_sources,
        "category": "funding",
        "item_type": "financing_event",
    }
    return item, content_ids, source_ids


def build_daily_report_data(
    db: Session,
    target_date: str,
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a traceable, deterministic report for one Beijing calendar day."""

    day = _target_day(target_date)
    report_warnings = list(warnings or [])
    contents = [item for item in _content_query(db, day) if _in_day(_time_choice(item)[0], day)]
    events = _event_query(db, day)

    event_items: list[dict[str, Any]] = []
    event_content_ids: set[int] = set()
    event_source_ids: set[int] = set()
    for event in events:
        item, linked_ids, linked_source_ids = _event_item(event, day)
        event_content_ids.update(linked_ids)
        event_source_ids.update(linked_source_ids)
        if item is None:
            report_warnings.append(f"融资事件 #{event.event_id} 没有可用的数据库来源链接，已跳过")
            continue
        event_items.append(item)

    grouped: dict[str, defaultdict[str, list[dict[str, Any]]]] = {
        key: defaultdict(list) for key, _title in REPORT_CATEGORIES
    }
    source_ids: set[int] = set(event_source_ids)
    regular_items: list[dict[str, Any]] = []
    for content in contents:
        if int(content.content_id) in event_content_ids:
            continue
        category = _classify_content(content)
        item = _content_item(content, category)
        if item is None:
            report_warnings.append(f"内容 #{content.content_id} 没有可用的数据库来源链接，已跳过")
            continue
        source_ids.add(int(content.source_id))
        regular_items.append(item)
        grouped[category][_theme_for(content, category)].append(item)

    for item in event_items:
        grouped["funding"]["融资事件"].append(item)

    sections: list[dict[str, Any]] = []
    for key, title in REPORT_CATEGORIES:
        groups = []
        for group_title in sorted(grouped[key]):
            items = grouped[key][group_title]
            items.sort(key=lambda value: (value.get("published_at") or "", value.get("title") or "", value.get("content_id", value.get("event_id", 0))))
            groups.append({"title": group_title, "items": items})
        sections.append({"key": key, "title": title, "groups": groups})

    included_items = len(regular_items) + len(event_items)
    counts = {key: sum(len(group["items"]) for group in section["groups"]) for key, section in ((item["key"], item) for item in sections)}
    summary = (
        f"{day.isoformat()} 共收录 {included_items} 条情报："
        f"技术进展 {counts['technology']} 条、产业新闻 {counts['industry']} 条、融资新闻 {counts['funding']} 条。"
    )
    # Keep warning order stable while avoiding duplicate messages from callers
    # and source-level filtering.
    report_warnings = list(dict.fromkeys(str(value) for value in report_warnings if str(value).strip()))
    return {
        "schema_version": SCHEMA_VERSION,
        "template_version": TEMPLATE_VERSION,
        "report_date": day.isoformat(),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "headline": "AI VC Daily",
        "executive_summary": summary,
        "stats": {
            "sources_attempted": len(source_ids),
            "sources_succeeded": len(source_ids),
            "raw_items": len(contents),
            "included_items": included_items,
            "failed_sources": 0,
        },
        "sections": sections,
        "warnings": report_warnings,
        "generation_mode": "deterministic",
    }


def validate_report_data(data: dict[str, Any]) -> list[str]:
    """Return schema violations; an empty list means the data is renderable."""

    errors: list[str] = []
    if not isinstance(data, dict):
        return ["report data must be an object"]
    for field in ("schema_version", "template_version", "report_date", "generated_at", "headline", "sections", "warnings"):
        if field not in data:
            errors.append(f"missing field: {field}")
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if data.get("template_version") != TEMPLATE_VERSION:
        errors.append(f"template_version must be {TEMPLATE_VERSION}")
    report_date = data.get("report_date")
    if isinstance(report_date, str):
        try:
            date.fromisoformat(report_date)
        except ValueError:
            errors.append("report_date must be an ISO date")
    elif report_date is not None:
        errors.append("report_date must be a string")
    if not isinstance(data.get("warnings"), list) or any(not isinstance(value, str) for value in data.get("warnings", [])):
        errors.append("warnings must be a list of strings")
    stats = data.get("stats")
    if stats is not None:
        if not isinstance(stats, dict):
            errors.append("stats must be an object")
        else:
            for key in ("sources_attempted", "sources_succeeded", "raw_items", "included_items", "failed_sources"):
                if key in stats and (not isinstance(stats[key], int) or stats[key] < 0):
                    errors.append(f"stats.{key} must be a non-negative integer")

    sections = data.get("sections")
    if not isinstance(sections, list):
        errors.append("sections must be a list")
        return errors
    expected = [key for key, _title in REPORT_CATEGORIES]
    actual = [section.get("key") for section in sections if isinstance(section, dict)]
    if actual != expected:
        errors.append(f"sections must contain exactly {expected} in order")
    seen_content: set[int] = set()
    seen_events: set[int] = set()
    for section_index, section in enumerate(sections):
        prefix = f"sections[{section_index}]"
        if not isinstance(section, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(section.get("key"), str) or not isinstance(section.get("title"), str):
            errors.append(f"{prefix} requires key and title strings")
        groups = section.get("groups")
        if not isinstance(groups, list):
            errors.append(f"{prefix}.groups must be a list")
            continue
        for group_index, group in enumerate(groups):
            gp = f"{prefix}.groups[{group_index}]"
            if not isinstance(group, dict) or not isinstance(group.get("title"), str) or not isinstance(group.get("items"), list):
                errors.append(f"{gp} requires title and items")
                continue
            for item_index, item in enumerate(group["items"]):
                ip = f"{gp}.items[{item_index}]"
                if not isinstance(item, dict):
                    errors.append(f"{ip} must be an object")
                    continue
                content_id = item.get("content_id")
                event_id = item.get("event_id")
                has_content = isinstance(content_id, int) and not isinstance(content_id, bool)
                has_event = isinstance(event_id, int) and not isinstance(event_id, bool)
                if has_content == has_event:
                    errors.append(f"{ip} must have exactly one content_id or event_id")
                if has_content:
                    if content_id <= 0:
                        errors.append(f"{ip}.content_id must be positive")
                    elif content_id in seen_content:
                        errors.append(f"duplicate content_id: {content_id}")
                    seen_content.add(content_id)
                if has_event:
                    if event_id <= 0:
                        errors.append(f"{ip}.event_id must be positive")
                    elif event_id in seen_events:
                        errors.append(f"duplicate event_id: {event_id}")
                    seen_events.add(event_id)
                for field in ("title", "summary", "source", "url"):
                    if not isinstance(item.get(field), str) or not item[field].strip():
                        errors.append(f"{ip}.{field} must be a non-empty string")
                if isinstance(item.get("url"), str) and item["url"].strip():
                    parsed = urlparse(item["url"].strip())
                    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
                        errors.append(f"{ip}.url must use http(s) or a relative URL")
                if has_event:
                    sources = item.get("sources")
                    if not isinstance(sources, list) or not sources:
                        errors.append(f"{ip}.sources must contain at least one source")
                    elif any(not isinstance(source, dict) or not source.get("url") for source in sources):
                        errors.append(f"{ip}.sources must contain database URLs")
    return errors


__all__ = ["build_daily_report_data", "validate_report_data", "SCHEMA_VERSION", "TEMPLATE_VERSION"]
