from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import ContentItem, IntelligenceReview
from .services import db_now
from .utils import json_dumps, json_loads


REVIEW_DECISIONS = {
    "relevant": ("reviewed", 80),
    "follow_up": ("follow_up", 70),
    "not_relevant": ("ignored", 20),
    "reset": ("unread", None),
}


@dataclass(frozen=True)
class IntelligenceScore:
    score: int
    confidence: float
    reasons: list[str]


def score_content(content: ContentItem) -> IntelligenceScore:
    """Calculate an explainable fallback score without calling an external model."""
    score = 35
    reasons: list[str] = []
    if content.ai_related is True:
        score += 25
        reasons.append("内容被判断为与 AI 相关")
    elif content.ai_related is False:
        score -= 25
        reasons.append("内容被判断为与 AI 无关")

    tag_values = {(tag.tag_key, tag.tag_value.lower()) for tag in content.tags}
    if ("content_type", "financing") in tag_values:
        score += 25
        reasons.append("包含融资内容类型标签")
    if ("ai_financing_relevance", "high") in tag_values:
        score += 20
        reasons.append("AI 融资相关性为高")
    if content.entities:
        score += 5
        reasons.append("已识别公司或机构实体")

    text = f"{content.title or ''} {content.summary or ''}".lower()
    if any(marker in text for marker in ("融资", "投资", "funding", "financing", "raised", "series")):
        score += 10
        reasons.append("标题或摘要包含融资信号")
    if content.source_category in {"official_news", "official_research", "venture_media", "ai_media"}:
        score += 5
        reasons.append("来源属于重点情报来源")
    if not reasons:
        reasons.append("当前仅有基础规则评分")

    score = max(0, min(100, score))
    confidence = min(0.98, max(0.35, 0.45 + len(reasons) * 0.08))
    return IntelligenceScore(score=score, confidence=round(confidence, 2), reasons=reasons)


def _score_payload(content: ContentItem) -> dict[str, Any]:
    calculated = score_content(content)
    reasons = json_loads(content.relevance_reasons_json, []) if content.relevance_reasons_json else []
    if not isinstance(reasons, list) or not reasons:
        reasons = calculated.reasons
    return {
        "score": content.relevance_score if content.relevance_score is not None else calculated.score,
        "confidence": content.relevance_confidence or calculated.confidence,
        "reasons": reasons,
    }


class IntelligenceInbox:
    """Deep module for the reviewable intelligence queue.

    Callers only need list, review, and reprocess. Persistence, score fallback,
    review precedence, and audit history remain behind this interface.
    """

    def list(
        self,
        db: Session,
        *,
        query: str = "",
        status: str = "",
        minimum_score: int | None = None,
        target_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        statement = select(ContentItem).order_by(ContentItem.crawl_time.desc(), ContentItem.content_id.desc())
        if status:
            statement = statement.where(ContentItem.review_status == status)
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(ContentItem.title.ilike(pattern), ContentItem.summary.ilike(pattern), ContentItem.source_name.ilike(pattern))
            )
        if target_date:
            start = datetime.combine(target_date, dt_time.min)
            statement = statement.where(ContentItem.crawl_time >= start, ContentItem.crawl_time < start + timedelta(days=1))

        # Fetch a bounded working set so ordering by an explainable Python score
        # does not expose score implementation details through the API.
        candidates = db.scalars(statement.limit(min(1000, max(limit + offset, 200)))).all()
        rows = []
        for content in candidates:
            score = _score_payload(content)
            if minimum_score is not None and score["score"] < minimum_score:
                continue
            rows.append((score["score"], content))
        rows.sort(key=lambda row: (row[0], row[1].crawl_time or datetime.min), reverse=True)
        selected = [content for _, content in rows[offset : offset + min(max(limit, 1), 200)]]
        return {
            "items": [self._payload(content) for content in selected],
            "total": len(rows),
            "limit": min(max(limit, 1), 200),
            "offset": max(offset, 0),
            "filters": {"query": query, "status": status, "minimum_score": minimum_score, "date": target_date.isoformat() if target_date else None},
        }

    def review(self, db: Session, content_id: int, decision: str, note: str | None = None) -> dict[str, Any]:
        if decision not in REVIEW_DECISIONS:
            raise ValueError(f"不支持的复核决定：{decision}")
        content = db.get(ContentItem, content_id)
        if not content:
            raise LookupError("内容不存在")
        previous_status = content.review_status
        previous_score = content.relevance_score
        status, minimum_score = REVIEW_DECISIONS[decision]
        content.review_status = status
        content.review_note = (note or "").strip() or None
        content.reviewed_at = None if decision == "reset" else db_now()
        if minimum_score is not None:
            current = _score_payload(content)["score"]
            content.relevance_score = max(current, minimum_score) if decision != "not_relevant" else min(current, minimum_score)
            content.relevance_confidence = max(content.relevance_confidence or 0.0, 0.95)
            content.relevance_reasons_json = json_dumps([f"人工复核：{decision}"] + _score_payload(content)["reasons"])
        db.add(
            IntelligenceReview(
                content_id=content.content_id,
                decision=decision,
                note=content.review_note,
                previous_status=previous_status,
                previous_score=previous_score,
            )
        )
        db.commit()
        db.refresh(content)
        return self._payload(content)

    def reprocess(self, db: Session, content_id: int) -> dict[str, Any]:
        content = db.get(ContentItem, content_id)
        if not content:
            raise LookupError("内容不存在")
        calculated = score_content(content)
        manually_reviewed = content.review_status in {"reviewed", "follow_up", "ignored"}
        if not manually_reviewed:
            content.relevance_score = calculated.score
            content.relevance_confidence = calculated.confidence
            content.relevance_reasons_json = json_dumps(calculated.reasons)
            content.review_status = "unread"
        db.commit()
        db.refresh(content)
        return self._payload(content)

    def _payload(self, content: ContentItem) -> dict[str, Any]:
        score = _score_payload(content)
        return {
            "content_id": content.content_id,
            "title": content.title,
            "summary": content.summary,
            "url": content.url,
            "source_id": content.source_id,
            "source_name": content.source_name,
            "source_category": content.source_category,
            "publish_time": content.publish_time.isoformat(sep=" ", timespec="seconds") if content.publish_time else None,
            "crawl_time": content.crawl_time.isoformat(sep=" ", timespec="seconds") if content.crawl_time else None,
            "review_status": content.review_status or "unread",
            "review_note": content.review_note,
            "reviewed_at": content.reviewed_at.isoformat(sep=" ", timespec="seconds") if content.reviewed_at else None,
            "relevance_score": score["score"],
            "relevance_confidence": score["confidence"],
            "relevance_reasons": score["reasons"],
            "ai_related": content.ai_related,
            "llm_status": content.llm_status,
            "tags": [{"tag_key": tag.tag_key, "tag_value": tag.tag_value} for tag in content.tags],
            "entities": [
                {"entity_id": relation.entity_id, "entity_type": relation.entity.entity_type, "name": relation.entity.display_name or relation.entity.canonical_name}
                for relation in content.entities
            ],
        }
