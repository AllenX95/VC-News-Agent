from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_category: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    access_method: Mapped[str] = mapped_column(String(40), default="auto")
    priority: Mapped[str] = mapped_column(String(10), default="P0")
    crawl_frequency: Mapped[str] = mapped_column(String(30), default="daily")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    include_keywords: Mapped[str | None] = mapped_column(Text)
    exclude_keywords: Mapped[str | None] = mapped_column(Text)
    login_required: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_required: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_js: Mapped[bool] = mapped_column(Boolean, default=False)
    crawl_risk: Mapped[str] = mapped_column(String(20), default="low")
    list_page_limit: Mapped[int] = mapped_column(Integer, default=50)
    item_limit_per_run: Mapped[int] = mapped_column(Integer, default=20)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
    parser_config: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    contents: Mapped[list["ContentItem"]] = relationship(back_populates="source")


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    crawl_run_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.source_id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(40), default="running")
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    new_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)


class CrawlError(Base):
    __tablename__ = "crawl_errors"

    error_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.source_id"))
    crawl_run_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_runs.crawl_run_id"))
    error_type: Mapped[str] = mapped_column(String(100), default="unknown")
    error_message: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stack_trace: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_note: Mapped[str | None] = mapped_column(Text)


class ContentItem(Base):
    __tablename__ = "content_items"

    content_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.source_id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_category: Mapped[str] = mapped_column(String(80), nullable=False)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime)
    crawl_time: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    publish_time_status: Mapped[str] = mapped_column(String(20), default="missing")
    summary: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(20))
    word_count: Mapped[int | None] = mapped_column(Integer)
    content_fingerprint: Mapped[str | None] = mapped_column(String(80))
    extraction_status: Mapped[str] = mapped_column(String(40), default="new")
    llm_status: Mapped[str] = mapped_column(String(40), default="not_configured")
    ai_related: Mapped[bool | None] = mapped_column(Boolean)
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    relevance_confidence: Mapped[float | None] = mapped_column(Float)
    relevance_reasons_json: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(40), default="unread")
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    full_content_cached: Mapped[bool] = mapped_column(Boolean, default=False)
    content_cache_until: Mapped[datetime | None] = mapped_column(DateTime)
    full_content_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    archive_object_path: Mapped[str | None] = mapped_column(Text)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    favorited_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    source: Mapped[Source] = relationship(back_populates="contents")
    cache: Mapped["ContentCache | None"] = relationship(back_populates="content", cascade="all, delete-orphan")
    tags: Mapped[list["ContentTag"]] = relationship(back_populates="content", cascade="all, delete-orphan")
    entities: Mapped[list["ContentEntity"]] = relationship(back_populates="content", cascade="all, delete-orphan")
    reviews: Mapped[list["IntelligenceReview"]] = relationship(back_populates="content", cascade="all, delete-orphan")


class ContentCache(Base):
    __tablename__ = "content_cache"

    content_id: Mapped[int] = mapped_column(ForeignKey("content_items.content_id"), primary_key=True)
    clean_text: Mapped[str] = mapped_column(Text)
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expire_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    content: Mapped[ContentItem] = relationship(back_populates="cache")


class TagDefinition(Base):
    __tablename__ = "tag_definitions"
    __table_args__ = (UniqueConstraint("tag_key", "tag_value", name="uq_tag_key_value"),)

    tag_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tag_key: Mapped[str] = mapped_column(String(80), nullable=False)
    tag_value: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name_cn: Mapped[str | None] = mapped_column(String(120))
    display_name_en: Mapped[str | None] = mapped_column(String(120))
    aliases: Mapped[str | None] = mapped_column(Text)
    parent_tag_id: Mapped[int | None] = mapped_column(ForeignKey("tag_definitions.tag_id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ContentTag(Base):
    __tablename__ = "content_tags"

    content_tag_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_items.content_id"), nullable=False)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag_definitions.tag_id"), nullable=False)
    tag_key: Mapped[str] = mapped_column(String(80), nullable=False)
    tag_value: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="rule")

    content: Mapped[ContentItem] = relationship(back_populates="tags")
    tag: Mapped[TagDefinition] = relationship()


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("entity_type", "canonical_name", name="uq_entity_type_name"),)

    entity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    aliases: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ContentEntity(Base):
    __tablename__ = "content_entities"

    content_entity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_items.content_id"), nullable=False)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.entity_id"), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="llm")

    content: Mapped[ContentItem] = relationship(back_populates="entities")
    entity: Mapped[Entity] = relationship()


