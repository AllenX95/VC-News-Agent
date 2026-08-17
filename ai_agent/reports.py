from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .config import DB_PATH
from .financing_events import FinancingEventCatalog
from .models import ContentItem, FinancingEvent, Report, ReportExport, ReportInput, ReportVersion, WatchItem
from .services import LLMService, db_now
from .utils import json_dumps, json_loads
from .v03_contracts import (
    ExportReportCommand,
    GenerateReportCommand,
    ReportPreviewRequest,
    ReportStatusCommand,
    ReviseReportCommand,
)


class ReportGenerator(Protocol):
    def generate(self, db: Session, report_type: str, start: date, end: date, inputs: list[dict[str, Any]]) -> dict[str, Any]: ...


class LLMReportAdapter:
    TASKS = {
        "weekly_financing": "generate_previous_week_financing_report",
        "current_week_financing": "generate_current_week_financing_report",
        "watchlist_digest": "generate_current_week_financing_report",
        "custom": "generate_current_week_financing_report",
    }

    def generate(self, db: Session, report_type: str, start: date, end: date, inputs: list[dict[str, Any]]) -> dict[str, Any]:
        task_name = self.TASKS.get(report_type, self.TASKS["custom"])
        payload = {
            "report_type": report_type,
            "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
            "inputs": inputs,
            "event_count": sum(1 for item in inputs if item.get("target_type") == "financing_event"),
        }
        service = LLMService()
        markdown = service.generate_financing_report(db, task_name, payload)
        assets = service._task_assets(db, task_name)
        return {
            "markdown": markdown,
            "prompt_id": assets[2].prompt_id if assets else None,
            "model_name": assets[5] if assets else None,
        }


