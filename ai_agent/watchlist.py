from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .financing_events import FinancingEventCatalog
from .models import ContentItem, FinancingEvent, WatchItem
from .services import db_now
from .v03_contracts import SaveWatchCommand, UpdateWatchCommand, WatchQuery


class WatchConflictError(RuntimeError):
    pass


class Watchlist:
    ACTIVE_STATUSES = {"watching", "follow_up", "paused"}
    TARGET_TYPES = {"financing_event", "content"}

    def list(self, db: Session, query: WatchQuery | None = None) -> dict[str, Any]:
        query = query or WatchQuery()
        statement = select(WatchItem)
        if query.status:
            statement = statement.where(WatchItem.status == query.status)
        if query.priority:
            statement = statement.where(WatchItem.priority == query.priority)
        if query.target_type:
            statement = statement.where(WatchItem.target_type == query.target_type)
        if query.due_before:
            statement = statement.where(WatchItem.next_review_date <= query.due_before)
        total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        items = db.scalars(statement).all()
        today = db_now().date()
        priority_order = case((WatchItem.priority == "high", 0), (WatchItem.priority == "medium", 1), else_=2)
        if query.sort == "priority":
            items = db.scalars(statement.order_by(priority_order, WatchItem.next_review_date, WatchItem.updated_at.desc())).all()
        elif query.sort == "updated_at":
            items = db.scalars(statement.order_by(WatchItem.updated_at.desc())).all()
        else:
            items = db.scalars(
                statement.order_by(
                    case(
                        (WatchItem.status == "follow_up", 0),
                        else_=1,
                    ),
                    case(
                        (WatchItem.next_review_date.is_(None), 1),
                        else_=0,
                    ),
                    WatchItem.next_review_date,
                    priority_order,
                    WatchItem.updated_at.desc(),
                )
            ).all()
        items = items[query.offset : query.offset + query.limit]
        return {
            "items": [self.to_view(db, item, today=today) for item in items],
            "total": total,
            "limit": query.limit,
            "offset": query.offset,
            "filters": query.model_dump(mode="json"),
        }

    def get(self, db: Session, watch_id: int) -> dict[str, Any]:
        item = db.get(WatchItem, watch_id)
        if not item:
            raise LookupError("Watch item not found")
        return self.to_view(db, item)

    def save(self, db: Session, command: SaveWatchCommand, watch_id: int | None = None) -> dict[str, Any]:
        if command.target_type not in self.TARGET_TYPES:
            raise ValueError("不支持的关注对象类型")
        title, summary = self._target_snapshot(db, command.target_type, command.target_id)
        item = db.get(WatchItem, watch_id) if watch_id else None
        if watch_id and not item:
            raise LookupError("Watch item not found")
        active_key = self._active_key(command.target_type, command.target_id, command.status)
        duplicate = None
        if active_key:
            duplicate = db.scalar(
                select(WatchItem).where(
                    WatchItem.active_target_key == active_key,
                    WatchItem.watch_id != (item.watch_id if item else 0),
                )
            )
        if duplicate:
            raise WatchConflictError("同一目标已有未完成关注项")
        if item is None:
            item = WatchItem(
                target_type=command.target_type,
                target_id=command.target_id,
                target_title_snapshot=title,
                target_summary_snapshot=summary,
            )
            db.add(item)
        item.target_type = command.target_type
        item.target_id = command.target_id
        item.target_title_snapshot = title
        item.target_summary_snapshot = summary
        item.priority = command.priority
        item.status = command.status
        item.reason = command.reason
        item.next_review_date = command.next_review_date
        item.notes = command.notes
        item.active_target_key = active_key
        item.completed_at = db_now() if command.status == "completed" else None
        db.commit()
        db.refresh(item)
        return self.to_view(db, item)

    def update(self, db: Session, watch_id: int, command: UpdateWatchCommand) -> dict[str, Any]:
        item = db.get(WatchItem, watch_id)
        if not item:
            raise LookupError("Watch item not found")
        values = command.model_dump(exclude_unset=True)
        status = command.status or item.status
        active_key = self._active_key(item.target_type, item.target_id, status)
        duplicate = None
        if active_key:
            duplicate = db.scalar(
                select(WatchItem).where(
                    WatchItem.active_target_key == active_key,
                    WatchItem.watch_id != item.watch_id,
                )
            )
        if duplicate:
            raise WatchConflictError("同一目标已有未完成关注项")
        for field in ("priority", "status", "reason", "next_review_date", "notes"):
            if field in values:
                setattr(item, field, values[field])
        item.active_target_key = active_key
        item.completed_at = db_now() if status == "completed" else None
        db.commit()
        db.refresh(item)
        return self.to_view(db, item)

    def remove(self, db: Session, watch_id: int) -> dict[str, Any]:
        item = db.get(WatchItem, watch_id)
        if not item:
            raise LookupError("Watch item not found")
        db.delete(item)
        db.commit()
        return {"ok": True, "watch_id": watch_id}

    def due_summary(self, db: Session, limit: int = 5) -> dict[str, Any]:
        today = db_now().date()
        due = db.scalars(
            select(WatchItem)
            .where(
                WatchItem.status == "follow_up",
                WatchItem.next_review_date.is_not(None),
                WatchItem.next_review_date <= today,
            )
            .order_by(WatchItem.next_review_date, WatchItem.priority)
            .limit(limit)
        ).all()
        count = db.scalar(
            select(func.count(WatchItem.watch_id)).where(
                WatchItem.status == "follow_up",
                WatchItem.next_review_date.is_not(None),
                WatchItem.next_review_date <= today,
            )
        ) or 0
        return {"count": count, "items": [self.to_view(db, item, today=today) for item in due]}

    def to_view(self, db: Session, item: WatchItem, today: date | None = None) -> dict[str, Any]:
        available = self._target_exists(db, item.target_type, item.target_id)
        next_review = item.next_review_date.isoformat() if item.next_review_date else None
        return {
            "watch_id": item.watch_id,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "target_title_snapshot": item.target_title_snapshot,
            "target_summary_snapshot": item.target_summary_snapshot,
            "target_available": available,
            "priority": item.priority,
            "status": item.status,
            "reason": item.reason,
            "next_review_date": next_review,
            "notes": item.notes,
            "created_at": item.created_at.isoformat(sep=" ", timespec="seconds") if item.created_at else None,
            "updated_at": item.updated_at.isoformat(sep=" ", timespec="seconds") if item.updated_at else None,
            "completed_at": item.completed_at.isoformat(sep=" ", timespec="seconds") if item.completed_at else None,
            "is_due": bool(
                item.status == "follow_up"
                and item.next_review_date
                and item.next_review_date <= (today or db_now().date())
            ),
        }

    def _target_snapshot(self, db: Session, target_type: str, target_id: int) -> tuple[str, str | None]:
        if target_type == "financing_event":
            event = db.get(FinancingEvent, target_id)
            if not event:
                raise LookupError("Financing event not found")
            return event.event_title, event.event_summary
        content = db.get(ContentItem, target_id)
        if not content:
            raise LookupError("Content not found")
        return content.title, content.summary

    def _target_exists(self, db: Session, target_type: str, target_id: int) -> bool:
        if target_type == "financing_event":
            return db.get(FinancingEvent, target_id) is not None
        return db.get(ContentItem, target_id) is not None

    def _active_key(self, target_type: str, target_id: int, status: str) -> str | None:
        return f"{target_type}:{target_id}" if status in self.ACTIVE_STATUSES else None
