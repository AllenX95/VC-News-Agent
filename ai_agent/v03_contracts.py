from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildEventRequest(ContractModel):
    start_date: date | None = None
    end_date: date | None = None
    limit: int = Field(default=200, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class EventQuery(ContractModel):
    status: Literal["", "pending", "confirmed", "excluded"] = ""
    company: str = ""
    start_date: date | None = None
    end_date: date | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ReviseEventCommand(ContractModel):
    event_title: str | None = None
    company_name: str | None = None
    announced_date: date | None = None
    financing_round: str | None = None
    amount_original: str | None = None
    amount_normalized: float | None = None
    currency: str | None = None
    investors: list[str] | None = None
    lead_investors: list[str] | None = None
    event_summary: str | None = None
    review_status: Literal["pending", "confirmed", "excluded"] | None = None
    locked_by_user: bool | None = None
    expected_updated_at: str | None = None


class ReorganizeEventCommand(ContractModel):
    operation: Literal["merge", "split", "attach_content", "detach_content", "set_primary_source"]
    event_ids: list[int] = Field(default_factory=list)
    target_event_id: int | None = None
    source_event_id: int | None = None
    content_id: int | None = None
    content_ids: list[int] = Field(default_factory=list)
    new_event_title: str | None = None


class SaveWatchCommand(ContractModel):
    target_type: Literal["financing_event", "content"]
    target_id: int
    priority: Literal["high", "medium", "low"] = "medium"
    status: Literal["watching", "follow_up", "paused", "completed"] = "watching"
    reason: str | None = None
    next_review_date: date | None = None
    notes: str | None = None


class UpdateWatchCommand(ContractModel):
    priority: Literal["high", "medium", "low"] | None = None
    status: Literal["watching", "follow_up", "paused", "completed"] | None = None
    reason: str | None = None
    next_review_date: date | None = None
    notes: str | None = None


class WatchQuery(ContractModel):
    status: Literal["", "watching", "follow_up", "paused", "completed"] = ""
    priority: Literal["", "high", "medium", "low"] = ""
    target_type: Literal["", "financing_event", "content"] = ""
    due_before: date | None = None
    sort: Literal["priority", "next_review_date", "updated_at"] = "next_review_date"
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ReportPreviewRequest(ContractModel):
    report_type: Literal[
        "weekly_financing", "current_week_financing", "watchlist_digest", "custom"
    ] = "weekly_financing"
    period_start: date
    period_end: date
    target_ids: list[int] = Field(default_factory=list)
    target_type: Literal["financing_event", "watch_item"] | None = None


class GenerateReportCommand(ReportPreviewRequest):
    title: str | None = None


class ReviseReportCommand(ContractModel):
    markdown_text: str = Field(min_length=1)
    status: Literal["draft", "reviewed", "archived"] = "draft"


class ExportReportCommand(ContractModel):
    version_number: int | None = Field(default=None, ge=1)
    output_path: str | None = None
    overwrite: bool = False


class ReportStatusCommand(ContractModel):
    status: Literal["draft", "reviewed", "exported", "archived"]


class FlexibleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool = True
    data: Any | None = None