class IntelligenceReview(Base):
    __tablename__ = "intelligence_reviews"

    review_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_items.content_id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    previous_status: Mapped[str | None] = mapped_column(String(40))
    previous_score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    content: Mapped[ContentItem] = relationship(back_populates="reviews")


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(40), default="manual")
    status: Mapped[str] = mapped_column(String(40), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    total_sources: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_sources: Mapped[int] = mapped_column(Integer, default=0)
    failed_sources: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LLMConfig(Base):
    __tablename__ = "llm_configs"

    llm_config_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False)
    encrypted_base_url: Mapped[str | None] = mapped_column(Text)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    encrypted_model_name: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_retries: Mapped[int] = mapped_column(Integer, default=1)
    context_window_tokens: Mapped[int] = mapped_column(Integer, default=1_000_000)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Prompt(Base):
    __tablename__ = "prompts"

    prompt_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_name: Mapped[str] = mapped_column(String(160), nullable=False)
    task_name: Mapped[str] = mapped_column(String(120), default="process_content_metadata")
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LLMTask(Base):
    __tablename__ = "llm_tasks"

    llm_task_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    llm_config_id: Mapped[int | None] = mapped_column(ForeignKey("llm_configs.llm_config_id"))
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.prompt_id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class LLMLog(Base):
    __tablename__ = "llm_logs"

    llm_log_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(120), nullable=False)
    llm_config_id: Mapped[int | None] = mapped_column(ForeignKey("llm_configs.llm_config_id"))
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.prompt_id"))
    content_id: Mapped[int | None] = mapped_column(ForeignKey("content_items.content_id"))
    model_name: Mapped[str | None] = mapped_column(String(200))
    input_token_count: Mapped[int | None] = mapped_column(Integer)
    output_token_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="success")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("summary_date", name="uq_summary_date"),)

    summary_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary_date: Mapped[str] = mapped_column(String(20), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    successful_items: Mapped[int] = mapped_column(Integer, default=0)
    partial_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    source_counts_json: Mapped[str | None] = mapped_column(Text)
    sections_json: Mapped[str | None] = mapped_column(Text)
    markdown_text: Mapped[str | None] = mapped_column(Text)
    llm_summary_status: Mapped[str] = mapped_column(String(40), default="disabled")
    llm_summary_text: Mapped[str | None] = mapped_column(Text)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    setting_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BackupRecord(Base):
    __tablename__ = "backups"

    backup_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backup_type: Mapped[str] = mapped_column(String(40), default="manual")
    backup_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="success")
    integrity_status: Mapped[str | None] = mapped_column(String(80))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LongTermLog(Base):
    __tablename__ = "long_term_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(20), default="info")
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SessionLog(Base):
    __tablename__ = "session_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(20), default="info")
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FinancingEvent(Base):
    __tablename__ = "financing_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), default="financing")
    event_title: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    company_name_normalized: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    announced_date: Mapped[date | None] = mapped_column(Date)
    financing_round: Mapped[str | None] = mapped_column(String(80))
    amount_original: Mapped[str | None] = mapped_column(Text)
    amount_normalized: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(20))
    investors_json: Mapped[str | None] = mapped_column(Text)
    lead_investors_json: Mapped[str | None] = mapped_column(Text)
    event_summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    locked_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    contents: Mapped[list["EventContent"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    change_logs: Mapped[list["EventChangeLog"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventContent(Base):
    __tablename__ = "event_contents"
    __table_args__ = (
        UniqueConstraint("event_id", "content_id", name="uq_event_content"),
        UniqueConstraint("content_id", name="uq_event_content_single"),
    )

    event_content_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("financing_events.event_id"), nullable=False, index=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_items.content_id"), nullable=False, index=True)
    is_primary_source: Mapped[bool] = mapped_column(Boolean, default=False)
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    match_reasons_json: Mapped[str | None] = mapped_column(Text)
    association_source: Mapped[str] = mapped_column(String(30), default="automatic")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    event: Mapped[FinancingEvent] = relationship(back_populates="contents")
    content: Mapped[ContentItem] = relationship()


class EventChangeLog(Base):
    __tablename__ = "event_change_logs"

    change_log_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("financing_events.event_id"), index=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    event: Mapped[FinancingEvent | None] = relationship(back_populates="change_logs")


class WatchItem(Base):
    __tablename__ = "watch_items"
    __table_args__ = (UniqueConstraint("active_target_key", name="uq_watch_active_target"),)

    watch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    active_target_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_title_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    target_summary_snapshot: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="watching")
    reason: Mapped[str | None] = mapped_column(Text)
    next_review_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Report(Base):
    __tablename__ = "reports"

    report_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    latest_version_number: Mapped[int] = mapped_column(Integer, default=0)
    last_generation_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    inputs: Mapped[list["ReportInput"]] = relationship(back_populates="report", cascade="all, delete-orphan")
    versions: Mapped[list["ReportVersion"]] = relationship(back_populates="report", cascade="all, delete-orphan")
    exports: Mapped[list["ReportExport"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class ReportInput(Base):
    __tablename__ = "report_inputs"

    report_input_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.report_id"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)

    report: Mapped[Report] = relationship(back_populates="inputs")


class ReportVersion(Base):
    __tablename__ = "report_versions"
    __table_args__ = (UniqueConstraint("report_id", "version_number", name="uq_report_version"),)

    report_version_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.report_id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_source: Mapped[str] = mapped_column(String(30), nullable=False)
    template_version: Mapped[str] = mapped_column(String(40), default="v0.3")
    prompt_id: Mapped[int | None] = mapped_column(Integer)
    model_name: Mapped[str | None] = mapped_column(String(200))
    input_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    markdown_text: Mapped[str] = mapped_column(Text, default="")
    generation_status: Mapped[str] = mapped_column(String(30), default="success")
    generation_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    report: Mapped[Report] = relationship(back_populates="versions")


class ReportExport(Base):
    __tablename__ = "report_exports"

    report_export_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.report_id"), nullable=False, index=True)
    report_version_id: Mapped[int] = mapped_column(ForeignKey("report_versions.report_version_id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    exported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    report: Mapped[Report] = relationship(back_populates="exports")

