from __future__ import annotations

import re
from datetime import date, datetime, time as dt_time, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ContentItem, EventChangeLog, EventContent, FinancingEvent
from .services import (
    FinancingService,
    build_financing_signature,
    clean_text,
    db_now,
    extract_financing_amount_texts,
    extract_financing_round_texts,
    extract_investor_texts,
    normalize_dedupe_text,
    normalize_company_name,
)
from .utils import json_dumps, json_loads
from .v03_contracts import BuildEventRequest, EventQuery, ReorganizeEventCommand, ReviseEventCommand


class EventConflictError(RuntimeError):
    pass


def _date_value(value: date | datetime | None) -> str | None:
    return value.isoformat() if value else None


def _date_time_value(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def _json_list(value: str | None) -> list[str]:
    parsed = json_loads(value, []) if value else []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _parse_amount(value: str | None) -> tuple[float | None, str | None]:
    if not value:
        return None, None
    normalized = value.lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(万|亿|million|billion)?", normalized)
    if not match:
        return None, None
    amount = float(match.group(1))
    unit = match.group(2) or ""
    if unit in {"万", "million"}:
        amount *= 10_000 if unit == "万" else 1_000_000
    elif unit in {"亿", "billion"}:
        amount *= 100_000_000 if unit == "亿" else 1_000_000_000
    if any(token in normalized for token in ("美元", "美金", "usd", "dollar")):
        currency = "USD"
    elif "港元" in normalized or "hkd" in normalized:
        currency = "HKD"
    elif any(token in normalized for token in ("欧元", "eur")):
        currency = "EUR"
    else:
        currency = "CNY" if any(token in normalized for token in ("元", "人民币", "rmb", "万", "亿")) else None
    return amount, currency


def _company_for_signature(signature: dict[str, Any]) -> str:
    companies = sorted(signature.get("companies", set()), key=lambda value: (-len(value), value))
    return companies[0] if companies else "未识别公司"


def _signature_from_event(event: FinancingEvent) -> dict[str, Any]:
    return {
        "date": event.announced_date,
        "companies": {event.company_name_normalized} if event.company_name_normalized else set(),
        "amounts": {normalize_dedupe_text(event.amount_original or "")} if event.amount_original else set(),
        "rounds": {normalize_dedupe_text(event.financing_round or "")} if event.financing_round else set(),
        "investors": {normalize_company_name(value) for value in _json_list(event.investors_json)},
        "title_norm": normalize_company_name(event.event_title),
        "tokens": set(re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", normalize_company_name(event.event_title))),
    }


def _event_match(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    left_companies = {normalize_company_name(value) for value in left.get("companies", set()) if value}
    right_companies = {normalize_company_name(value) for value in right.get("companies", set()) if value}
    if not left_companies or not right_companies:
        return 0.0, ["公司名称缺失，不能自动合并"]
    if not (left_companies & right_companies) and not any(
        a in b or b in a for a in left_companies for b in right_companies if len(a) >= 4 and len(b) >= 4
    ):
        return 0.0, ["公司名称不一致"]

    score = 40.0
    reasons.append("公司名称一致")
    if left.get("rounds") and right.get("rounds") and left["rounds"] & right["rounds"]:
        score += 20
        reasons.append("融资轮次一致")
    if left.get("amounts") and right.get("amounts") and left["amounts"] & right["amounts"]:
        score += 20
        reasons.append("融资金额一致")
    if left.get("date") and right.get("date"):
        delta = abs((left["date"] - right["date"]).days)
        if delta <= 7:
            score += 10
            reasons.append(f"宣布日期相差 {delta} 天")
    if left.get("investors") and right.get("investors") and left["investors"] & right["investors"]:
        score += 5
        reasons.append("投资机构有重合")
    title_similarity = SequenceMatcher(None, left.get("title_norm", ""), right.get("title_norm", "")).ratio()
    if title_similarity >= 0.55:
        score += 5
        reasons.append("标题文本相似")
    return min(score, 100.0), reasons


class FinancingEventCatalog:
    """Own financing event discovery, aggregation, revision and source history."""

    def build_candidates(self, db: Session, request: BuildEventRequest | None = None) -> dict[str, Any]:
        request = request or BuildEventRequest()
        today = db_now().date()
        end_date = request.end_date or today
        start_date = request.start_date or end_date - timedelta(days=30)
        start_at = datetime.combine(start_date, dt_time.min)
        end_at = datetime.combine(end_date + timedelta(days=1), dt_time.min)

        query = (
            select(ContentItem)
            .where(
                FinancingService().content_filter(),
                ContentItem.crawl_time >= start_at,
                ContentItem.crawl_time < end_at,
            )
            .order_by(ContentItem.crawl_time.desc(), ContentItem.content_id.desc())
            .offset(request.offset)
            .limit(request.limit)
        )
        contents = db.scalars(query).all()
        active_event_ids = select(EventContent.event_id).join(FinancingEvent).where(
            EventContent.content_id == ContentItem.content_id,
            FinancingEvent.review_status != "excluded",
        )
        events = db.scalars(
            select(FinancingEvent).where(FinancingEvent.review_status != "excluded")).all()
        stats = {"scanned": len(contents), "created": 0, "attached": 0, "skipped": 0, "conflicts": 0}
        changed_events: list[FinancingEvent] = []

        for content in contents:
            linked = db.scalar(select(EventContent).where(EventContent.content_id == content.content_id).limit(1))
            if linked:
                stats["skipped"] += 1
                continue
            signature = self._content_signature(content)
            best_event: FinancingEvent | None = None
            best_score = 0.0
            best_reasons: list[str] = []
            for event in events:
                if event.locked_by_user or event.review_status in {"confirmed", "excluded"}:
                    continue
                score, reasons = _event_match(signature, _signature_from_event(event))
                if score > best_score:
                    best_event, best_score, best_reasons = event, score, reasons

            if best_event and best_score >= 70:
                self._attach(db, best_event, content, best_score, best_reasons, "automatic")
                best_event.confidence = max(best_event.confidence, round(best_score / 100, 2))
                stats["attached"] += 1
                changed_events.append(best_event)
                continue

            event = self._new_event_from_content(content, signature, best_score / 100 if best_score else 0.0)
            if best_score and best_score < 70:
                stats["conflicts"] += 1
            db.add(event)
            db.flush()
            reasons = best_reasons or ["未找到可安全合并的已有事件"]
            self._attach(db, event, content, best_score, reasons, "automatic")
            self._log(db, event, "candidate_created", None, self._event_dict(event), json_dumps(reasons))
            events.append(event)
            changed_events.append(event)
            stats["created"] += 1

        db.commit()
        return {
            "ok": True,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "events": [self.to_view(event) for event in changed_events],
            **stats,
        }

    def list(self, db: Session, query: EventQuery | None = None) -> dict[str, Any]:
        query = query or EventQuery()
        statement = select(FinancingEvent).order_by(FinancingEvent.announced_date.desc(), FinancingEvent.event_id.desc())
        if query.status:
            statement = statement.where(FinancingEvent.review_status == query.status)
        if query.company:
            statement = statement.where(FinancingEvent.company_name.ilike(f"%{query.company.strip()}%"))
        if query.start_date:
            statement = statement.where(FinancingEvent.announced_date >= query.start_date)
        if query.end_date:
            statement = statement.where(FinancingEvent.announced_date <= query.end_date)
        if query.min_confidence is not None:
            statement = statement.where(FinancingEvent.confidence >= query.min_confidence)
        total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        events = db.scalars(statement.offset(query.offset).limit(query.limit)).all()
        return {
            "items": [self.to_view(event) for event in events],
            "total": total,
            "limit": query.limit,
            "offset": query.offset,
            "filters": query.model_dump(mode="json"),
        }

    def get(self, db: Session, event_id: int) -> dict[str, Any]:
        event = db.get(FinancingEvent, event_id)
        if not event:
            raise LookupError("Financing event not found")
        return self.to_view(event)

    def revise(self, db: Session, event_id: int, command: ReviseEventCommand) -> dict[str, Any]:
        event = db.get(FinancingEvent, event_id)
        if not event:
            raise LookupError("Financing event not found")
        if command.expected_updated_at and _date_time_value(event.updated_at) != command.expected_updated_at:
            raise EventConflictError("融资事件已被其他操作更新，请刷新后重试")
        before = self._event_dict(event)
        values = command.model_dump(exclude_unset=True)
        values.pop("expected_updated_at", None)
        for field in (
            "event_title",
            "company_name",
            "announced_date",
            "financing_round",
            "amount_original",
            "amount_normalized",
            "currency",
            "event_summary",
        ):
            if field in values:
                setattr(event, field, values[field])
        if "company_name" in values:
            event.company_name_normalized = normalize_company_name(event.company_name)
        if "investors" in values:
            event.investors_json = json_dumps(values["investors"] or [])
        if "lead_investors" in values:
            event.lead_investors_json = json_dumps(values["lead_investors"] or [])
        if command.review_status is not None:
            event.review_status = command.review_status
            event.reviewed_at = db_now()
        if command.locked_by_user is not None:
            event.locked_by_user = command.locked_by_user
        elif values:
            event.locked_by_user = True
        self._log(db, event, "revise", before, self._event_dict(event), "人工修订")
        db.commit()
        db.refresh(event)
        return self.to_view(event)

    def reorganize(self, db: Session, command: ReorganizeEventCommand) -> dict[str, Any]:
        if command.operation == "merge":
            return self._merge(db, command)
        if command.operation == "split":
            return self._split(db, command)
        if command.operation == "attach_content":
            return self._attach_content(db, command)
        if command.operation == "detach_content":
            return self._detach_content(db, command)
        if command.operation == "set_primary_source":
            return self._set_primary(db, command)
        raise ValueError("不支持的事件重组操作")

    def to_view(self, event: FinancingEvent) -> dict[str, Any]:
        sources = []
        for relation in sorted(event.contents, key=lambda value: (not value.is_primary_source, value.event_content_id)):
            content = relation.content
            sources.append(
                {
                    "event_content_id": relation.event_content_id,
                    "content_id": relation.content_id,
                    "title": content.title,
                    "summary": content.summary,
                    "url": content.url,
                    "source_name": content.source_name,
                    "publish_time": _date_time_value(content.publish_time),
                    "crawl_time": _date_time_value(content.crawl_time),
                    "is_primary_source": relation.is_primary_source,
                    "match_score": relation.match_score,
                    "match_reasons": json_loads(relation.match_reasons_json, []),
                    "association_source": relation.association_source,
                }
            )
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "event_title": event.event_title,
            "company_name": event.company_name,
            "company_name_normalized": event.company_name_normalized,
            "announced_date": _date_value(event.announced_date),
            "financing_round": event.financing_round,
            "amount_original": event.amount_original,
            "amount_normalized": event.amount_normalized,
            "currency": event.currency,
            "investors": _json_list(event.investors_json),
            "lead_investors": _json_list(event.lead_investors_json),
            "event_summary": event.event_summary,
            "confidence": event.confidence,
            "review_status": event.review_status,
            "locked_by_user": event.locked_by_user,
            "reviewed_at": _date_time_value(event.reviewed_at),
            "created_at": _date_time_value(event.created_at),
            "updated_at": _date_time_value(event.updated_at),
            "sources": sources,
        }

    def _content_signature(self, content: ContentItem) -> dict[str, Any]:
        signature = build_financing_signature(content)
        text = f"{content.title} {content.summary or ''}"
        title_match = re.match(
            r"^([\u4e00-\u9fffA-Za-z0-9._ -]{2,30}?)(?:获|获得|完成|宣布完成|raises|raised|lands|scores|secures|closes)",
            content.title.strip(),
            flags=re.IGNORECASE,
        )
        if title_match:
            company = normalize_company_name(title_match.group(1))
            if company:
                signature["companies"] = {company}
        signature["investors"] = {normalize_company_name(value) for value in extract_investor_texts(text)}
        signature["amount_texts"] = extract_financing_amount_texts(text)
        signature["round_texts"] = extract_financing_round_texts(text)
        return signature

    def _new_event_from_content(self, content: ContentItem, signature: dict[str, Any], confidence: float) -> FinancingEvent:
        company = _company_for_signature(signature)
        amount_original = (signature.get("amount_texts") or [None])[0]
        amount_normalized, currency = _parse_amount(amount_original)
        round_value = (signature.get("round_texts") or [None])[0]
        investors = extract_investor_texts(f"{content.title} {content.summary or ''}")
        return FinancingEvent(
            event_title=content.title or f"{company} 融资事件",
            company_name=company,
            company_name_normalized=normalize_company_name(company),
            announced_date=(content.publish_time or content.crawl_time).date() if content.publish_time or content.crawl_time else None,
            financing_round=round_value,
            amount_original=amount_original,
            amount_normalized=amount_normalized,
            currency=currency,
            investors_json=json_dumps(investors),
            lead_investors_json=json_dumps(investors[:1]),
            event_summary=content.summary or content.title,
            confidence=round(confidence, 2),
            review_status="pending",
        )

    def _attach(
        self,
        db: Session,
        event: FinancingEvent,
        content: ContentItem,
        score: float,
        reasons: list[str],
        association_source: str,
    ) -> EventContent:
        relation = db.scalar(
            select(EventContent).where(EventContent.event_id == event.event_id, EventContent.content_id == content.content_id)
        )
        if relation:
            return relation
        has_primary = any(item.is_primary_source for item in event.contents)
        relation = EventContent(
            event=event,
            content=content,
            is_primary_source=not has_primary,
            match_score=round(score, 2),
            match_reasons_json=json_dumps(reasons),
            association_source=association_source,
        )
        db.add(relation)
        return relation

    def _merge(self, db: Session, command: ReorganizeEventCommand) -> dict[str, Any]:
        ids = list(dict.fromkeys(command.event_ids))
        if len(ids) < 2:
            raise ValueError("合并至少需要两个事件")
        target_id = command.target_event_id or ids[0]
        target = db.get(FinancingEvent, target_id)
        if not target or target_id not in ids:
            raise LookupError("合并目标事件不存在")
        before = self._event_dict(target)
        for event_id in ids:
            if event_id == target_id:
                continue
            source = db.get(FinancingEvent, event_id)
            if not source:
                raise LookupError(f"事件 {event_id} 不存在")
            for relation in list(source.contents):
                existing = db.scalar(
                    select(EventContent).where(EventContent.event_id == target.event_id, EventContent.content_id == relation.content_id)
                )
                if existing:
                    existing.match_score = max(existing.match_score, relation.match_score)
                    db.delete(relation)
                else:
                    relation.event_id = target.event_id
                    relation.association_source = "manual"
            source.review_status = "excluded"
            source.locked_by_user = True
            source.event_summary = f"已合并至事件 #{target.event_id}"
            self._log(db, source, "merge_source", self._event_dict(source), self._event_dict(source), f"merged_into={target.event_id}")
        target.locked_by_user = True
        self._log(db, target, "merge", before, self._event_dict(target), "人工合并事件")
        db.commit()
        return {"ok": True, "operation": "merge", "event": self.to_view(target)}

    def _split(self, db: Session, command: ReorganizeEventCommand) -> dict[str, Any]:
        source_id = command.source_event_id or command.target_event_id
        if not source_id or not command.content_ids:
            raise ValueError("拆分需要 source_event_id 和 content_ids")
        source = db.get(FinancingEvent, source_id)
        if not source:
            raise LookupError("源事件不存在")
        selected = set(command.content_ids)
        relations = [relation for relation in source.contents if relation.content_id in selected]
        if not relations or len(relations) >= len(source.contents):
            raise ValueError("拆分必须选择部分来源")
        new_event = self._new_event_from_content(relations[0].content, self._content_signature(relations[0].content), source.confidence)
        if command.new_event_title:
            new_event.event_title = command.new_event_title
        db.add(new_event)
        db.flush()
        for relation in relations:
            relation.event_id = new_event.event_id
            relation.association_source = "manual"
        new_event.locked_by_user = True
        self._log(db, source, "split", self._event_dict(source), self._event_dict(source), f"new_event={new_event.event_id}")
        self._log(db, new_event, "split_created", None, self._event_dict(new_event), f"source_event={source.event_id}")
        db.commit()
        return {"ok": True, "operation": "split", "event": self.to_view(new_event), "source_event": self.to_view(source)}

    def _attach_content(self, db: Session, command: ReorganizeEventCommand) -> dict[str, Any]:
        target_id = command.target_event_id
        if not target_id or not command.content_id:
            raise ValueError("添加来源需要 target_event_id 和 content_id")
        event = db.get(FinancingEvent, target_id)
        content = db.get(ContentItem, command.content_id)
        if not event or not content:
            raise LookupError("事件或内容不存在")
        old = db.scalar(select(EventContent).where(EventContent.content_id == content.content_id))
        if old and old.event_id != event.event_id:
            db.delete(old)
            db.flush()
        self._attach(db, event, content, 100, ["人工添加来源"], "manual")
        event.locked_by_user = True
        self._log(db, event, "attach_content", None, self._event_dict(event), f"content={content.content_id}")
        db.commit()
        return {"ok": True, "operation": "attach_content", "event": self.to_view(event)}

    def _detach_content(self, db: Session, command: ReorganizeEventCommand) -> dict[str, Any]:
        if not command.target_event_id or not command.content_id:
            raise ValueError("移除来源需要 target_event_id 和 content_id")
        event = db.get(FinancingEvent, command.target_event_id)
        if not event:
            raise LookupError("事件不存在")
        if len(event.contents) <= 1:
            raise ValueError("事件至少需要保留一个来源")
        relation = db.scalar(
            select(EventContent).where(EventContent.event_id == event.event_id, EventContent.content_id == command.content_id)
        )
        if not relation:
            raise LookupError("来源不属于该事件")
        was_primary = relation.is_primary_source
        db.delete(relation)
        db.flush()
        if was_primary and event.contents:
            event.contents[0].is_primary_source = True
        event.locked_by_user = True
        self._log(db, event, "detach_content", None, self._event_dict(event), f"content={command.content_id}")
        db.commit()
        return {"ok": True, "operation": "detach_content", "event": self.to_view(event)}

    def _set_primary(self, db: Session, command: ReorganizeEventCommand) -> dict[str, Any]:
        if not command.target_event_id or not command.content_id:
            raise ValueError("设置主要来源需要 target_event_id 和 content_id")
        event = db.get(FinancingEvent, command.target_event_id)
        if not event:
            raise LookupError("事件不存在")
        if not any(item.content_id == command.content_id for item in event.contents):
            raise LookupError("来源不属于该事件")
        for relation in event.contents:
            relation.is_primary_source = relation.content_id == command.content_id
        event.locked_by_user = True
        self._log(db, event, "set_primary_source", None, self._event_dict(event), f"content={command.content_id}")
        db.commit()
        return {"ok": True, "operation": "set_primary_source", "event": self.to_view(event)}

    def _event_dict(self, event: FinancingEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "event_title": event.event_title,
            "company_name": event.company_name,
            "announced_date": _date_value(event.announced_date),
            "financing_round": event.financing_round,
            "amount_original": event.amount_original,
            "review_status": event.review_status,
            "locked_by_user": event.locked_by_user,
            "source_ids": [relation.content_id for relation in event.contents],
        }

    def _log(
        self,
        db: Session,
        event: FinancingEvent,
        operation: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        note: str | None = None,
    ) -> None:
        db.add(
            EventChangeLog(
                event_id=event.event_id,
                operation=operation,
                before_json=json_dumps(before or {}),
                after_json=json_dumps(after or {}),
                note=note,
            )
        )