class ReportWorkspace:
    """Own report input snapshots, immutable versions, generation and Markdown export."""

    def __init__(self, generator: ReportGenerator | None = None) -> None:
        self.generator = generator or LLMReportAdapter()
        self.events = FinancingEventCatalog()

    def preview(self, db: Session, request: ReportPreviewRequest) -> dict[str, Any]:
        self._validate_period(request.period_start, request.period_end)
        candidates = self._candidate_inputs(db, request)
        return {
            "report_type": request.report_type,
            "period_start": request.period_start.isoformat(),
            "period_end": request.period_end.isoformat(),
            "inputs": candidates,
            "input_count": len(candidates),
        }

    def generate(self, db: Session, command: GenerateReportCommand) -> dict[str, Any]:
        self._validate_period(command.period_start, command.period_end)
        inputs = self._candidate_inputs(db, command)
        title = command.title or self._default_title(command.report_type, command.period_start, command.period_end)
        report = Report(
            report_type=command.report_type,
            title=title,
            period_start=command.period_start,
            period_end=command.period_end,
            status="draft",
            latest_version_number=0,
        )
        db.add(report)
        db.flush()
        self._replace_inputs(db, report, inputs)
        input_snapshot = self._input_snapshot(inputs)
        try:
            generated = self.generator.generate(db, command.report_type, command.period_start, command.period_end, inputs)
            markdown = str(generated.get("markdown") or "")
            if not markdown.strip():
                raise RuntimeError("报告生成器返回空正文")
            version = self._create_version(
                db,
                report,
                1,
                "generated",
                input_snapshot,
                markdown,
                generated,
            )
            report.latest_version_number = 1
            report.last_generation_error = None
            db.commit()
            db.refresh(report)
            return self.to_view(report, include_body=True)
        except Exception as exc:
            error_text = str(exc)
            db.rollback()
            # Persist the failed attempt without presenting it as a usable report version.
            report = Report(
                report_type=command.report_type,
                title=title,
                period_start=command.period_start,
                period_end=command.period_end,
                status="draft",
                latest_version_number=0,
                last_generation_error=error_text,
            )
            db.add(report)
            db.flush()
            self._replace_inputs(db, report, inputs)
            db.add(
                ReportVersion(
                    report=report,
                    version_number=1,
                    version_source="generated",
                    input_snapshot_json=json_dumps(input_snapshot),
                    markdown_text="",
                    generation_status="failed",
                    generation_error=error_text,
                )
            )
            db.commit()
            raise RuntimeError(error_text) from exc

    def regenerate(self, db: Session, report_id: int) -> dict[str, Any]:
        report = self._get_report(db, report_id)
        inputs = self._current_inputs(report)
        start = report.period_start or db_now().date()
        end = report.period_end or start
        next_number = self._next_version_number(db, report)
        input_snapshot = self._input_snapshot(inputs)
        try:
            generated = self.generator.generate(db, report.report_type, start, end, inputs)
            markdown = str(generated.get("markdown") or "")
            if not markdown.strip():
                raise RuntimeError("报告生成器返回空正文")
            self._create_version(db, report, next_number, "regenerated", input_snapshot, markdown, generated)
            report.latest_version_number = next_number
            report.last_generation_error = None
            db.commit()
            db.refresh(report)
            return self.to_view(report, include_body=True)
        except Exception as exc:
            error_text = str(exc)
            db.rollback()
            report = self._get_report(db, report_id)
            db.add(
                ReportVersion(
                    report=report,
                    version_number=next_number,
                    version_source="regenerated",
                    input_snapshot_json=json_dumps(input_snapshot),
                    markdown_text="",
                    generation_status="failed",
                    generation_error=error_text,
                )
            )
            report.last_generation_error = error_text
            db.commit()
            raise RuntimeError(error_text) from exc

    def revise(self, db: Session, report_id: int, command: ReviseReportCommand) -> dict[str, Any]:
        report = self._get_report(db, report_id)
        next_number = self._next_version_number(db, report)
        inputs = self._current_inputs(report)
        version = self._create_version(
            db,
            report,
            next_number,
            "manual_edit",
            self._input_snapshot(inputs),
            command.markdown_text,
            {},
        )
        report.latest_version_number = next_number
        report.status = command.status
        report.last_generation_error = None
        db.commit()
        db.refresh(report)
        return self.to_view(report, include_body=True)

    def set_status(self, db: Session, report_id: int, command: ReportStatusCommand) -> dict[str, Any]:
        report = self._get_report(db, report_id)
        report.status = command.status
        db.commit()
        db.refresh(report)
        return self.to_view(report)

    def export(self, db: Session, report_id: int, command: ExportReportCommand) -> dict[str, Any]:
        report = self._get_report(db, report_id)
        version_number = command.version_number or report.latest_version_number
        version = db.scalar(
            select(ReportVersion).where(
                ReportVersion.report_id == report_id,
                ReportVersion.version_number == version_number,
                ReportVersion.generation_status == "success",
            )
        )
        if not version:
            raise LookupError("可导出的报告版本不存在")
        if not version.markdown_text.strip():
            raise ValueError("报告正文为空，不能导出")
        target = self._resolve_export_path(report, version, command)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(version.markdown_text, encoding="utf-8", newline="\n")
            os.replace(temp_path, target)
        except Exception as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"报告导出失败：{exc}") from exc
        export = ReportExport(
            report=report,
            report_version_id=version.report_version_id,
            version_number=version.version_number,
            file_path=str(target),
        )
        db.add(export)
        report.status = "exported"
        report.last_generation_error = None
        db.commit()
        return {
            "ok": True,
            "report_id": report.report_id,
            "version_number": version.version_number,
            "file_path": str(target),
            "exported_at": export.exported_at.isoformat(sep=" ", timespec="seconds") if export.exported_at else None,
        }

    def list(self, db: Session, status: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        statement = select(Report).order_by(Report.updated_at.desc(), Report.report_id.desc())
        if status:
            statement = statement.where(Report.status == status)
        total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        reports = db.scalars(statement.offset(offset).limit(limit)).all()
        return {"items": [self.to_view(report) for report in reports], "total": total, "limit": limit, "offset": offset}

    def get(self, db: Session, report_id: int) -> dict[str, Any]:
        return self.to_view(self._get_report(db, report_id), include_body=True)

    def _candidate_inputs(self, db: Session, request: ReportPreviewRequest) -> list[dict[str, Any]]:
        if request.target_ids:
            if request.target_type == "financing_event":
                return [self._snapshot_for_target(db, "financing_event", target_id) for target_id in request.target_ids]
            if request.target_type == "watch_item":
                return [self._snapshot_for_target(db, "watch_item", target_id) for target_id in request.target_ids]
            return [
                self._snapshot_for_target(db, "financing_event", target_id)
                for target_id in request.target_ids
            ]
        if request.report_type in {"weekly_financing", "current_week_financing"}:
            events = db.scalars(
                select(FinancingEvent)
                .where(
                    FinancingEvent.review_status == "confirmed",
                    FinancingEvent.announced_date >= request.period_start,
                    FinancingEvent.announced_date <= request.period_end,
                )
                .order_by(FinancingEvent.announced_date, FinancingEvent.event_id)
            ).all()
            return [self._snapshot_for_target(db, "financing_event", event.event_id) for event in events]
        if request.report_type == "watchlist_digest":
            watches = db.scalars(
                select(WatchItem)
                .where(
                    WatchItem.status != "completed",
                    (WatchItem.next_review_date.is_(None))
                    | and_(
                        WatchItem.next_review_date >= request.period_start,
                        WatchItem.next_review_date <= request.period_end,
                    ),
                )
                .order_by(WatchItem.next_review_date, WatchItem.watch_id)
            ).all()
            return [self._snapshot_for_target(db, "watch_item", item.watch_id) for item in watches]
        return []

    def _snapshot_for_target(self, db: Session, target_type: str, target_id: int) -> dict[str, Any]:
        if target_type == "financing_event":
            event = db.get(FinancingEvent, target_id)
            if not event:
                raise LookupError(f"融资事件 {target_id} 不存在")
            view = self.events.to_view(event)
            return {
                "target_type": target_type,
                "target_id": target_id,
                "title": event.event_title,
                "summary": event.event_summary or "",
                "company_name": event.company_name,
                "review_status": event.review_status,
                "sources": view["sources"],
                "snapshot": view,
                "included": True,
            }
        if target_type == "watch_item":
            item = db.get(WatchItem, target_id)
            if not item:
                raise LookupError(f"关注项 {target_id} 不存在")
            return {
                "target_type": target_type,
                "target_id": target_id,
                "title": item.target_title_snapshot,
                "summary": item.target_summary_snapshot or "",
                "status": item.status,
                "priority": item.priority,
                "snapshot": {
                    "watch_id": item.watch_id,
                    "target_type": item.target_type,
                    "target_id": item.target_id,
                    "title": item.target_title_snapshot,
                    "summary": item.target_summary_snapshot,
                    "reason": item.reason,
                    "notes": item.notes,
                    "priority": item.priority,
                    "status": item.status,
                },
                "included": True,
            }
        content = db.get(ContentItem, target_id)
        if not content:
            raise LookupError(f"内容 {target_id} 不存在")
        return {
            "target_type": "content",
            "target_id": target_id,
            "title": content.title,
            "summary": content.summary or "",
            "snapshot": {"content_id": content.content_id, "title": content.title, "summary": content.summary, "url": content.url},
            "included": True,
        }

    def _replace_inputs(self, db: Session, report: Report, inputs: list[dict[str, Any]]) -> None:
        report.inputs.clear()
        for order, item in enumerate(inputs):
            report.inputs.append(
                ReportInput(
                    target_type=item["target_type"],
                    target_id=item["target_id"],
                    display_order=order,
                    included=item.get("included", True),
                    snapshot_json=json_dumps(item.get("snapshot", item)),
                )
            )

    def _current_inputs(self, report: Report) -> list[dict[str, Any]]:
        return [
            {
                "target_type": item.target_type,
                "target_id": item.target_id,
                "included": item.included,
                "snapshot": json_loads(item.snapshot_json, {}),
                "title": json_loads(item.snapshot_json, {}).get("event_title")
                or json_loads(item.snapshot_json, {}).get("title")
                or f"{item.target_type} #{item.target_id}",
            }
            for item in sorted(report.inputs, key=lambda value: value.display_order)
            if item.included
        ]

    def _input_snapshot(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "target_type": item["target_type"],
                "target_id": item["target_id"],
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                "snapshot": item.get("snapshot", item),
            }
            for item in inputs
            if item.get("included", True)
        ]

    def _create_version(
        self,
        db: Session,
        report: Report,
        number: int,
        source: str,
        input_snapshot: list[dict[str, Any]],
        markdown: str,
        metadata: dict[str, Any],
    ) -> ReportVersion:
        version = ReportVersion(
            report=report,
            version_number=number,
            version_source=source,
            template_version="v0.3",
            prompt_id=metadata.get("prompt_id"),
            model_name=metadata.get("model_name"),
            input_snapshot_json=json_dumps(input_snapshot),
            markdown_text=markdown,
            generation_status="success",
        )
        db.add(version)
        return version

    def _next_version_number(self, db: Session, report: Report) -> int:
        maximum = db.scalar(select(func.max(ReportVersion.version_number)).where(ReportVersion.report_id == report.report_id)) or 0
        return max(report.latest_version_number, maximum) + 1

    def _get_report(self, db: Session, report_id: int) -> Report:
        report = db.get(Report, report_id)
        if not report:
            raise LookupError("Report not found")
        return report

    def to_view(self, report: Report, include_body: bool = False) -> dict[str, Any]:
        versions = sorted(report.versions, key=lambda value: value.version_number, reverse=True)
        version_views = []
        for version in versions:
            version_view = {
                "report_version_id": version.report_version_id,
                "version_number": version.version_number,
                "version_source": version.version_source,
                "template_version": version.template_version,
                "prompt_id": version.prompt_id,
                "model_name": version.model_name,
                "generation_status": version.generation_status,
                "generation_error": version.generation_error,
                "created_at": version.created_at.isoformat(sep=" ", timespec="seconds") if version.created_at else None,
            }
            if include_body:
                version_view["markdown_text"] = version.markdown_text
                version_view["input_snapshot"] = json_loads(version.input_snapshot_json, [])
            version_views.append(version_view)
        payload = {
            "report_id": report.report_id,
            "report_type": report.report_type,
            "title": report.title,
            "period_start": report.period_start.isoformat() if report.period_start else None,
            "period_end": report.period_end.isoformat() if report.period_end else None,
            "status": report.status,
            "latest_version_number": report.latest_version_number,
            "last_generation_error": report.last_generation_error,
            "created_at": report.created_at.isoformat(sep=" ", timespec="seconds") if report.created_at else None,
            "updated_at": report.updated_at.isoformat(sep=" ", timespec="seconds") if report.updated_at else None,
            "inputs": [
                {
                    "report_input_id": item.report_input_id,
                    "target_type": item.target_type,
                    "target_id": item.target_id,
                    "display_order": item.display_order,
                    "included": item.included,
                    "snapshot": json_loads(item.snapshot_json, {}),
                }
                for item in sorted(report.inputs, key=lambda value: value.display_order)
            ],
            "versions": version_views,
        }
        if include_body and versions:
            latest = next((version for version in versions if version.version_number == report.latest_version_number), versions[0])
            payload["markdown_text"] = latest.markdown_text
        return payload

    def _resolve_export_path(self, report: Report, version: ReportVersion, command: ExportReportCommand) -> Path:
        if command.output_path:
            target = Path(command.output_path).expanduser()
            if target.suffix.lower() != ".md":
                target = target / f"{self._slug(report.title)}_v{version.version_number}.md"
        else:
            target = DB_PATH.parent / "reports" / f"{self._slug(report.title)}_v{version.version_number}.md"
        if target.exists() and not command.overwrite:
            for index in range(2, 1000):
                candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
                if not candidate.exists():
                    target = candidate
                    break
        return target

    def _default_title(self, report_type: str, start: date, end: date) -> str:
        labels = {
            "weekly_financing": "融资周报",
            "current_week_financing": "本周融资动态",
            "watchlist_digest": "关注项摘要",
            "custom": "投资情报报告",
        }
        return f"{labels.get(report_type, labels['custom'])}（{start.isoformat()} 至 {end.isoformat()}）"

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value, flags=re.UNICODE).strip("_")
        return slug[:80] or "report"

    def _validate_period(self, start: date, end: date) -> None:
        if start > end:
            raise ValueError("报告开始日期不能晚于结束日期")
