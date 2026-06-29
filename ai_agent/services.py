from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import traceback
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from bs4 import BeautifulSoup
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import (
    ARCHIVE_DIR,
    BACKUP_DIR,
    DB_PATH,
    DEFAULT_CACHE_HOURS,
    TIMEZONE_NAME,
    TZ,
    apply_network_proxy_settings,
    normalize_proxy_mode,
)
from .database import SessionLocal
from .models import (
    BackupRecord,
    ContentCache,
    ContentEntity,
    ContentItem,
    ContentTag,
    CrawlError,
    CrawlRun,
    DailySummary,
    Entity,
    LLMConfig,
    LLMLog,
    LLMTask,
    LongTermLog,
    Prompt,
    SessionLog,
    Source,
    SystemSetting,
    TagDefinition,
)
from .security import decrypt_value, encrypt_value
from .utils import canonicalize_url, clean_text, fingerprint, first_chars, json_dumps, json_loads, now_bj, parse_datetime


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
}

ARTICLE_SOURCE_CATEGORIES = {
    "venture_media",
    "ai_media",
    "tech_business_media",
    "official_news",
    "official_research",
}
INTRINSIC_AI_SOURCE_CATEGORIES = {"ai_media", "official_news", "official_research"}
SIGNAL_SOURCE_CATEGORIES = {"ai_research_signal", "ai_product_signal", "startup_directory"}
OLDER_ARTICLE_STOP_STREAK = 10
OVERSEAS_DATE_LENIENT_HOST_MARKERS = (
    "a16z.com",
    "techcrunch.com",
    "theverge.com",
    "venturebeat.com",
    "the-decoder.com",
    "crunchbase.com",
    "deepmind.google",
    "ai.meta.com",
)
SUMMARY_CHAR_LIMIT = 180
SUMMARY_SENTENCE_MIN_CHARS = 80
FINANCING_REPORT_SETTING_KEY = "weekly_financing_report_dir"
FINANCING_ROUND_PATTERNS = [
    "Pre-A轮",
    "Pre-A",
    "种子轮",
    "天使轮",
    "A轮",
    "B轮",
    "C轮",
    "D轮",
    "E轮",
    "F轮",
    "战略融资",
    "股权融资",
    "pre-seed",
    "seed",
    "series a",
    "series b",
    "series c",
    "series d",
    "series e",
]
FINANCING_KEYWORDS = (
    "融资",
    "投资",
    "领投",
    "跟投",
    "参投",
    "募资",
    "并购",
    "收购",
    "funding",
    "financing",
    "investment",
    "raised",
    "raises",
    "round",
    "seed",
    "series",
)
ENTITY_STOPWORDS = {
    "AI",
    "VC",
    "PE",
    "LLM",
    "GPU",
    "AIGC",
    "OpenAI",
    "Anthropic",
    "Meta",
    "Google",
    "NVIDIA",
    "AMD",
    "Microsoft",
    "Amazon",
    "Inc",
    "CEO",
    "Labs",
    "AI Labs",
}
AI_RELATED_KEYWORDS = (
    "人工智能",
    "生成式",
    "大模型",
    "基础模型",
    "多模态",
    "智能体",
    "算力",
    "推理",
    "训练",
    "语料",
    "机器人",
    "具身智能",
    "自动驾驶",
    "ai infra",
    "ai agent",
    "aigc",
    "llm",
    "rag",
    "mcp",
    "gpu",
    "openai",
    "anthropic",
    "deepseek",
    "claude",
    "gemini",
    "chatgpt",
    "copilot",
)


def estimate_llm_tokens(*parts: str) -> int:
    total_chars = sum(len(part or "") for part in parts)
    return max(1, (total_chars + 3) // 4)


def summary_chars(text: str, limit: int = SUMMARY_CHAR_LIMIT) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    window = text[:limit]
    for index in range(len(window) - 1, SUMMARY_SENTENCE_MIN_CHARS - 1, -1):
        if window[index] in "。！？!?；;":
            return window[: index + 1].strip()
    comma_cut = max(window.rfind("，"), window.rfind(","), window.rfind("、"))
    if comma_cut >= SUMMARY_SENTENCE_MIN_CHARS:
        return window[:comma_cut].strip() + "..."
    return window.rstrip() + "..."


def week_range(target: datetime | None = None, previous: bool = False) -> tuple[datetime, datetime]:
    current = target or db_now()
    week_start_date = current.date() - timedelta(days=current.date().weekday())
    week_start = datetime.combine(week_start_date, dt_time.min)
    if previous:
        return week_start - timedelta(days=7), week_start
    return week_start, week_start + timedelta(days=7)


def display_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


def content_preview_item(item: ContentItem) -> dict[str, Any]:
    display_time = item.publish_time or item.crawl_time
    return {
        "content_id": item.content_id,
        "title": item.title,
        "source_name": item.source_name,
        "display_time": display_datetime(display_time),
        "summary": item.summary or "暂无简介",
        "llm_status": item.llm_status,
        "url": item.url,
    }


def normalize_dedupe_text(text: str | None) -> str:
    text = (text or "").lower()
    text = re.sub(r"[\s_·•・\-—–|｜:：,，.。;；!！?？'\"“”‘’「」《》【】（）()\[\]{}]+", "", text)
    return text


def normalize_company_name(name: str) -> str:
    name = re.sub(
        r"^(?:首发|融资|公司|企业|项目|团队|具身智能|基础设施|供应商|开发商|初创公司|创业公司|平台|专注于|报道称)",
        "",
        name.strip(),
    )
    name = re.sub(r"(?:公司|企业|项目|团队|平台|科技|智能|医疗|机器人)$", "", name).strip()
    return normalize_dedupe_text(name)


def extract_company_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    for match in re.finditer(r"[「“\"]([^」”\"]{2,60})[」”\"]", text):
        candidate = normalize_company_name(match.group(1))
        if 2 <= len(candidate) <= 60:
            candidates.add(candidate)

    action_pattern = re.compile(
        r"([A-Za-z][A-Za-z0-9._ -]{1,60}|[\u4e00-\u9fffA-Za-z0-9._ -]{2,40})"
        r"(?:获|获得|完成|宣布完成| raises | raised | lands | scores | secures | closes )"
        r".{0,30}?(?:融资|funding|round|investment)",
        flags=re.IGNORECASE,
    )
    for match in action_pattern.finditer(text):
        candidate = normalize_company_name(match.group(1))
        if 2 <= len(candidate) <= 60:
            candidates.add(candidate)

    for match in re.finditer(r"\b[A-Z][A-Za-z0-9._]*(?:[ -][A-Z][A-Za-z0-9._]*){0,3}\b", text):
        raw = match.group(0).strip()
        if raw in ENTITY_STOPWORDS:
            continue
        candidate = normalize_company_name(raw)
        if 3 <= len(candidate) <= 50:
            candidates.add(candidate)

    for match in re.finditer(r"([\u4e00-\u9fff]{2,4})(?:AI)?创业", text):
        candidate = normalize_company_name(match.group(1))
        if 2 <= len(candidate) <= 12 and candidate not in {"官宣", "融资", "完成", "获得"}:
            candidates.add(candidate)

    for match in re.finditer(r"([\u4e00-\u9fff]{2,4})等.{0,25}(?:创办|创立|创办了|创立了|联合创办|联合创立)", text):
        candidate = normalize_company_name(match.group(1))
        if 2 <= len(candidate) <= 12:
            candidates.add(candidate)

    return candidates


def extract_company_display_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"[「“\"]([^」”\"]{2,60})[」”\"]", text):
        candidates.append(clean_text(match.group(1)))
    action_pattern = re.compile(
        r"([A-Za-z][A-Za-z0-9._ -]{1,60}|[\u4e00-\u9fffA-Za-z0-9._ -]{2,40})"
        r"(?:获|获得|完成|宣布完成| raises | raised | lands | scores | secures | closes )",
        flags=re.IGNORECASE,
    )
    for match in action_pattern.finditer(text):
        raw = clean_text(match.group(1))
        raw = re.sub(
            r"^(?:首发|融资|公司|企业|项目|团队|具身智能|基础设施|供应商|开发商|初创公司|创业公司|平台|专注于|报道称)",
            "",
            raw,
        ).strip()
        if 2 <= len(raw) <= 60:
            candidates.append(raw)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def extract_financing_amounts(text: str) -> set[str]:
    amounts: set[str] = set()
    for match in re.finditer(
        r"(?:近|超|约|逾|数)?\d+(?:\.\d+)?\s*(?:万|亿)?\s*(?:美元|美金|人民币|元|欧元|英镑|港元|dollars?|usd|rmb)(?:\+)?",
        text,
        flags=re.IGNORECASE,
    ):
        amounts.add(normalize_dedupe_text(match.group(0)))
    for match in re.finditer(r"(?:近|超|约|逾)?(?:亿元|千万元|百万元|千万美元|百万美元|千万人民币|百万人民币)", text, flags=re.IGNORECASE):
        amounts.add(normalize_dedupe_text(match.group(0)))
    for match in re.finditer(r"(?:数千万|数亿|千万级|亿元级|百万级|billion|million)", text, flags=re.IGNORECASE):
        amounts.add(normalize_dedupe_text(match.group(0)))
    return amounts


def extract_financing_amount_texts(text: str) -> list[str]:
    amounts: list[str] = []
    for match in re.finditer(
        r"(?:近|超|约|逾|数)?\d+(?:\.\d+)?\s*(?:万|亿)?\s*(?:美元|美金|人民币|元|欧元|英镑|港元|dollars?|usd|rmb)(?:\+)?",
        text,
        flags=re.IGNORECASE,
    ):
        amounts.append(clean_text(match.group(0)))
    for match in re.finditer(r"(?:近|超|约|逾)?(?:亿元|千万元|百万元|千万美元|百万美元|千万人民币|百万人民币)", text, flags=re.IGNORECASE):
        amounts.append(clean_text(match.group(0)))
    for match in re.finditer(r"(?:数千万|数亿|千万级|亿元级|百万级|billion|million)", text, flags=re.IGNORECASE):
        amounts.append(clean_text(match.group(0)))
    return list(dict.fromkeys(amount for amount in amounts if amount))


def extract_financing_rounds(text: str) -> set[str]:
    normalized = text.lower()
    rounds: set[str] = set()
    for pattern in FINANCING_ROUND_PATTERNS:
        if pattern.lower() in normalized:
            rounds.add(normalize_dedupe_text(pattern))
    return rounds


def extract_financing_round_texts(text: str) -> list[str]:
    normalized = text.lower()
    rounds = [pattern for pattern in FINANCING_ROUND_PATTERNS if pattern.lower() in normalized]
    return list(dict.fromkeys(rounds))


def extract_investor_texts(text: str) -> list[str]:
    investors: list[str] = []
    patterns = [
        r"(?:本轮由|由)([^。；;，,]{2,80}?)(?:领投|联合领投|投资|参投)",
        r"(?:投资方包括|投资方为|投资机构包括)([^。；;]{2,100})",
        r"(?:领投方为|领投方是)([^。；;，,]{2,80})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            raw = clean_text(match.group(1))
            for part in re.split(r"[、,，和及与]", raw):
                part = clean_text(part)
                part = re.sub(r"^(?:本轮|由|同时|并由)", "", part).strip()
                if 2 <= len(part) <= 40:
                    investors.append(part)
    return list(dict.fromkeys(investor for investor in investors if investor))


def title_tokens(text: str) -> set[str]:
    normalized = normalize_dedupe_text(text)
    tokens = set(re.findall(r"[a-z0-9]{3,}", normalized))
    tokens.update(normalized[index : index + 2] for index in range(max(len(normalized) - 1, 0)))
    return {token for token in tokens if token}


def build_financing_signature(item: ContentItem) -> dict[str, Any]:
    text = f"{item.title} {item.summary or ''}"
    display_time = item.publish_time or item.crawl_time
    companies = extract_company_candidates(text)
    for rel in item.entities:
        entity = rel.entity
        if entity and entity.entity_type == "company":
            candidate = normalize_company_name(entity.display_name or entity.canonical_name or "")
            if candidate:
                companies.add(candidate)
    return {
        "content_id": item.content_id,
        "date": display_time.date() if display_time else None,
        "companies": companies,
        "amounts": extract_financing_amounts(text),
        "rounds": extract_financing_rounds(text),
        "title_norm": normalize_dedupe_text(item.title),
        "tokens": title_tokens(text),
    }


def financing_duplicate_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    score = 0.0
    left_companies = left["companies"]
    right_companies = right["companies"]
    if left_companies and right_companies:
        if left_companies & right_companies:
            score += 0.56
        elif any(a in b or b in a for a in left_companies for b in right_companies if len(a) >= 4 and len(b) >= 4):
            score += 0.42

    if left["amounts"] and right["amounts"] and left["amounts"] & right["amounts"]:
        score += 0.2
    if left["rounds"] and right["rounds"] and left["rounds"] & right["rounds"]:
        score += 0.14

    title_similarity = SequenceMatcher(None, left["title_norm"], right["title_norm"]).ratio()
    if title_similarity >= 0.82:
        score += 0.3
    elif title_similarity >= 0.65:
        score += 0.18

    token_union = left["tokens"] | right["tokens"]
    token_jaccard = len(left["tokens"] & right["tokens"]) / len(token_union) if token_union else 0.0
    if token_jaccard >= 0.45:
        score += 0.18
    elif token_jaccard >= 0.28:
        score += 0.1

    if left["date"] and right["date"] and abs((left["date"] - right["date"]).days) <= 7:
        score += 0.08
    return min(score, 1.0)


class CrawlProgress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False,
            "status": "idle",
            "message": "尚未开始抓取",
            "current_source": "",
            "completed_sources": 0,
            "total_sources": 0,
            "new_items": 0,
            "failed_items": 0,
            "started_at": "",
            "finished_at": "",
        }

    def start(self, total_sources: int, message: str = "开始抓取", started_at: datetime | None = None) -> None:
        started_at = started_at or db_now()
        with self._lock:
            self._state.update(
                {
                    "running": True,
                    "status": "running",
                    "message": message,
                    "current_source": "",
                    "completed_sources": 0,
                    "total_sources": total_sources,
                    "new_items": 0,
                    "failed_items": 0,
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "finished_at": "",
                }
            )

    def source_started(self, source_name: str) -> None:
        with self._lock:
            self._state["current_source"] = source_name
            self._state["message"] = f"正在抓取：{source_name}"

    def source_finished(self, source_name: str, new_items: int, failed_items: int, ok: bool = True) -> None:
        with self._lock:
            self._state["completed_sources"] += 1
            self._state["new_items"] += new_items
            self._state["failed_items"] += failed_items
            self._state["current_source"] = source_name
            self._state["message"] = f"{source_name} 完成，新增 {new_items} 条" if ok else f"{source_name} 抓取失败"

    def finish(self, status: str, message: str) -> None:
        with self._lock:
            self._state["running"] = False
            self._state["status"] = status
            self._state["message"] = message
            self._state["current_source"] = ""
            self._state["finished_at"] = db_now().isoformat(timespec="seconds")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
        total = state.get("total_sources") or 0
        completed = state.get("completed_sources") or 0
        state["percent"] = int((completed / total) * 100) if total else (100 if not state.get("running") else 0)
        return state

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._state.get("running"))


crawl_progress = CrawlProgress()


_llm_semaphore_lock = threading.Lock()
_llm_semaphore_limit = 0
_llm_semaphore = threading.BoundedSemaphore(1)


def llm_semaphore_for_limit(limit: int) -> threading.BoundedSemaphore:
    global _llm_semaphore, _llm_semaphore_limit
    limit = max(1, min(limit, 6))
    with _llm_semaphore_lock:
        if limit != _llm_semaphore_limit:
            _llm_semaphore = threading.BoundedSemaphore(limit)
            _llm_semaphore_limit = limit
        return _llm_semaphore


def db_now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def get_setting(db: Session, key: str, default: str = "") -> str:
    setting = db.get(SystemSetting, key)
    return setting.setting_value if setting else default


def get_int_setting(db: Session, key: str, default: int, minimum: int = 1, maximum: int = 20) -> int:
    try:
        value = int(get_setting(db, key, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def set_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(SystemSetting, key)
    if setting:
        setting.setting_value = value
    else:
        db.add(SystemSetting(setting_key=key, setting_value=value))


def configured_proxy_settings(db: Session) -> dict[str, str]:
    return {
        "mode": normalize_proxy_mode(get_setting(db, "network_proxy_mode", "off")),
        "url": get_setting(db, "network_proxy_url", ""),
        "no_proxy": get_setting(db, "network_proxy_no_proxy", ""),
    }


def apply_configured_proxy_settings(db: Session) -> dict[str, str]:
    settings = configured_proxy_settings(db)
    return apply_network_proxy_settings(
        mode=settings["mode"],
        proxy_url=settings["url"],
        no_proxy=settings["no_proxy"],
    )


def add_session_log(db: Session, event_type: str, message: str, level: str = "info") -> None:
    db.add(SessionLog(event_type=event_type, message=message, level=level))


def add_long_log(db: Session, event_type: str, message: str, level: str = "info") -> None:
    db.add(LongTermLog(event_type=event_type, message=message, level=level))


def clear_session_logs(db: Session) -> None:
    db.query(SessionLog).delete()
    db.commit()


def ensure_tag(db: Session, tag_key: str, tag_value: str) -> TagDefinition:
    tag = db.scalar(select(TagDefinition).where(TagDefinition.tag_key == tag_key, TagDefinition.tag_value == tag_value))
    if tag:
        return tag
    tag = TagDefinition(tag_key=tag_key, tag_value=tag_value, display_name_cn=tag_value, display_name_en=tag_value)
    db.add(tag)
    db.flush()
    return tag


def attach_tag(
    db: Session,
    content: ContentItem,
    tag_key: str,
    tag_value: str,
    source: str = "rule",
    confidence: float | None = 1.0,
) -> None:
    if not tag_value:
        return
    existing = next((tag for tag in content.tags if tag.tag_key == tag_key and tag.tag_value == tag_value), None)
    if existing:
        return
    tag = ensure_tag(db, tag_key, tag_value)
    db.add(
        ContentTag(
            content_id=content.content_id,
            tag_id=tag.tag_id,
            tag_key=tag_key,
            tag_value=tag_value,
            source=source,
            confidence=confidence,
        )
    )


def ensure_entity(db: Session, entity_type: str, name: str) -> Entity:
    normalized = clean_text(name)
    entity = db.scalar(select(Entity).where(Entity.entity_type == entity_type, Entity.canonical_name == normalized))
    if entity:
        return entity
    entity = Entity(entity_type=entity_type, canonical_name=normalized, display_name=normalized)
    db.add(entity)
    db.flush()
    return entity


def attach_entity(
    db: Session,
    content: ContentItem,
    entity_type: str,
    name: str,
    source: str = "llm",
    confidence: float | None = 0.8,
) -> None:
    if entity_type not in {"company", "product", "investor", "person", "org"} or not clean_text(name):
        return
    entity = ensure_entity(db, entity_type, name)
    existing = next((rel for rel in content.entities if rel.entity_id == entity.entity_id), None)
    if existing:
        return
    db.add(ContentEntity(content_id=content.content_id, entity_id=entity.entity_id, source=source, confidence=confidence))


@dataclass
class ExtractedItem:
    title: str
    url: str
    summary: str = ""
    clean_text: str = ""
    publish_time: datetime | None = None
    publish_time_status: str = "missing"
    language: str | None = None
    metadata: dict[str, Any] | None = None


class LLMService:
    def content_metadata_ready(self, db: Session) -> bool:
        return self.task_ready(db, "process_content_metadata")

    def task_ready(self, db: Session, task_name: str) -> bool:
        task = db.scalar(select(LLMTask).where(LLMTask.task_name == task_name, LLMTask.enabled.is_(True)))
        if not task or not task.llm_config_id or not task.prompt_id:
            return False
        config = db.get(LLMConfig, task.llm_config_id)
        prompt = db.get(Prompt, task.prompt_id)
        if not config or not prompt or not config.enabled or not prompt.enabled:
            return False
        return bool(decrypt_value(config.encrypted_api_key) and decrypt_value(config.encrypted_model_name))

    def ai_financing_relevance_ready(self, db: Session) -> bool:
        return self.task_ready(db, "classify_ai_financing_relevance")

    def _task_assets(self, db: Session, task_name: str) -> tuple[LLMTask, LLMConfig, Prompt, str, str, str] | None:
        task = db.scalar(select(LLMTask).where(LLMTask.task_name == task_name, LLMTask.enabled.is_(True)))
        if not task or not task.llm_config_id or not task.prompt_id:
            return None
        config = db.get(LLMConfig, task.llm_config_id)
        prompt = db.get(Prompt, task.prompt_id)
        if not config or not prompt or not config.enabled or not prompt.enabled:
            return None
        base_url = decrypt_value(config.encrypted_base_url)
        api_key = decrypt_value(config.encrypted_api_key)
        model_name = decrypt_value(config.encrypted_model_name)
        if not api_key or not model_name:
            return None
        return task, config, prompt, base_url, api_key, model_name

    def generate_financing_report(
        self,
        db: Session,
        task_name: str,
        payload: dict[str, Any],
    ) -> str:
        assets = self._task_assets(db, task_name)
        if not assets:
            raise ValueError("\u8bf7\u5148\u5728 LLM / Prompt \u9875\u9762\u4e3a\u8be5\u878d\u8d44\u603b\u7ed3\u4efb\u52a1\u9009\u62e9\u6a21\u578b\u548c Prompt")
        task, config, prompt, base_url, api_key, model_name = assets

        started = time.monotonic()
        try:
            user_content = json_dumps(payload)
            estimated_input_tokens = estimate_llm_tokens(prompt.prompt_text, user_content)
            context_window_tokens = max(1, config.context_window_tokens or 1_000_000)
            if estimated_input_tokens > context_window_tokens:
                raise RuntimeError(
                    f"Input is about {estimated_input_tokens} tokens, exceeding model context window {context_window_tokens} tokens"
                )
            output_text = self._call_model(
                config,
                base_url,
                api_key,
                model_name,
                prompt.prompt_text,
                user_content,
                concurrency_limit=get_int_setting(db, "llm_parallelism", 2, 1, 6),
                require_json=False,
            ).strip()
            if output_text.startswith("```"):
                lines = output_text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                output_text = "\n".join(lines).strip()
            try:
                wrapped_output = json.loads(output_text)
            except (json.JSONDecodeError, TypeError):
                wrapped_output = None
            if isinstance(wrapped_output, dict):
                for key in ("report", "markdown", "content", "text"):
                    value = wrapped_output.get(key)
                    if isinstance(value, str) and value.strip():
                        output_text = value.strip()
                        break

            if output_text.startswith("```"):
                lines = output_text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                output_text = "\n".join(lines).strip()

            if not output_text:
                raise RuntimeError("\u6a21\u578b\u8fd4\u56de\u4e86\u7a7a\u62a5\u544a")
        except Exception as exc:
            self._record_financing_report_log(
                db,
                task,
                config,
                prompt,
                model_name,
                started,
                "failed",
                first_chars(str(exc), 500),
                commit=True,
            )
            raise RuntimeError(f"\u878d\u8d44\u603b\u7ed3\u751f\u6210\u5931\u8d25\uff1a{first_chars(str(exc), 220)}") from exc

        self._record_financing_report_log(db, task, config, prompt, model_name, started, "success")
        return output_text

    def _record_financing_report_log(
        self,
        db: Session,
        task: LLMTask,
        config: LLMConfig,
        prompt: Prompt,
        model_name: str,
        started: float,
        status: str,
        error_message: str | None = None,
        commit: bool = False,
    ) -> None:
        try:
            db.add(
                LLMLog(
                    task_name=task.task_name,
                    llm_config_id=config.llm_config_id,
                    prompt_id=prompt.prompt_id,
                    content_id=None,
                    model_name=model_name,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status=status,
                    error_message=error_message,
                )
            )
            if commit:
                db.commit()
            else:
                db.flush()
        except Exception:
            db.rollback()

    def classify_ai_financing_relevance(
        self,
        db: Session,
        content: ContentItem,
        clean_body: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        assets = self._task_assets(db, "classify_ai_financing_relevance")
        if not assets:
            return None
        task, config, prompt, base_url, api_key, model_name = assets

        payload = {
            "title": content.title,
            "source": content.source_name,
            "url": content.url,
            "publish_time": content.publish_time.isoformat() if content.publish_time else None,
            "summary": content.summary,
            "metadata": metadata or {},
            "text": first_chars(clean_body or content.summary or "", 12000),
        }

        started = time.monotonic()
        try:
            output_text = self._call_model(
                config,
                base_url,
                api_key,
                model_name,
                prompt.prompt_text,
                json_dumps(payload),
                concurrency_limit=get_int_setting(db, "llm_parallelism", 2, 1, 6),
            )
            data = self._extract_json(output_text)
            db.add(
                LLMLog(
                    task_name=task.task_name,
                    llm_config_id=config.llm_config_id,
                    prompt_id=prompt.prompt_id,
                    content_id=content.content_id,
                    model_name=model_name,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status="success",
                )
            )
            return data
        except Exception as exc:  # noqa: BLE001
            db.add(
                LLMLog(
                    task_name=task.task_name,
                    llm_config_id=config.llm_config_id,
                    prompt_id=prompt.prompt_id,
                    content_id=content.content_id,
                    model_name=model_name,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status="failed",
                    error_message=str(exc),
                )
            )
            return None

    def process_content_metadata(self, db: Session, content: ContentItem, clean_body: str | None) -> dict[str, Any] | None:
        task = db.scalar(select(LLMTask).where(LLMTask.task_name == "process_content_metadata", LLMTask.enabled.is_(True)))
        if not task or not task.llm_config_id or not task.prompt_id:
            content.llm_status = "not_configured"
            content.extraction_status = "partial"
            return None

        config = db.get(LLMConfig, task.llm_config_id)
        prompt = db.get(Prompt, task.prompt_id)
        if not config or not prompt or not config.enabled or not prompt.enabled:
            content.llm_status = "not_configured"
            content.extraction_status = "partial"
            return None

        base_url = decrypt_value(config.encrypted_base_url)
        api_key = decrypt_value(config.encrypted_api_key)
        model_name = decrypt_value(config.encrypted_model_name)
        if not api_key or not model_name:
            content.llm_status = "not_configured"
            content.extraction_status = "partial"
            return None

        payload = {
            "title": content.title,
            "source": content.source_name,
            "url": content.url,
            "publish_time": content.publish_time.isoformat() if content.publish_time else None,
            "text": first_chars(clean_body or content.summary or "", 12000),
        }

        started = time.monotonic()
        try:
            output_text = self._call_model(
                config,
                base_url,
                api_key,
                model_name,
                prompt.prompt_text,
                json_dumps(payload),
                concurrency_limit=get_int_setting(db, "llm_parallelism", 2, 1, 6),
            )
            data = self._extract_json(output_text)
            content.llm_status = "success"
            content.extraction_status = "processed"
            db.add(
                LLMLog(
                    task_name="process_content_metadata",
                    llm_config_id=config.llm_config_id,
                    prompt_id=prompt.prompt_id,
                    content_id=content.content_id,
                    model_name=model_name,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status="success",
                )
            )
            return data
        except Exception as exc:  # noqa: BLE001
            content.llm_status = "failed"
            content.extraction_status = "partial"
            db.add(
                LLMLog(
                    task_name="process_content_metadata",
                    llm_config_id=config.llm_config_id,
                    prompt_id=prompt.prompt_id,
                    content_id=content.content_id,
                    model_name=model_name,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status="failed",
                    error_message=str(exc),
                )
            )
            return None

    def test_config(self, db: Session, config: LLMConfig) -> dict[str, Any]:
        base_url = decrypt_value(config.encrypted_base_url)
        api_key = decrypt_value(config.encrypted_api_key)
        model_name = decrypt_value(config.encrypted_model_name)
        if not api_key or not model_name:
            return {"ok": False, "message": "API Key 或 Model Name 为空", "latency_ms": None}

        started = time.monotonic()
        try:
            output_text = self._call_model(
                config,
                base_url,
                api_key,
                model_name,
                "你是一个 LLM 连接测试助手。请只输出 JSON。",
                '请回复 JSON：{"ok":true,"message":"connected"}',
                concurrency_limit=get_int_setting(db, "llm_parallelism", 2, 1, 6),
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            db.add(
                LLMLog(
                    task_name="connection_test",
                    llm_config_id=config.llm_config_id,
                    prompt_id=None,
                    content_id=None,
                    model_name=model_name,
                    latency_ms=latency_ms,
                    status="success",
                )
            )
            add_session_log(db, "llm_connection_test", f"{config.config_name} 连接测试成功，用时 {latency_ms} ms")
            db.commit()
            return {
                "ok": True,
                "message": f"连接成功，用时 {latency_ms} ms",
                "latency_ms": latency_ms,
                "output": first_chars(output_text, 120),
            }
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            db.add(
                LLMLog(
                    task_name="connection_test",
                    llm_config_id=config.llm_config_id,
                    prompt_id=None,
                    content_id=None,
                    model_name=model_name,
                    latency_ms=latency_ms,
                    status="failed",
                    error_message=str(exc),
                )
            )
            add_session_log(db, "llm_connection_test_failed", f"{config.config_name} 连接测试失败：{first_chars(str(exc), 160)}", "error")
            db.commit()
            return {"ok": False, "message": first_chars(str(exc), 220), "latency_ms": latency_ms}

    def _call_model(
        self,
        config: LLMConfig,
        base_url: str,
        api_key: str,
        model_name: str,
        system_prompt: str,
        user_content: str,
        concurrency_limit: int | None = None,
        require_json: bool = True,
    ) -> str:
        semaphore = llm_semaphore_for_limit(concurrency_limit or 2)
        semaphore.acquire()
        try:
            retry_count = max(0, config.max_retries or 0)
            for attempt in range(retry_count + 1):
                try:
                    if config.provider_type == "anthropic":
                        endpoint = (base_url.rstrip("/") if base_url else "https://api.anthropic.com") + "/v1/messages"
                        response = requests.post(
                            endpoint,
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json",
                            },
                            json={
                                "model": model_name,
                                "max_tokens": 1200 if require_json else 8192,
                                "system": system_prompt,
                                "messages": [{"role": "user", "content": user_content}],
                            },
                            timeout=config.timeout_seconds,
                        )
                        response.raise_for_status()
                        data = response.json()
                        return self._extract_anthropic_text(data)

                    endpoint_base = base_url.rstrip("/") if base_url else "https://api.openai.com/v1"
                    endpoint = endpoint_base if endpoint_base.endswith("/chat/completions") else endpoint_base + "/chat/completions"
                    request_body: dict[str, Any] = {
                        "model": model_name,
                        "temperature": 0.2,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                    }
                    if require_json:
                        request_body["response_format"] = {"type": "json_object"}
                    response = requests.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=request_body,
                        timeout=config.timeout_seconds,
                    )
                    response.raise_for_status()
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
                except requests.RequestException as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    retryable = status is None or status in {408, 409, 425, 429} or status >= 500
                    if attempt >= retry_count or not retryable:
                        raise
                    time.sleep(1.5 * (attempt + 1))
            raise RuntimeError("LLM 请求未返回结果")
        finally:
            semaphore.release()

    def _extract_anthropic_text(self, data: dict[str, Any]) -> str:
        content = data.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "".join(parts)
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
                if isinstance(first.get("text"), str):
                    return first["text"]
        return ""

    def _extract_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        return json.loads(text)


class CrawlService:
    def __init__(self) -> None:
        self.llm = LLMService()

    def _run_source_worker(
        self,
        source_id: int,
        parent_run_id: int | None,
        run_timestamp: datetime,
    ) -> dict[str, Any]:
        with SessionLocal() as worker_db:
            source = worker_db.get(Source, source_id)
            if not source:
                return {
                    "source_id": source_id,
                    "source_name": f"source#{source_id}",
                    "status": "failed",
                    "total_items": 0,
                    "new_items": 0,
                    "failed_items": 1,
                    "message": "信息源不存在",
                }
            crawl_progress.source_started(source.source_name)
            source_name = source.source_name
            try:
                run = CrawlService().run_source(
                    worker_db,
                    source,
                    parent_run_id=parent_run_id,
                    run_timestamp=run_timestamp,
                )
                return {
                    "source_id": source_id,
                    "source_name": source_name,
                    "status": run.status,
                    "total_items": run.total_items or 0,
                    "new_items": run.new_items or 0,
                    "failed_items": run.failed_items or 0,
                    "message": run.message or "",
                }
            except Exception as exc:  # noqa: BLE001
                worker_db.rollback()
                run = CrawlRun(
                    source_id=source_id,
                    status="failed",
                    started_at=run_timestamp,
                    finished_at=db_now(),
                    total_items=0,
                    new_items=0,
                    failed_items=1,
                    message=str(exc),
                )
                worker_db.add(run)
                worker_db.flush()
                self._record_error(
                    worker_db,
                    source,
                    run.crawl_run_id,
                    "source_worker_failed",
                    str(exc),
                    source.source_url,
                    traceback.format_exc(),
                )
                add_session_log(worker_db, "source_crawl_failed", f"{source_name}: {exc}", "error")
                worker_db.commit()
                return {
                    "source_id": source_id,
                    "source_name": source_name,
                    "status": "failed",
                    "total_items": 0,
                    "new_items": 0,
                    "failed_items": 1,
                    "message": str(exc),
                }

    def run_all_sources(self, db: Session, manual: bool = False, run_timestamp: datetime | None = None) -> CrawlRun:
        run_timestamp = run_timestamp or db_now()
        run = CrawlRun(source_id=None, status="running", started_at=run_timestamp)
        db.add(run)
        db.flush()
        add_session_log(db, "crawl_start", "开始抓取所有已启用信息源")
        db.commit()

        run_id = run.crawl_run_id
        try:
            sources = db.scalars(select(Source).where(Source.enabled.is_(True)).order_by(Source.source_id)).all()
            crawl_progress.start(len(sources), "开始手动抓取" if manual else "开始自动抓取", run_timestamp)
            source_parallelism = get_int_setting(db, "source_parallelism", 4, 1, 12)
            add_session_log(
                db,
                "crawl_concurrency",
                f"并发设置：信息源 {source_parallelism}，LLM {get_int_setting(db, 'llm_parallelism', 2, 1, 6)}",
            )
            db.commit()
            total = new_items = failed_items = 0
            if source_parallelism <= 1:
                for source in sources:
                    crawl_progress.source_started(source.source_name)
                    try:
                        source_run = self.run_source(db, source, parent_run_id=run.crawl_run_id, run_timestamp=run_timestamp)
                        total += source_run.total_items
                        new_items += source_run.new_items
                        failed_items += source_run.failed_items
                        crawl_progress.source_finished(
                            source.source_name,
                            source_run.new_items,
                            source_run.failed_items,
                            ok=source_run.status == "success",
                        )
                    except Exception as exc:  # noqa: BLE001
                        failed_items += 1
                        crawl_progress.source_finished(source.source_name, 0, 1, ok=False)
                        self._record_error(db, source, run.crawl_run_id, "source_run_failed", str(exc), source.source_url)
            else:
                source_jobs = [(source.source_id, source.source_name) for source in sources]
                with ThreadPoolExecutor(max_workers=min(source_parallelism, len(source_jobs) or 1)) as executor:
                    futures = {
                        executor.submit(self._run_source_worker, source_id, run.crawl_run_id, run_timestamp): source_name
                        for source_id, source_name in source_jobs
                    }
                    for future in as_completed(futures):
                        fallback_source_name = futures[future]
                        try:
                            result = future.result()
                        except Exception as exc:  # noqa: BLE001
                            result = {
                                "source_name": fallback_source_name,
                                "status": "failed",
                                "total_items": 0,
                                "new_items": 0,
                                "failed_items": 1,
                                "message": str(exc),
                            }
                            self._record_error(
                                db,
                                None,
                                run.crawl_run_id,
                                "source_future_failed",
                                str(exc),
                                None,
                                traceback.format_exc(),
                            )
                        total += int(result.get("total_items") or 0)
                        new_items += int(result.get("new_items") or 0)
                        failed_items += int(result.get("failed_items") or 0)
                        crawl_progress.source_finished(
                            result.get("source_name") or fallback_source_name,
                            int(result.get("new_items") or 0),
                            int(result.get("failed_items") or 0),
                            ok=result.get("status") == "success",
                        )

            run.total_items = total
            run.new_items = new_items
            run.failed_items = failed_items
            run.status = "success" if failed_items == 0 else "partial_success"
            run.finished_at = db_now()
            run.message = f"新增 {new_items} 条，失败 {failed_items} 项"

            try:
                cleanup_expired(db)
                DailySummaryService().generate(db, target_date=run_timestamp.date().isoformat())
            except Exception as exc:  # noqa: BLE001
                failed_items += 1
                run.failed_items = failed_items
                run.status = "partial_success"
                run.message = f"新增 {new_items} 条，失败 {failed_items} 项；收尾失败：{first_chars(str(exc), 80)}"
                self._record_error(
                    db,
                    None,
                    run.crawl_run_id,
                    "crawl_finalize_failed",
                    str(exc),
                    None,
                    traceback.format_exc(),
                )

            add_long_log(db, "crawl_finished", run.message)
            add_session_log(db, "crawl_finished", run.message)
            crawl_progress.finish(run.status, run.message)
            db.commit()
            return run
        except Exception as exc:  # noqa: BLE001
            message = f"抓取任务异常：{first_chars(str(exc), 120)}"
            db.rollback()
            run = db.get(CrawlRun, run_id)
            if run:
                run.status = "failed"
                run.finished_at = db_now()
                run.message = message
                self._record_error(db, None, run_id, "crawl_run_failed", str(exc), None, traceback.format_exc())
                add_session_log(db, "crawl_failed", message, level="error")
                db.commit()
            crawl_progress.finish("failed", message)
            if run:
                return run
            raise

    def run_source(
        self,
        db: Session,
        source: Source,
        parent_run_id: int | None = None,
        run_timestamp: datetime | None = None,
    ) -> CrawlRun:
        run_timestamp = run_timestamp or db_now()
        run = CrawlRun(source_id=source.source_id, status="running", started_at=run_timestamp)
        db.add(run)
        db.flush()
        add_session_log(db, "source_crawl_start", f"开始抓取 {source.source_name}")
        db.commit()

        try:
            items = self.fetch_source(source, run_timestamp=run_timestamp)
            new_count = 0
            duplicate_count = non_today_count = non_ai_count = 0
            metadata_llm_ready = self.llm.content_metadata_ready(db)
            if self._is_article_source(source) and not self._source_is_intrinsically_ai(source) and not metadata_llm_ready:
                add_session_log(db, "ai_filter_fallback", f"{source.source_name} 未配置可用 LLM，AI 筛选使用关键词兜底", "error")
            for item in items:
                if self._content_exists(db, source, item):
                    duplicate_count += 1
                    continue
                if self._is_article_source(source) and not self._is_item_in_source_window(source, item, run_timestamp):
                    non_today_count += 1
                    continue
                metadata_attempted_for_filter = (
                    metadata_llm_ready and self._is_article_source(source) and not self._source_is_intrinsically_ai(source)
                )
                keep_item, llm_data, ai_related_override = self._should_keep_item(
                    db,
                    source,
                    item,
                    run_timestamp,
                    metadata_llm_ready=metadata_llm_ready,
                )
                if not keep_item:
                    non_ai_count += 1
                    continue
                save_llm_data = llm_data
                skip_save_llm = False
                llm_status_override = None
                extraction_status_override = None
                if metadata_llm_ready:
                    if save_llm_data is None and not metadata_attempted_for_filter:
                        save_llm_data = self._metadata_for_extracted_item(db, source, item, run_timestamp)
                    skip_save_llm = True
                    if save_llm_data is None:
                        llm_status_override = "failed"
                        extraction_status_override = "partial"
                if self._save_item(
                    db,
                    source,
                    item,
                    run_timestamp=run_timestamp,
                    precomputed_llm_data=save_llm_data,
                    ai_related_override=ai_related_override,
                    skip_llm=skip_save_llm,
                    llm_status_override=llm_status_override,
                    extraction_status_override=extraction_status_override,
                ):
                    new_count += 1
                    db.commit()
            source.last_success_at = db_now()
            source.error_count = 0
            run.status = "success"
            run.total_items = len(items)
            run.new_items = new_count
            run.finished_at = db_now()
            details = []
            if duplicate_count:
                details.append(f"重复 {duplicate_count}")
            if non_today_count:
                details.append(f"非今日 {non_today_count}")
            if non_ai_count:
                details.append(f"非 AI {non_ai_count}")
            suffix = f"（候选 {len(items)} 条，跳过：" + "，".join(details) + "）" if details else f"（候选 {len(items)} 条）"
            run.message = f"{source.source_name} 抓取完成，新增 {new_count} 条{suffix}"
            add_session_log(db, "source_crawl_success", run.message)
            db.commit()
            return run
        except Exception as exc:  # noqa: BLE001
            source.error_count += 1
            run.status = "failed"
            run.failed_items = 1
            run.finished_at = db_now()
            run.message = str(exc)
            self._record_error(db, source, run.crawl_run_id, "crawl_failed", str(exc), source.source_url, traceback.format_exc())
            add_session_log(db, "source_crawl_failed", f"{source.source_name}: {exc}", "error")
            db.commit()
            return run

    def fetch_source(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        source_name = source.source_name.lower()
        source_host = urlparse(source.source_url).netloc.lower()
        if source.source_category == "hacker_news":
            return self.fetch_hacker_news(source)
        if source.source_category == "github":
            return self.fetch_github_trending(source)
        if source.source_category == "product_hunt":
            return self.fetch_product_hunt(source)
        if "36氪" in source.source_name or "36kr" in source_host:
            return self.fetch_36kr(source, run_timestamp=run_timestamp)
        if "机器之心" in source.source_name or "jiqizhixin" in source_host:
            return self.fetch_jiqizhixin(source, run_timestamp=run_timestamp)
        if "量子位" in source.source_name or "qbitai" in source_host:
            return self.fetch_qbitai(source, run_timestamp=run_timestamp)
        if "latepost" in source_name or "latepost" in source_host or "晚点" in source.source_name:
            return self.fetch_latepost(source, run_timestamp=run_timestamp)
        if "pedaily" in source_host or "投资界" in source.source_name:
            return self.fetch_pedaily(source, run_timestamp=run_timestamp)
        if "cyzone.cn" in source_host or "创业邦" in source.source_name:
            return self.fetch_cyzone(source, run_timestamp=run_timestamp)
        if "lieyun" in source_host or "猎云" in source.source_name:
            return self.fetch_lieyun(source, run_timestamp=run_timestamp)
        if "chinaventure" in source_host or "投中网" in source.source_name:
            return self.fetch_chinaventure(source, run_timestamp=run_timestamp)
        if "cls.cn" in source_host or "财联社" in source.source_name:
            return self.fetch_cls(source, run_timestamp=run_timestamp)
        if "tmtpost" in source_host or "钛媒体" in source.source_name:
            return self.fetch_tmtpost(source, run_timestamp=run_timestamp)
        if "equalocean" in source_host:
            return self.fetch_equalocean(source, run_timestamp=run_timestamp)
        if "iyiou" in source_host or "亿欧" in source.source_name:
            return self.fetch_iyiou(source, run_timestamp=run_timestamp)
        if "pharmcube" in source_host or "bydrug" in source_host or "医药魔方" in source.source_name:
            return self.fetch_bydrug(source, run_timestamp=run_timestamp)
        if "svtr.ai" in source_host or "svtr" in source.source_name.lower():
            return self.fetch_svtr(source, run_timestamp=run_timestamp)
        if "jazzyear.com" in source_host or "甲子光年" in source.source_name:
            return self.fetch_jazzyear(source, run_timestamp=run_timestamp)
        if "a16z.com" in source_host or source_name == "a16z ai":
            return self.fetch_a16z_ai(source, run_timestamp=run_timestamp)
        if "techcrunch.com" in source_host:
            return self.fetch_techcrunch_ai(source, run_timestamp=run_timestamp)
        if "theverge.com" in source_host:
            return self.fetch_the_verge_ai(source, run_timestamp=run_timestamp)
        if "venturebeat.com" in source_host:
            return self.fetch_venturebeat_ai(source, run_timestamp=run_timestamp)
        if "the-decoder.com" in source_host:
            return self.fetch_the_decoder(source, run_timestamp=run_timestamp)
        if "crunchbase.com" in source_host:
            return self.fetch_crunchbase_ai(source, run_timestamp=run_timestamp)
        if "huggingface.co" in source_host and "/papers" in urlparse(source.source_url).path:
            return self.fetch_huggingface_papers(source, run_timestamp=run_timestamp)
        if "huggingface.co" in source_host and "/spaces" in urlparse(source.source_url).path:
            return self.fetch_huggingface_spaces(source, run_timestamp=run_timestamp)
        if "deepmind.google" in source_host:
            return self.fetch_deepmind_blog(source, run_timestamp=run_timestamp)
        if "ai.meta.com" in source_host:
            return self.fetch_meta_ai_blog(source, run_timestamp=run_timestamp)
        if "ycombinator.com" in source_host:
            return self.fetch_yc_ai_companies(source, run_timestamp=run_timestamp)
        if "openai.com" in source_host:
            return self.fetch_openai(source, run_timestamp=run_timestamp)
        if "anthropic.com" in source_host:
            return self.fetch_anthropic(source, run_timestamp=run_timestamp)
        return self.fetch_generic_site(source, run_timestamp=run_timestamp)

    def fetch_hacker_news(self, source: Source) -> list[ExtractedItem]:
        top = requests.get(source.source_url, headers=REQUEST_HEADERS, timeout=source.timeout_seconds).json()
        items: list[ExtractedItem] = []
        for story_id in top[:20]:
            data = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers=REQUEST_HEADERS,
                timeout=source.timeout_seconds,
            ).json()
            if not data or data.get("type") != "story":
                continue
            title = data.get("title") or f"Hacker News story {story_id}"
            url = data.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            published = datetime.fromtimestamp(data.get("time", 0), TZ).replace(tzinfo=None) if data.get("time") else None
            summary = clean_text(data.get("text") or title)
            items.append(
                ExtractedItem(
                    title=title,
                    url=url,
                    summary=summary_chars(summary),
                    clean_text=summary,
                    publish_time=published,
                    publish_time_status="exact" if published else "missing",
                    language="en",
                    metadata={"score": data.get("score"), "by": data.get("by")},
                )
            )
        return items

    def fetch_github_trending(self, source: Source) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        items: list[ExtractedItem] = []
        limit = max(source.item_limit_per_run or 0, 10)
        for article in soup.select("article.Box-row")[:limit]:
            anchor = article.select_one("h2 a")
            if not anchor:
                continue
            repo = clean_text(anchor.get_text(" ")).replace(" / ", "/").replace(" ", "")
            url = canonicalize_url(anchor.get("href", ""), "https://github.com")
            desc = clean_text(article.select_one("p").get_text(" ")) if article.select_one("p") else ""
            lang = clean_text(article.select_one("[itemprop='programmingLanguage']").get_text(" ")) if article.select_one("[itemprop='programmingLanguage']") else ""
            summary = desc or repo
            items.append(
                ExtractedItem(
                    title=repo,
                    url=url,
                    summary=summary_chars(summary),
                    clean_text=f"{repo}. {desc}. Language: {lang}",
                    publish_time=db_now(),
                    publish_time_status="estimated",
                    language="en",
                    metadata={"language": lang},
                )
            )
        return items

    def fetch_product_hunt(self, source: Source) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        candidates: dict[str, ExtractedItem] = {}
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if "/products/" not in href:
                continue
            title = clean_text(anchor.get_text(" "))
            title = re.sub(r"^\d+\.\s*", "", title).strip()
            if len(title) < 3 or len(title) > 120 or title.lower() in {"artificial intelligence"}:
                continue
            url = canonicalize_url(href, "https://www.producthunt.com")
            summary = self._product_hunt_summary(anchor, title)
            candidates[url] = ExtractedItem(
                title=title,
                url=url,
                summary=summary_chars(summary or title),
                clean_text=summary or title,
                publish_time=db_now(),
                publish_time_status="estimated",
                language="en",
            )
            if len(candidates) >= source.item_limit_per_run:
                break
        if not candidates:
            raise RuntimeError("Product Hunt 页面未解析到今日榜单项，可能需要 Playwright Worker 兜底")
        return list(candidates.values())

    def fetch_36kr(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        entry_url = source.source_url
        if urlparse(entry_url).path in {"", "/"}:
            entry_url = "https://36kr.com/motif/327686782977"
        html = self._get_html(entry_url, source.timeout_seconds)
        if "sec_sdk_build" in html and "captcha" in html.lower():
            raise RuntimeError("36氪返回验证码页，未获取到栏目内容")
        if urlparse(entry_url).path.startswith("/information/"):
            soup = BeautifulSoup(html, "html.parser")
            links: dict[str, str] = {}
            for card in soup.select(".information-flow-list .information-flow-item"):
                anchor = card.select_one("a.article-item-title[href*='/p/']")
                if not anchor:
                    continue
                title = clean_text(anchor.get_text(" "))
                href = canonicalize_url(anchor.get("href", ""), entry_url)
                if title and re.search(r"36kr\.com/p/\d+", href):
                    links[href] = title
                if len(links) >= source.list_page_limit:
                    break
            return self._fetch_details_from_links(
                source,
                [(title, url) for url, title in links.items()],
                run_timestamp=run_timestamp,
                filter_target_day=True,
            )
        links = self._collect_links(
            html,
            entry_url,
            lambda href, text: "/p/" in href and len(text) >= 8,
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_jiqizhixin(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        if "机器之心·数据服务" in html and "/articles/" not in html:
            raise RuntimeError("机器之心当前公开入口返回数据服务页，未暴露可抓取文章列表")
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: "/articles/" in href and len(text) >= 8,
            source.list_page_limit,
        )
        if not links:
            return self.fetch_generic_site(source, run_timestamp=run_timestamp)
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_qbitai(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: bool(re.search(r"qbitai\.com/\d{4}/\d{2}/\d+\.html", href)) and len(text) >= 8,
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_jazzyear(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                urlparse(href).path.endswith("/article_info.html")
                and bool(re.search(r"(?:^|&)id=\d+(?:&|$)", urlparse(href).query))
                and len(text) >= 8
            ),
            source.list_page_limit,
        )
        links.sort(key=lambda item: self._date_from_text(item[0])[0] or datetime.min, reverse=True)
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_a16z_ai(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main")
        if not main:
            raise RuntimeError("a16z AI 页面未找到正文区域")

        links: dict[str, str] = {}
        for card in main.select("[data-feed-item]"):
            anchor = card.select_one("h4 a[href]")
            if not anchor:
                continue
            text = clean_text(anchor.get_text(" "))
            href = canonicalize_url(anchor.get("href", ""), source.source_url)
            if not text or "a16z.com" not in urlparse(href).netloc.lower():
                continue
            links[href] = text
            if len(links) >= source.list_page_limit:
                break
        return self._fetch_details_from_links(
            source,
            [(title, url) for url, title in links.items()],
            run_timestamp=run_timestamp,
            filter_target_day=True,
        )

    def fetch_latepost(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: "/news/dj_detail" in href and len(text) >= 8,
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_pedaily(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: ("news.pedaily.cn/" in href and href.endswith(".shtml") and len(text) >= 6),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_cyzone(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        links: dict[str, str] = {}
        for card in soup.select(".article-item"):
            anchor = card.select_one("a.item-title[href*='/article/']")
            if not anchor:
                continue
            title = clean_text(anchor.get_text(" "))
            href = canonicalize_url(anchor.get("href", ""), source.source_url)
            if not title or not re.search(r"cyzone\.cn/article/\d+\.html", href):
                continue
            card_date, _ = self._date_from_text(clean_text(card.get_text(" ")))
            title_hint = f"{title} {card_date:%Y-%m-%d %H:%M}" if card_date else title
            links[href] = title_hint
            if len(links) >= source.list_page_limit:
                break
        return self._fetch_details_from_links(
            source,
            [(title, url) for url, title in links.items()],
            run_timestamp=run_timestamp,
            filter_target_day=True,
        )

    def fetch_lieyun(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: "/archives/" in href and len(text) >= 6,
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_chinaventure(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                "chinaventure.com.cn" in href
                and href.endswith(".html")
                and len(text) >= 6
                and any(marker in text for marker in ("融资", "投资", "创投", "AI", "人工智能"))
            ),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_cls(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                ("cls.cn/detail/" in href or "cls.cn/share/article/" in href)
                and len(text) >= 6
                and any(marker in text for marker in ("创投通", "融资", "投资", "AI", "人工智能"))
            ),
            source.list_page_limit,
        )
        if not links:
            telegraph_html = self._get_html("https://www.cls.cn/telegraph", source.timeout_seconds)
            links = self._collect_links(
                telegraph_html,
                "https://www.cls.cn/telegraph",
                lambda href, text: ("cls.cn/detail/" in href or "cls.cn/share/article/" in href) and len(text) >= 6 and "融资" in text,
                source.list_page_limit,
            )
        links = self._normalize_cls_links(links)
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_tmtpost(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: bool(re.search(r"tmtpost\.com/\d+\.html$", href)) and len(text) >= 6,
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_equalocean(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        finance_markers = (
            "funding",
            "raises",
            "raised",
            "investment",
            "investor",
            "venture",
            "financing",
            "融资",
            "投资",
        )
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                "equalocean.com" in href
                and len(text) >= 8
                and any(path in href for path in ("/analysis/", "/news/", "/briefing/"))
                and any(marker in text.lower() for marker in finance_markers)
            ),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_iyiou(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        if self._looks_like_browser_challenge(html):
            raise RuntimeError("亿欧中文站返回浏览器验证页，需要 Browser Worker / JS 渲染后才能抓取")
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                "iyiou.com" in href
                and len(text) >= 6
                and any(path in href for path in ("/news/", "/data/", "/briefing/"))
                and any(marker in text for marker in ("融资", "投资", "AI", "人工智能", "大模型", "机器人"))
            ),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_bydrug(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                ("bydrug.pharmcube.com" in href or "pharmcube.com" in href)
                and ("/news/detail" in href or "/news/" in href)
                and len(text) >= 8
            ),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_svtr(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        timestamp = run_timestamp or db_now()
        items: list[ExtractedItem] = []
        skip_titles = {
            "AI创投日报 LIVE",
            "AI创投日报",
            "融资轮次分布",
            "重点投资人动态",
            "AI创投周报精选",
            "AI Market Intelligence",
        }
        in_live_section = False
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            title = clean_text(heading.get_text(" "))
            if title == "AI创投日报 LIVE":
                in_live_section = True
                continue
            if not in_live_section:
                continue
            if title.startswith("Chat with") or title == "AI创投周报精选":
                break
            if (
                not title
                or title in skip_titles
                or title.startswith("📊")
                or title.startswith("💰")
                or title.startswith("👥")
                or len(title) > 80
            ):
                continue
            fragments: list[str] = []
            node = heading.next_sibling
            while node and len(fragments) < 8:
                if getattr(node, "name", None) in {"h1", "h2", "h3", "h4"}:
                    break
                text = clean_text(node.get_text(" ")) if hasattr(node, "get_text") else clean_text(str(node))
                if text:
                    fragments.append(text)
                node = node.next_sibling
            body = clean_text(" ".join(fragments))
            if not body:
                continue
            if body in {"未知", "种子轮", "天使轮", "Pre-A轮", "A轮", "B轮", "C轮", "D轮"} or re.search(
                r"(?:Pre-)?[A-D]\+?轮|种子轮|天使轮|战略融资",
                body,
            ):
                body = f"{title} 融资轮次/状态：{body}"
            elif not any(marker in body for marker in ("融资", "投资方", "估值", "筹集", "基金")):
                continue
            item_url = f"{source.source_url.rstrip('/')}?company={quote(title)}"
            items.append(
                ExtractedItem(
                    title=f"{title} 融资动态",
                    url=item_url,
                    summary=summary_chars(body),
                    clean_text=body,
                    publish_time=timestamp,
                    publish_time_status="estimated",
                    language="zh" if re.search(r"[\u4e00-\u9fff]", body + title) else "en",
                    metadata={"source_page": source.source_url},
                )
            )
            if len(items) >= source.item_limit_per_run:
                break
        return items

    def fetch_techcrunch_ai(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                "techcrunch.com" in href
                and bool(re.search(r"/20\d{2}/\d{2}/\d{2}/", href))
                and len(text) >= 8
            ),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_the_verge_ai(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                "theverge.com" in href
                and bool(re.search(r"/(?:ai-artificial-intelligence|tech|policy|news)/\d+", urlparse(href).path))
                and len(text) >= 8
            ),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_venturebeat_ai(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                "venturebeat.com" in href
                and len(text) >= 8
                and not text.lower().startswith("credit:")
                and not any(part in urlparse(href).path for part in ("/author/", "/category/", "/events/", "/tag/"))
                and urlparse(href).path.count("/") >= 2
            ),
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_the_decoder(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: (
                "the-decoder.com" in href
                and len(text) >= 8
                and urlparse(href).path.strip("/").count("/") == 0
                and not any(skip in href for skip in ("/register", "/subscription", "/about", "/author", "/artificial-intelligence-news"))
            ),
            source.list_page_limit,
        )
        cleaned = [(self._clean_decoder_title(title), url) for title, url in links]
        return self._fetch_details_from_links(source, cleaned, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_crunchbase_ai(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for anchor in soup.select(".category-post a[href]"):
            text = clean_text(anchor.get_text(" "))
            href = canonicalize_url(anchor["href"], source.source_url)
            if (
                len(text) < 8
                or "news.crunchbase.com" not in href
                or href in seen
                or any(skip in urlparse(href).path for skip in ("/sections/", "/daily/", "/company-ipo-exits-list"))
            ):
                continue
            seen.add(href)
            links.append((text, href))
            if len(links) >= source.list_page_limit:
                break
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_huggingface_papers(
        self,
        source: Source,
        run_timestamp: datetime | None = None,
    ) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        timestamp = run_timestamp or db_now()
        items: list[ExtractedItem] = []
        seen: set[str] = set()
        for article in soup.find_all("article"):
            anchor = article.find("a", href=re.compile(r"^/papers/\d"))
            title_node = article.find(["h2", "h3", "h4"])
            title = clean_text(title_node.get_text(" ")) if title_node else ""
            if not anchor or not title:
                continue
            url = canonicalize_url(anchor["href"], source.source_url)
            if url in seen:
                continue
            seen.add(url)
            details = clean_text(article.get_text(" "))
            submitter = re.search(r"Submitted by\s+([A-Za-z0-9_.-]+)", details)
            score = re.search(r"Submitted by\s+[A-Za-z0-9_.-]+\s+(\d+)", details)
            summary_parts = [title]
            if submitter:
                summary_parts.append(f"Submitted by {submitter.group(1)}")
            if score:
                summary_parts.append(f"score {score.group(1)}")
            items.append(
                ExtractedItem(
                    title=title,
                    url=url,
                    summary=summary_chars(" | ".join(summary_parts)),
                    clean_text=details or title,
                    publish_time=timestamp,
                    publish_time_status="estimated",
                    language="en",
                    metadata={"source_page": source.source_url},
                )
            )
            if len(items) >= source.item_limit_per_run:
                break
        return items

    def fetch_huggingface_spaces(
        self,
        source: Source,
        run_timestamp: datetime | None = None,
    ) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        timestamp = run_timestamp or db_now()
        items: list[ExtractedItem] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not re.match(r"^/spaces/[^/]+/[^/]+", href):
                continue
            url = canonicalize_url(href, source.source_url)
            if url in seen:
                continue
            seen.add(url)
            title_node = anchor.find(["h2", "h3", "h4"])
            title = clean_text(title_node.get_text(" ")) if title_node else ""
            title = title.rstrip("🐌🔥🏆✨🚀").strip()
            if not title:
                title = self._humanize_slug(urlparse(url).path.rstrip("/").split("/")[-1])
            desc_node = anchor.find("p")
            description = clean_text(desc_node.get_text(" ")) if desc_node else ""
            owner = urlparse(url).path.strip("/").split("/")[1]
            time_node = anchor.find("time")
            age = clean_text(time_node.get_text(" ")) if time_node else ""
            body = clean_text(" ".join(part for part in (title, description, f"Owner: {owner}", age) if part))
            items.append(
                ExtractedItem(
                    title=title,
                    url=url,
                    summary=summary_chars(description or body),
                    clean_text=body,
                    publish_time=timestamp,
                    publish_time_status="estimated",
                    language="en",
                    metadata={"owner": owner, "source_page": source.source_url, "age": age},
                )
            )
            if len(items) >= source.item_limit_per_run:
                break
        return items

    def fetch_deepmind_blog(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        root = soup.find("main") or soup
        links = self._collect_path_links(
            str(root),
            source.source_url,
            lambda path: path.startswith("/blog/") and path.strip("/") != "blog" and path.count("/") >= 2,
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_meta_ai_blog(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        root = soup.find("main") or soup
        links = self._collect_path_links(
            str(root),
            source.source_url,
            lambda path: path.startswith("/blog/") and path.strip("/") != "blog" and path.count("/") >= 2,
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_yc_ai_companies(
        self,
        source: Source,
        run_timestamp: datetime | None = None,
    ) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        soup = BeautifulSoup(html, "html.parser")
        timestamp = run_timestamp or db_now()
        items: list[ExtractedItem] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not re.match(r"^/companies/[^/]+$", href):
                continue
            url = canonicalize_url(href, source.source_url)
            if url in seen:
                continue
            seen.add(url)
            card_text = clean_text(anchor.get_text(" "))
            if not card_text:
                continue
            title = re.split(r"\s+Y Combinator Logo\b", card_text, maxsplit=1)[0].strip()
            if not title or title.lower() in {"startup directory", "companies"}:
                continue
            summary = card_text
            summary = re.sub(rf"^{re.escape(title)}\s+Y Combinator Logo\s*", "", summary).strip()
            summary = re.sub(r"\s+", " ", summary)
            items.append(
                ExtractedItem(
                    title=title,
                    url=url,
                    summary=summary_chars(summary or card_text),
                    clean_text=card_text,
                    publish_time=timestamp,
                    publish_time_status="estimated",
                    language="en",
                    metadata={"source_page": source.source_url},
                )
            )
            if len(items) >= source.item_limit_per_run:
                break
        return items

    def fetch_openai(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._collect_links(
            html,
            source.source_url,
            lambda href, text: "openai.com/index/" in href and len(text) >= 8 and self._date_from_text(text)[0],
            source.list_page_limit,
        )
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_anthropic(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        if source.source_category == "official_research":
            predicate = (
                lambda href, text: "anthropic.com/research/" in href
                and "/research/team/" not in href
                and len(text) >= 8
                and self._date_from_text(text)[0]
            )
        else:
            predicate = lambda href, text: "anthropic.com/news/" in href and len(text) >= 8 and self._date_from_text(text)[0]
        links = self._collect_links(html, source.source_url, predicate, source.list_page_limit)
        return self._fetch_details_from_links(source, links, run_timestamp=run_timestamp, filter_target_day=True)

    def fetch_generic_site(self, source: Source, run_timestamp: datetime | None = None) -> list[ExtractedItem]:
        html = self._get_html(source.source_url, source.timeout_seconds)
        links = self._extract_links(html, source.source_url, source.list_page_limit)
        if not links:
            detail = self._extract_detail(html, source.source_url)
            if detail and self._is_article_source(source) and not self._is_item_in_source_window(source, detail, run_timestamp or db_now()):
                return []
            return [detail] if detail else []

        items: list[ExtractedItem] = []
        seen: set[str] = set()
        for title_hint, url in links:
            canonical = canonicalize_url(url, source.source_url)
            if canonical in seen:
                continue
            seen.add(canonical)
            try:
                detail_html = self._get_html(canonical, source.timeout_seconds)
                item = self._extract_detail(detail_html, canonical, fallback_title=title_hint)
                if item:
                    if self._is_article_source(source) and not self._is_item_in_source_window(source, item, run_timestamp or db_now()):
                        continue
                    items.append(item)
            except Exception:
                items.append(ExtractedItem(title=title_hint, url=canonical, summary=summary_chars(title_hint)))
            if not self._is_article_source(source) and len(items) >= source.item_limit_per_run:
                break
        return items

    def _get_html(self, url: str, timeout: int) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.8 * (attempt + 1))
        if last_error:
            raise last_error
        raise RuntimeError(f"未能获取页面：{url}")

    def _collect_links(
        self,
        html: str,
        base_url: str,
        predicate,
        limit: int,
    ) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        links: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            text = clean_text(anchor.get_text(" "))
            href = canonicalize_url(anchor["href"], base_url)
            if not href.startswith("http") or not text:
                continue
            if self._is_low_value_link_text(text):
                continue
            if predicate(href, text):
                current = links.get(href, "")
                if len(text) > len(current):
                    links[href] = text
            if len(links) >= limit:
                break
        return [(title, url) for url, title in links.items()]

    def _collect_path_links(self, html: str, base_url: str, predicate, limit: int) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        links: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            href = canonicalize_url(anchor["href"], base_url)
            parsed = urlparse(href)
            if not parsed.scheme.startswith("http") or not predicate(parsed.path):
                continue
            text = clean_text(anchor.get_text(" "))
            if self._is_low_value_link_text(text) or text.lower() in {"learn more", "featured", "next"}:
                text = ""
            if not text:
                text = self._humanize_slug(parsed.path.rstrip("/").split("/")[-1])
            current = links.get(href, "")
            if len(text) > len(current):
                links[href] = text
            if len(links) >= limit:
                break
        return [(title, url) for url, title in links.items()]

    def _normalize_cls_links(self, links: list[tuple[str, str]]) -> list[tuple[str, str]]:
        normalized_links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for title, url in links:
            article_id = ""
            match = re.search(r"/(?:share/article|detail)/(\d+)", url)
            if match:
                article_id = match.group(1)
            normalized_url = f"https://www.cls.cn/detail/{article_id}" if article_id else url
            if normalized_url in seen:
                continue
            seen.add(normalized_url)
            normalized_links.append((title, normalized_url))
        return normalized_links

    def _looks_like_browser_challenge(self, html: str) -> bool:
        challenge_markers = (
            "/probe.js",
            "Please Enable JavaScript and Cookie",
            "Just a moment",
            "HuaweiCloudWAF",
        )
        return any(marker in html for marker in challenge_markers)

    def _humanize_slug(self, slug: str) -> str:
        text = re.sub(r"[-_]+", " ", slug or "")
        return clean_text(text).title()

    def _clean_decoder_title(self, title: str) -> str:
        title = clean_text(title)
        title = re.sub(r"^Read full article about:\s*", "", title, flags=re.I)
        return title

    def _fetch_details_from_links(
        self,
        source: Source,
        links: list[tuple[str, str]],
        run_timestamp: datetime | None = None,
        filter_target_day: bool = False,
    ) -> list[ExtractedItem]:
        if not links:
            return []
        items: list[ExtractedItem] = []
        target_timestamp = run_timestamp or db_now()
        older_streak = 0
        link_limit = len(links) if filter_target_day else min(len(links), source.item_limit_per_run)
        for title_hint, url in links[:link_limit]:
            try:
                detail_html = self._get_html(url, source.timeout_seconds)
                item = self._extract_detail(detail_html, url, fallback_title=title_hint)
                if item:
                    clean_hint = self._clean_card_title(title_hint)
                    if clean_hint and (len(item.title) > 120 or item.title == title_hint):
                        item.title = clean_hint
                    list_date, list_date_status = self._date_from_text(title_hint)
                    if list_date and (
                        "openai.com" in url
                        or "anthropic.com" in url
                        or "jazzyear.com" in url
                        or "cyzone.cn" in url
                        or not item.publish_time
                    ):
                        item.publish_time = list_date
                        item.publish_time_status = list_date_status
                    elif not item.publish_time:
                        estimated = self._date_from_url(url)
                        if estimated:
                            item.publish_time = estimated
                            item.publish_time_status = "estimated"
                    if filter_target_day:
                        if self._is_item_before_target_day(item, target_timestamp):
                            if self._is_item_in_source_window(source, item, target_timestamp):
                                older_streak = 0
                                items.append(item)
                                continue
                            older_streak += 1
                            if older_streak >= OLDER_ARTICLE_STOP_STREAK:
                                break
                            continue
                        if not self._is_item_in_source_window(source, item, target_timestamp):
                            continue
                        older_streak = 0
                    items.append(item)
            except Exception:
                list_date, list_date_status = self._date_from_text(title_hint)
                fallback_date = list_date or self._date_from_url(url)
                fallback_title = self._clean_card_title(title_hint) or title_hint
                fallback_item = ExtractedItem(
                    title=first_chars(fallback_title, 180),
                    url=url,
                    summary=summary_chars(title_hint),
                    publish_time=fallback_date,
                    publish_time_status=list_date_status if list_date else ("estimated" if fallback_date else "missing"),
                )
                if filter_target_day:
                    if self._is_item_before_target_day(fallback_item, target_timestamp):
                        if self._is_item_in_source_window(source, fallback_item, target_timestamp):
                            older_streak = 0
                            items.append(fallback_item)
                            continue
                        older_streak += 1
                        if older_streak >= OLDER_ARTICLE_STOP_STREAK:
                            break
                        continue
                    if not self._is_item_in_source_window(source, fallback_item, target_timestamp):
                        continue
                    older_streak = 0
                items.append(fallback_item)
        return items

    def _extract_links(self, html: str, base_url: str, limit: int) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        base_host = urlparse(base_url).netloc.replace("www.", "")
        results: list[tuple[str, str]] = []
        for anchor in soup.find_all("a", href=True):
            text = clean_text(anchor.get_text(" "))
            if len(text) < 8 or len(text) > 120:
                continue
            if self._is_low_value_link_text(text):
                continue
            href = canonicalize_url(anchor["href"], base_url)
            parsed = urlparse(href)
            if not parsed.scheme.startswith("http"):
                continue
            if base_host and base_host not in parsed.netloc.replace("www.", ""):
                continue
            if any(skip in href.lower() for skip in ("login", "signup", "about", "contact", "privacy")):
                continue
            results.append((text, href))
            if len(results) >= limit:
                break
        return results

    def _extract_detail(self, html: str, url: str, fallback_title: str = "") -> ExtractedItem | None:
        soup = BeautifulSoup(html, "html.parser")
        title = self._meta(soup, "property", "og:title") or self._meta(soup, "name", "twitter:title")
        title = title or clean_text(soup.find("h1").get_text(" ")) if soup.find("h1") else title
        title = title or clean_text(soup.title.get_text(" ")) if soup.title else title
        title = title or fallback_title
        if not title:
            return None
        if "jazzyear.com" in urlparse(url).netloc.lower() and soup.title:
            title = clean_text(soup.title.get_text(" "))
        title = self._clean_title(title)

        description = self._meta(soup, "name", "description") or self._meta(soup, "property", "og:description") or ""
        time_node = soup.find("time")
        date_published_node = soup.find(attrs={"itemprop": "datePublished"})
        published_raw = (
            self._meta(soup, "property", "article:published_time")
            or self._meta(soup, "name", "pubdate")
            or self._meta(soup, "name", "publishdate")
            or self._meta(soup, "name", "publish_time")
            or self._meta(soup, "name", "date")
            or self._meta(soup, "itemprop", "datePublished")
            or self._node_datetime(date_published_node)
            or self._node_datetime(time_node)
        )
        parsed_published = parse_datetime(published_raw)
        jsonld_published = self._date_from_json_ld(soup)
        text_published, text_published_status = self._date_from_text(soup.get_text(" ", strip=True)[:2500])
        url_published = self._date_from_url(url)
        publish_time = parsed_published or jsonld_published or text_published or url_published
        if parsed_published or jsonld_published:
            publish_time_status = "exact"
        elif text_published:
            publish_time_status = text_published_status
        elif url_published:
            publish_time_status = "estimated"
        else:
            publish_time_status = "missing"

        kr_published = self._date_from_36kr_html(html, url)
        if kr_published:
            publish_time = kr_published
            publish_time_status = "exact"

        content_root = soup.find("article") or soup.find("main") or soup.body
        paragraphs = [clean_text(p.get_text(" ")) for p in content_root.find_all("p")] if content_root else []
        body = clean_text(" ".join(p for p in paragraphs if len(p) > 20))
        summary = summary_chars(description or body or title)
        language = "zh" if re.search(r"[\u4e00-\u9fff]", body + title) else "en"
        return ExtractedItem(
            title=first_chars(title, 180),
            url=url,
            summary=summary,
            clean_text=body,
            publish_time=publish_time,
            publish_time_status=publish_time_status,
            language=language,
        )

    def _meta(self, soup: BeautifulSoup, attr: str, value: str) -> str:
        node = soup.find("meta", attrs={attr: value})
        return clean_text(node.get("content")) if node and node.get("content") else ""

    def _node_datetime(self, node) -> str:
        if not node:
            return ""
        for attr in ("datetime", "content", "data-time", "data-date"):
            if node.get(attr):
                return clean_text(node.get(attr))
        return clean_text(node.get_text(" "))

    def _date_from_36kr_html(self, html: str, url: str) -> datetime | None:
        parsed_url = urlparse(url)
        if parsed_url.netloc.lower() not in {"36kr.com", "www.36kr.com"}:
            return None
        article_match = re.search(r"/p/(\d+)", parsed_url.path)
        if not article_match:
            return None

        article_id = article_match.group(1)
        for match in re.finditer(re.escape(article_id), html):
            nearby = html[match.start() : match.start() + 2500]
            timestamp_match = re.search(r'"publishTime"\s*:\s*(\d{13})', nearby)
            if not timestamp_match:
                continue
            try:
                return datetime.fromtimestamp(int(timestamp_match.group(1)) / 1000, TZ).replace(tzinfo=None)
            except (OverflowError, OSError, ValueError):
                continue
        return None

    def _date_from_json_ld(self, soup: BeautifulSoup) -> datetime | None:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            stack = data if isinstance(data, list) else [data]
            while stack:
                node = stack.pop()
                if isinstance(node, list):
                    stack.extend(node)
                    continue
                if not isinstance(node, dict):
                    continue
                for key in ("datePublished", "dateCreated", "dateModified", "uploadDate"):
                    parsed = parse_datetime(node.get(key))
                    if parsed:
                        return parsed
                stack.extend(value for value in node.values() if isinstance(value, (dict, list)))
        return None

    def _is_low_value_link_text(self, text: str) -> bool:
        normalized = text.strip().lower()
        low_values = {
            "首页",
            "快讯",
            "全部",
            "热点",
            "登录",
            "注册",
            "关于我们",
            "加入我们",
            "广告联系",
            "research",
            "company",
            "product",
            "safety",
            "engineering",
            "security",
            "global affairs",
            "skip to main content",
        }
        return normalized in low_values or normalized.startswith("share") or normalized.startswith("skip to")

    def _date_from_url(self, url: str) -> datetime | None:
        match = re.search(r"(?:/|-)(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|\.|-|$)", url)
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
        match = re.search(r"(?:/|-)(20\d{2})(\d{2})(\d{2})(?:/|\.|-|$)", url)
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
        return None

    def _date_from_text(self, text: str) -> tuple[datetime | None, str]:
        normalized = clean_text(text)
        now = now_bj().replace(tzinfo=None)

        today_time = re.search(r"(?:今天|今日)\s*(\d{1,2}):(\d{2})", normalized)
        if today_time:
            try:
                return (
                    datetime(
                        now.year,
                        now.month,
                        now.day,
                        int(today_time.group(1)),
                        int(today_time.group(2)),
                    ),
                    "estimated",
                )
            except ValueError:
                pass

        yesterday_time = re.search(r"昨天\s*(\d{1,2}):(\d{2})", normalized)
        if yesterday_time:
            try:
                yesterday = now - timedelta(days=1)
                return (
                    datetime(
                        yesterday.year,
                        yesterday.month,
                        yesterday.day,
                        int(yesterday_time.group(1)),
                        int(yesterday_time.group(2)),
                    ),
                    "estimated",
                )
            except ValueError:
                pass

        relative_time = re.search(r"(\d{1,3})\s*(分钟前|小时前|天前)", normalized)
        if relative_time:
            amount = int(relative_time.group(1))
            unit = relative_time.group(2)
            if unit == "分钟前":
                return now - timedelta(minutes=amount), "estimated"
            if unit == "小时前":
                return now - timedelta(hours=amount), "estimated"
            if unit == "天前":
                return now - timedelta(days=amount), "estimated"

        numeric = re.search(
            r"(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?:\s+(\d{1,2}):(\d{2}))?",
            normalized,
        )
        if numeric:
            try:
                return (
                    datetime(
                        int(numeric.group(1)),
                        int(numeric.group(2)),
                        int(numeric.group(3)),
                        int(numeric.group(4) or 0),
                        int(numeric.group(5) or 0),
                    ),
                    "exact",
                )
            except ValueError:
                pass

        short_numeric = re.search(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", normalized)
        if short_numeric:
            try:
                parsed = datetime(
                    now.year,
                    int(short_numeric.group(1)),
                    int(short_numeric.group(2)),
                    int(short_numeric.group(3) or 0),
                    int(short_numeric.group(4) or 0),
                )
                if parsed.date() > (now + timedelta(days=1)).date():
                    parsed = parsed.replace(year=parsed.year - 1)
                return parsed, "estimated"
            except ValueError:
                pass

        chinese = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2}))?", normalized)
        if chinese:
            try:
                return (
                    datetime(
                        now.year,
                        int(chinese.group(1)),
                        int(chinese.group(2)),
                        int(chinese.group(3) or 0),
                        int(chinese.group(4) or 0),
                    ),
                    "estimated",
                )
            except ValueError:
                pass

        chinese_full = re.search(r"(?<!\d)(20\d{2})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2}))?", normalized)
        if chinese_full:
            try:
                return (
                    datetime(
                        int(chinese_full.group(1)),
                        int(chinese_full.group(2)),
                        int(chinese_full.group(3)),
                        int(chinese_full.group(4) or 0),
                        int(chinese_full.group(5) or 0),
                    ),
                    "exact",
                )
            except ValueError:
                pass

        month_names = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "sept": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }
        english = re.search(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),\s+(20\d{2})\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if english:
            month = month_names[english.group(1).lower()]
            try:
                return datetime(int(english.group(3)), month, int(english.group(2))), "exact"
            except ValueError:
                pass
        return None, "missing"

    def _clean_title(self, title: str) -> str:
        title = clean_text(title)
        title = re.sub(r"\s*[-|—_]\s*36氪\s*$", "", title)
        title = re.sub(r"\s*[-|—_]\s*钛媒体官方网站\s*$", "", title)
        title = re.sub(r"\s*医药新闻\s*[-|—_]\s*ByDrug.*$", "", title)
        title = re.sub(r"\s*\|\s*Anthropic\s*$", "", title)
        title = re.sub(r"\s*\|\s*OpenAI\s*$", "", title)
        title = re.sub(r"\s*\|\s*TechCrunch\s*$", "", title)
        title = re.sub(r"\s*[-|—_]\s*The Verge\s*$", "", title)
        title = re.sub(r"\s*[-|—_]\s*VentureBeat\s*$", "", title)
        title = re.sub(r"\s*[-|—_]\s*THE DECODER\s*$", "", title, flags=re.I)
        title = re.sub(r"\s*[-|—_]\s*Crunchbase News\s*$", "", title, flags=re.I)
        title = re.sub(r"\s*[-|—_]\s*Google DeepMind\s*$", "", title)
        title = re.sub(r"\s*\|\s*Andreessen Horowitz\s*$", "", title)
        return title

    def _clean_card_title(self, text: str) -> str:
        text = self._clean_title(text)
        categories = (
            "Company|Research|Product|Safety|Engineering|Security|Global Affairs|AI Adoption|"
            "Announcements|Alignment|Interpretability|Policy|Science|Economic Research|Societal Impacts"
        )
        month = (
            "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
            "Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        )

        title_before_date = re.match(rf"(.+?)\s+(?:{categories})\s+(?:{month})\s+\d{{1,2}},\s+20\d{{2}}\b", text, re.I)
        if title_before_date:
            return clean_text(title_before_date.group(1))

        title_after_date = re.match(
            rf"^(?:{categories})\s+(?:{month})\s+\d{{1,2}},\s+20\d{{2}}\s+(.+)$",
            text,
            re.I,
        )
        if title_after_date:
            candidate = clean_text(title_after_date.group(1))
            candidate = re.split(
                r"\s+(?:Today|We|Our|New|A new|Introducing our|Learn|Read)\b",
                candidate,
                maxsplit=1,
            )[0]
            return clean_text(candidate)

        date_first = re.match(rf"^(?:{month})\s+\d{{1,2}},\s+20\d{{2}}\s+(?:{categories})\s+(.+)$", text, re.I)
        if date_first:
            return clean_text(date_first.group(1))

        return text if len(text) <= 140 else first_chars(text, 120)

    def _product_hunt_summary(self, anchor, title: str) -> str:
        section = anchor
        for _ in range(3):
            section = section.parent
            if not section:
                break
            if section.name == "section":
                break
        text = clean_text(section.get_text(" ")) if section else clean_text(anchor.get_text(" "))
        text = re.sub(rf"^\d+\.\s*{re.escape(title)}", "", text).strip()
        text = re.sub(r"\s+\d+\s+\d+$", "", text).strip()
        return text

    def _is_article_source(self, source: Source) -> bool:
        return source.source_category in ARTICLE_SOURCE_CATEGORIES

    def _source_is_intrinsically_ai(self, source: Source) -> bool:
        host = urlparse(source.source_url).netloc.lower()
        return source.source_category in INTRINSIC_AI_SOURCE_CATEGORIES or any(
            marker in host for marker in ("openai.com", "anthropic.com", "qbitai.com", "a16z.com")
        )

    def _target_date(self, run_timestamp: datetime | None) -> Any:
        timestamp = run_timestamp or db_now()
        if timestamp.tzinfo:
            timestamp = timestamp.astimezone(TZ).replace(tzinfo=None)
        return timestamp.date()

    def _is_item_on_target_day(self, item: ExtractedItem, run_timestamp: datetime | None) -> bool:
        if not item.publish_time:
            return False
        return item.publish_time.date() == self._target_date(run_timestamp)

    def _is_item_before_target_day(self, item: ExtractedItem, run_timestamp: datetime | None) -> bool:
        if not item.publish_time:
            return False
        return item.publish_time.date() < self._target_date(run_timestamp)

    def _source_allows_previous_day(self, source: Source) -> bool:
        host = urlparse(source.source_url).netloc.lower()
        return any(marker in host for marker in OVERSEAS_DATE_LENIENT_HOST_MARKERS)

    def _is_item_in_source_window(self, source: Source, item: ExtractedItem, run_timestamp: datetime | None) -> bool:
        if self._is_item_on_target_day(item, run_timestamp):
            return True
        if not item.publish_time or not self._source_allows_previous_day(source):
            return False
        return item.publish_time.date() == self._target_date(run_timestamp) - timedelta(days=1)

    def _content_exists(self, db: Session, source: Source, item: ExtractedItem) -> bool:
        canonical = canonicalize_url(item.url, source.source_url)
        return bool(db.scalar(select(ContentItem.content_id).where(ContentItem.canonical_url == canonical)))

    def _keyword_ai_related(self, item: ExtractedItem) -> bool:
        text = clean_text(f"{item.title} {item.summary} {first_chars(item.clean_text or '', 1200)}").lower()
        if re.search(r"(?<![a-z])ai(?![a-z])", text):
            return True
        return any(keyword in text for keyword in AI_RELATED_KEYWORDS)

    def _metadata_for_extracted_item(
        self,
        db: Session,
        source: Source,
        item: ExtractedItem,
        run_timestamp: datetime | None,
    ) -> dict[str, Any] | None:
        body = item.clean_text or item.summary or item.title
        content = ContentItem(
            source_id=source.source_id,
            title=item.title,
            url=item.url,
            canonical_url=canonicalize_url(item.url, source.source_url),
            source_name=source.source_name,
            source_category=source.source_category,
            publish_time=item.publish_time,
            crawl_time=run_timestamp or db_now(),
            publish_time_status=item.publish_time_status,
            summary=item.summary or summary_chars(body),
            language=item.language,
            word_count=len(body),
            content_fingerprint=fingerprint(body) if body else None,
            extraction_status="new",
            llm_status="pending",
        )
        return self.llm.process_content_metadata(db, content, item.clean_text)

    def _should_keep_item(
        self,
        db: Session,
        source: Source,
        item: ExtractedItem,
        run_timestamp: datetime | None,
        metadata_llm_ready: bool | None = None,
    ) -> tuple[bool, dict[str, Any] | None, bool | None]:
        if not self._is_article_source(source):
            return True, None, None
        if self._source_is_intrinsically_ai(source):
            return True, None, True

        llm_data: dict[str, Any] | None = None
        llm_ready = self.llm.content_metadata_ready(db) if metadata_llm_ready is None else metadata_llm_ready
        if llm_ready:
            llm_data = self._metadata_for_extracted_item(db, source, item, run_timestamp)
            if llm_data and llm_data.get("ai_related") is True:
                return True, llm_data, None
            if llm_data and llm_data.get("ai_related") is False:
                return False, llm_data, None

        if self._keyword_ai_related(item):
            return True, llm_data, True
        return False, llm_data, None

    def _save_item(
        self,
        db: Session,
        source: Source,
        item: ExtractedItem,
        run_timestamp: datetime | None = None,
        precomputed_llm_data: dict[str, Any] | None = None,
        ai_related_override: bool | None = None,
        skip_llm: bool = False,
        llm_status_override: str | None = None,
        extraction_status_override: str | None = None,
    ) -> bool:
        canonical = canonicalize_url(item.url, source.source_url)
        if db.scalar(select(ContentItem).where(ContentItem.canonical_url == canonical)):
            return False

        run_timestamp = run_timestamp or db_now()
        body = item.clean_text or item.summary or item.title
        publish_time = item.publish_time
        content = ContentItem(
            source_id=source.source_id,
            title=item.title,
            url=item.url,
            canonical_url=canonical,
            source_name=source.source_name,
            source_category=source.source_category,
            publish_time=publish_time,
            crawl_time=run_timestamp,
            publish_time_status=item.publish_time_status,
            summary=item.summary or summary_chars(body),
            language=item.language,
            word_count=len(body),
            content_fingerprint=fingerprint(body) if body else None,
            extraction_status="new",
            llm_status="pending",
            full_content_cached=bool(item.clean_text),
            content_cache_until=db_now() + timedelta(hours=DEFAULT_CACHE_HOURS) if item.clean_text else None,
        )
        db.add(content)
        db.flush()

        if item.clean_text:
            db.add(
                ContentCache(
                    content_id=content.content_id,
                    clean_text=item.clean_text,
                    cached_at=db_now(),
                    expire_at=db_now() + timedelta(hours=int(get_setting(db, "content_cache_hours", "48"))),
                )
            )

        attach_tag(db, content, "source_category", source.source_category, "rule")
        attach_tag(db, content, "priority", source.priority, "rule")
        if source.source_category == "github":
            attach_tag(db, content, "content_type", "repo", "rule")
        elif source.source_category == "product_hunt":
            attach_tag(db, content, "content_type", "product_launch", "rule")
        elif source.source_category == "hacker_news":
            attach_tag(db, content, "content_type", "discussion", "rule")
        elif source.source_category == "ai_research_signal":
            attach_tag(db, content, "content_type", "research", "rule")
        elif source.source_category == "ai_product_signal":
            attach_tag(db, content, "content_type", "product_launch", "rule")
        elif source.source_category == "startup_directory":
            attach_tag(db, content, "content_type", "company_news", "rule")

        llm_data = precomputed_llm_data
        if llm_data is None and not skip_llm:
            llm_data = self.llm.process_content_metadata(db, content, item.clean_text)
        if llm_data:
            self._apply_llm_data(db, content, llm_data)
            if precomputed_llm_data and content.llm_status == "pending":
                content.llm_status = "success"
                content.extraction_status = "processed"
        elif skip_llm:
            if llm_status_override:
                content.llm_status = llm_status_override
            if extraction_status_override:
                content.extraction_status = extraction_status_override
        if ai_related_override is not None:
            content.ai_related = ai_related_override
        elif not content.summary:
            content.summary = summary_chars(body)

        if self._is_article_source(source) and self.llm.ai_financing_relevance_ready(db):
            financing_data = self.llm.classify_ai_financing_relevance(db, content, item.clean_text or body, llm_data)
            if financing_data:
                self._apply_llm_data(db, content, financing_data)

        if content.extraction_status == "new":
            content.extraction_status = "processed" if content.summary else "partial"
        db.flush()
        return True

    def mark_stale_not_configured_as_pending(self, db: Session) -> int:
        if not self.llm.content_metadata_ready(db):
            return 0
        items = db.scalars(select(ContentItem).where(ContentItem.llm_status == "not_configured")).all()
        for content in items:
            content.llm_status = "pending"
        if items:
            message = f"LLM 已配置，已将 {len(items)} 条旧 not_configured 内容改为 pending"
            add_session_log(db, "llm_status_reset", message)
            add_long_log(db, "llm_status_reset", message)
            db.commit()
        return len(items)

    def reprocess_not_configured_content(self, db: Session, limit: int = 20) -> dict[str, int]:
        limit = max(1, min(limit, 100))
        items = db.scalars(
            select(ContentItem)
            .where(ContentItem.llm_status.in_(["not_configured", "pending"]))
            .order_by(ContentItem.crawl_time.desc(), ContentItem.content_id.desc())
            .limit(limit)
        ).all()
        processed = failed = skipped = 0
        for content in items:
            body = content.cache.clean_text if content.cache else content.summary or content.title
            llm_data = self.llm.process_content_metadata(db, content, body)
            if llm_data:
                self._apply_llm_data(db, content, llm_data)
                if content.source_category in ARTICLE_SOURCE_CATEGORIES and self.llm.ai_financing_relevance_ready(db):
                    financing_data = self.llm.classify_ai_financing_relevance(db, content, body, llm_data)
                    if financing_data:
                        self._apply_llm_data(db, content, financing_data)
                processed += 1
            elif content.llm_status == "failed":
                failed += 1
            else:
                skipped += 1
            if content.extraction_status == "new":
                content.extraction_status = "processed" if content.summary else "partial"
            db.flush()
        message = f"LLM 补处理完成：成功 {processed} 条，失败 {failed} 条，跳过 {skipped} 条"
        add_session_log(db, "llm_reprocess_finished", message, "info" if failed == 0 else "error")
        add_long_log(db, "llm_reprocess_finished", message, "info" if failed == 0 else "error")
        db.commit()
        return {"total": len(items), "processed": processed, "failed": failed, "skipped": skipped}

    def _apply_llm_data(self, db: Session, content: ContentItem, data: dict[str, Any]) -> None:
        summary = clean_text(data.get("summary"))
        if summary:
            content.summary = summary_chars(summary)
        if "ai_related" in data:
            value = data.get("ai_related")
            content.ai_related = value if isinstance(value, bool) else None
        content_type = clean_text(data.get("content_type"))
        if content_type:
            attach_tag(db, content, "content_type", content_type, "llm", 0.8)
        financing_relevance = clean_text(data.get("ai_financing_relevance")).lower()
        if financing_relevance in {"high", "none"}:
            db.execute(
                delete(ContentTag).where(
                    ContentTag.content_id == content.content_id,
                    ContentTag.tag_key == "ai_financing_relevance",
                )
            )
            attach_tag(db, content, "ai_financing_relevance", financing_relevance, "llm", 0.9)
        for sector in data.get("sector_tags") or data.get("sector") or []:
            attach_tag(db, content, "sector", clean_text(sector), "llm", 0.8)
        for region in data.get("region_tags") or data.get("region") or []:
            attach_tag(db, content, "region", clean_text(region), "llm", 0.8)
        for entity in data.get("entities") or []:
            if isinstance(entity, dict):
                attach_entity(db, content, clean_text(entity.get("type")), clean_text(entity.get("name")), "llm", 0.8)

    def _record_error(
        self,
        db: Session,
        source: Source | None,
        crawl_run_id: int | None,
        error_type: str,
        message: str,
        url: str | None,
        stack_trace: str | None = None,
    ) -> None:
        db.add(
            CrawlError(
                source_id=source.source_id if source else None,
                crawl_run_id=crawl_run_id,
                error_type=error_type,
                error_message=message,
                url=url,
                stack_trace=stack_trace,
            )
        )


class FinancingService:
    REPORT_LLM_ITEM_LIMIT = 200
    MANUAL_STATUS_TAG_KEY = "manual_financing_status"
    MANUAL_EXCLUDED_TAG_VALUE = "excluded"

    def __init__(self) -> None:
        self.llm = LLMService()

    def content_filter(self) -> Any:
        financing_tagged = select(ContentTag.content_id).where(
            ContentTag.tag_key == "content_type",
            ContentTag.tag_value == "financing",
        )
        high_relevance_tagged = select(ContentTag.content_id).where(
            ContentTag.tag_key == "ai_financing_relevance",
            ContentTag.tag_value == "high",
        )
        manual_excluded = select(ContentTag.content_id).where(
            ContentTag.tag_key == self.MANUAL_STATUS_TAG_KEY,
            ContentTag.tag_value == self.MANUAL_EXCLUDED_TAG_VALUE,
        )
        return and_(
            ContentItem.ai_related.is_(True),
            ContentItem.content_id.in_(financing_tagged),
            ContentItem.content_id.in_(high_relevance_tagged),
            ~ContentItem.content_id.in_(manual_excluded),
        )

    def query_items(
        self,
        db: Session,
        limit: int = 100,
        target_date: date | None = None,
    ) -> tuple[list[dict[str, Any]], str, str]:
        today_date = db_now().date()
        window_date = target_date or today_date
        day_start = datetime.combine(window_date, dt_time.min)
        day_end = day_start + timedelta(days=1)
        raw_items = self._raw_financing_items(db, day_start, day_end, max(limit * 4, 200))
        window_label = "今日融资信息" if window_date == today_date else f"{window_date.isoformat()} 融资信息"
        if not raw_items and target_date is None:
            window_label = "最新融资信息"
            raw_items = self._raw_financing_items(db, None, None, max(limit * 4, 200))
        return self.dedupe_financing_items(raw_items, limit), window_label, window_date.isoformat()

    def counts(self, db: Session, target_date: date | None = None) -> dict[str, int]:
        today_date = db_now().date()
        window_date = target_date or today_date
        window_start = datetime.combine(window_date, dt_time.min)
        window_end = window_start + timedelta(days=1)
        day_start = datetime.combine(today_date, dt_time.min)
        day_end = day_start + timedelta(days=1)
        financing_filter = self.content_filter()
        selected_count = (
            db.scalar(
                select(func.count(ContentItem.content_id)).where(
                    ContentItem.crawl_time >= window_start,
                    ContentItem.crawl_time < window_end,
                    financing_filter,
                )
            )
            or 0
        )
        today_count = (
            db.scalar(
                select(func.count(ContentItem.content_id)).where(
                    ContentItem.crawl_time >= day_start,
                    ContentItem.crawl_time < day_end,
                    financing_filter,
                )
            )
            or 0
        )
        total_count = db.scalar(select(func.count(ContentItem.content_id)).where(financing_filter)) or 0
        return {"selected_count": selected_count, "today_count": today_count, "total_count": total_count}

    def available_dates(self, db: Session, limit: int = 90) -> list[str]:
        crawl_date = func.date(ContentItem.crawl_time)
        rows = db.scalars(
            select(crawl_date)
            .where(self.content_filter())
            .group_by(crawl_date)
            .order_by(crawl_date.desc())
            .limit(max(1, min(limit, 365)))
        ).all()
        return [str(row) for row in rows if row]

    def exclude_content_ids(self, db: Session, content_ids: list[int]) -> dict[str, Any]:
        unique_ids = sorted({int(content_id) for content_id in content_ids if int(content_id) > 0})
        if not unique_ids:
            return {"excluded": 0, "content_ids": []}

        contents = db.scalars(select(ContentItem).where(ContentItem.content_id.in_(unique_ids))).all()
        for content in contents:
            attach_tag(
                db,
                content,
                self.MANUAL_STATUS_TAG_KEY,
                self.MANUAL_EXCLUDED_TAG_VALUE,
                "manual",
                1.0,
            )
        excluded_ids = [content.content_id for content in contents]
        if excluded_ids:
            add_session_log(db, "manual_financing_excluded", f"手动排除融资新闻：{', '.join(map(str, excluded_ids))}")
            add_long_log(db, "manual_financing_excluded", f"手动排除融资新闻：{', '.join(map(str, excluded_ids))}")
        db.commit()
        return {"excluded": len(excluded_ids), "content_ids": excluded_ids}

    def report_location(self, db: Session) -> str:
        return get_setting(db, FINANCING_REPORT_SETTING_KEY, str(DB_PATH.parent / "reports"))

    def identify_this_week(self, db: Session, limit: int = 300) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        start, end = week_range()
        metadata_ready = self.llm.content_metadata_ready(db)
        financing_ready = self.llm.ai_financing_relevance_ready(db)
        items = db.scalars(
            select(ContentItem)
            .where(ContentItem.crawl_time >= start, ContentItem.crawl_time < end)
            .order_by(ContentItem.crawl_time.desc(), ContentItem.content_id.desc())
            .limit(limit)
        ).all()

        crawl_service = CrawlService()
        stats = {
            "total": len(items),
            "metadata_processed": 0,
            "candidates": 0,
            "classified": 0,
            "high": 0,
            "none": 0,
            "failed": 0,
            "skipped": 0,
        }
        for content in items:
            if content.source_category not in ARTICLE_SOURCE_CATEGORIES:
                stats["skipped"] += 1
                continue

            body = self._content_body(content)
            llm_data: dict[str, Any] | None = None
            if metadata_ready and content.llm_status in {"not_configured", "pending", "failed"}:
                llm_data = self.llm.process_content_metadata(db, content, body)
                if llm_data:
                    crawl_service._apply_llm_data(db, content, llm_data)
                    stats["metadata_processed"] += 1
                elif content.llm_status == "failed":
                    stats["failed"] += 1

            if not self._is_financing_candidate(content, llm_data):
                stats["skipped"] += 1
                db.flush()
                continue

            stats["candidates"] += 1
            if not financing_ready:
                stats["skipped"] += 1
                db.flush()
                continue

            financing_data = self.llm.classify_ai_financing_relevance(
                db,
                content,
                body,
                llm_data or self._metadata_snapshot(content),
            )
            if not financing_data:
                stats["failed"] += 1
                db.flush()
                continue

            crawl_service._apply_llm_data(db, content, financing_data)
            stats["classified"] += 1
            relevance = clean_text(financing_data.get("ai_financing_relevance")).lower()
            if relevance == "high":
                stats["high"] += 1
            elif relevance == "none":
                stats["none"] += 1
            db.flush()

        add_session_log(
            db,
            "weekly_financing_identified",
            f"本周融资识别完成：候选 {stats['candidates']} 条，高相关 {stats['high']} 条",
            "info" if stats["failed"] == 0 else "error",
        )
        add_long_log(
            db,
            "weekly_financing_identified",
            f"{start.date().isoformat()} 至 {(end - timedelta(days=1)).date().isoformat()}："
            f"处理 {stats['total']} 条，分类 {stats['classified']} 条，高相关 {stats['high']} 条，失败 {stats['failed']} 条",
            "info" if stats["failed"] == 0 else "error",
        )
        db.commit()

        raw_financing = self._raw_financing_items(db, start, end, 2000)
        event_count = len(self._dedupe_clusters(raw_financing, 2000))
        return {
            "ok": True,
            "week_start": start.date().isoformat(),
            "week_end": (end - timedelta(days=1)).date().isoformat(),
            "metadata_ready": metadata_ready,
            "financing_llm_ready": financing_ready,
            "event_count": event_count,
            **stats,
        }

    def generate_previous_week_report(self, db: Session, output_location: str | None = None) -> dict[str, Any]:
        start, end = week_range(previous=True)
        return self._generate_week_report(
            db, start, end, "上周", "generate_previous_week_financing_report", output_location
        )

    def generate_current_week_report(self, db: Session, output_location: str | None = None) -> dict[str, Any]:
        current = db_now()
        start, _ = week_range(current)
        end = datetime.combine(current.date() + timedelta(days=1), dt_time.min)
        return self._generate_week_report(
            db, start, end, "本周", "generate_current_week_financing_report", output_location
        )

    def _generate_week_report(
        self,
        db: Session,
        start: datetime,
        end: datetime,
        period_label: str,
        task_name: str,
        output_location: str | None = None,
    ) -> dict[str, Any]:
        raw_items = self._raw_financing_items(db, start, end, 2000)
        clusters = self._dedupe_clusters(raw_items, 2000)
        payload = self._weekly_report_llm_payload(start, end, clusters, len(raw_items), period_label)
        markdown = self.llm.generate_financing_report(db, task_name, payload)

        target = self._resolve_report_path(output_location or self.report_location(db), start, end)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")

        persistence_warning = self._record_week_report_result(
            db,
            output_location,
            period_label,
            start,
            end,
            len(clusters),
            target,
        )
        return {
            "ok": True,
            "path": str(target),
            "week_start": start.date().isoformat(),
            "week_end": (end - timedelta(days=1)).date().isoformat(),
            "raw_count": len(raw_items),
            "event_count": len(clusters),
            "period_label": period_label,
            "llm_generated": True,
            "llm_error": None,
            "persistence_warning": persistence_warning,
            "markdown": markdown,
        }

    def _record_week_report_result(
        self,
        db: Session,
        output_location: str | None,
        period_label: str,
        start: datetime,
        end: datetime,
        event_count: int,
        target: Path,
    ) -> str | None:
        try:
            if output_location:
                set_setting(db, FINANCING_REPORT_SETTING_KEY, output_location.strip().strip('"'))
            add_session_log(db, "weekly_financing_report_generated", f"{period_label} financing report generated: {target}")
            add_long_log(
                db,
                "weekly_financing_report_generated",
                f"{start.date().isoformat()} to {(end - timedelta(days=1)).date().isoformat()}: "
                f"{event_count} events, report {target}",
            )
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            return first_chars(str(exc), 500)
        return None

    def _weekly_report_llm_payload(
        self,
        start: datetime,
        end: datetime,
        clusters: list[dict[str, Any]],
        raw_count: int,
        period_label: str,
    ) -> dict[str, Any]:
        selected_clusters = clusters[: self.REPORT_LLM_ITEM_LIMIT]
        news_items = []
        for cluster in selected_clusters:
            items = self._sorted_cluster_items(cluster)
            primary = items[0]
            region_tags = [tag.tag_value for tag in primary.tags if tag.tag_key == "region"]
            entities = [
                {
                    "type": relation.entity.entity_type,
                    "name": relation.entity.display_name or relation.entity.canonical_name,
                }
                for relation in primary.entities
                if relation.entity
            ]
            related_reports = [
                {
                    "content_id": item.content_id,
                    "title": item.title,
                    "url": item.url,
                    "source_name": item.source_name,
                    "publish_time": item.publish_time.isoformat() if item.publish_time else None,
                    "crawl_time": item.crawl_time.isoformat(),
                }
                for item in items
            ]
            news_items.append(
                {
                    "content_id": primary.content_id,
                    "title": primary.title,
                    "url": primary.url,
                    "source_name": primary.source_name,
                    "publish_time": primary.publish_time.isoformat() if primary.publish_time else None,
                    "crawl_time": primary.crawl_time.isoformat(),
                    "summary": primary.summary or "",
                    "region_tags": region_tags,
                    "entities": entities,
                    "related_count": max(0, len(items) - 1),
                    "related_reports": related_reports,
                    "text": first_chars(primary.cache.clean_text, 1000) if primary.cache else primary.summary or primary.title,
                }
            )
        return {
            "period": {
                "label": period_label,
                "start_date": start.date().isoformat(),
                "end_date": (end - timedelta(days=1)).date().isoformat(),
            },
            "raw_news_count": raw_count,
            "event_count": len(clusters),
            "input_truncated": len(clusters) > len(selected_clusters),
            "input_notes": [
                "news_items are deduplicated financing events.",
                "related_reports contains every source report title and url for the event; list all related_reports urls without rewriting them.",
            ],
            "news_items": news_items,
        }

    def render_weekly_report(
        self,
        start: datetime,
        end: datetime,
        clusters: list[dict[str, Any]],
        raw_count: int,
        period_label: str = "上周",
    ) -> str:
        start_label = start.date().isoformat()
        end_label = (end - timedelta(days=1)).date().isoformat()
        lines = [
            f"# 融资新闻周报（{start_label} 至 {end_label}）",
            "",
            "## 统计汇总",
            "",
            f"- 融资事件数（去重）：{len(clusters)}",
            f"- 原始融资新闻数：{raw_count}",
            f"- 统计口径：基于 content_type=financing、ai_related=true、ai_financing_relevance=high 的新闻，并按公司、轮次、金额和标题相似度去重。",
            "",
        ]
        if not clusters:
            lines.extend(["## 事件明细", "", f"{period_label}暂无已识别的融资事件。", ""])
            return "\n".join(lines).strip() + "\n"

        lines.extend(["## 事件明细", ""])
        for index, cluster in enumerate(clusters, start=1):
            detail = self._cluster_report_detail(cluster)
            lines.extend(
                [
                    f"### {index}. {detail['company']}",
                    "",
                    f"- 公司：{detail['company']}",
                    f"- 融资轮次：{detail['round']}",
                    f"- 融资金额：{detail['amount']}",
                    f"- 项目简介：{detail['project_intro']}",
                    f"- 投资方简介：{detail['investor_intro']}",
                    f"- 新闻来源：{detail['source_names']}",
                ]
            )
            if detail["reports"]:
                report_links = "；".join(f"[{item.title}]({item.url})" for item in detail["reports"])
                lines.append(f"- 相关报道：{report_links}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def dedupe_financing_items(self, raw_items: list[ContentItem], limit: int) -> list[dict[str, Any]]:
        grouped: list[dict[str, Any]] = []
        for cluster in self._dedupe_clusters(raw_items, limit):
            items = self._sorted_cluster_items(cluster)
            primary = content_preview_item(items[0])
            related = [content_preview_item(item) for item in items[1:]]
            primary["related_reports"] = related
            primary["related_count"] = len(related)
            primary["source_names"] = "、".join(dict.fromkeys(item.source_name for item in items))
            grouped.append(primary)
        return grouped

    def _raw_financing_items(
        self,
        db: Session,
        start: datetime | None,
        end: datetime | None,
        raw_limit: int,
    ) -> list[ContentItem]:
        conditions = [self.content_filter()]
        if start is not None:
            conditions.append(ContentItem.crawl_time >= start)
        if end is not None:
            conditions.append(ContentItem.crawl_time < end)
        return db.scalars(
            select(ContentItem)
            .where(*conditions)
            .order_by(func.coalesce(ContentItem.publish_time, ContentItem.crawl_time).desc(), ContentItem.content_id.desc())
            .limit(raw_limit)
        ).all()

    def _dedupe_clusters(self, raw_items: list[ContentItem], limit: int) -> list[dict[str, Any]]:
        clusters: list[dict[str, Any]] = []
        for item in raw_items:
            signature = build_financing_signature(item)
            best_cluster: dict[str, Any] | None = None
            best_score = 0.0
            for cluster in clusters:
                score = financing_duplicate_score(signature, cluster["signature"])
                if score > best_score:
                    best_score = score
                    best_cluster = cluster

            if best_cluster and best_score >= 0.72:
                best_cluster["items"].append(item)
                best_cluster["signatures"].append(signature)
                if len((signature["companies"] | best_cluster["signature"]["companies"])) > len(best_cluster["signature"]["companies"]):
                    best_cluster["signature"]["companies"] |= signature["companies"]
                best_cluster["signature"]["amounts"] |= signature["amounts"]
                best_cluster["signature"]["rounds"] |= signature["rounds"]
                best_cluster["signature"]["tokens"] |= signature["tokens"]
                continue

            clusters.append({"items": [item], "signature": signature, "signatures": [signature]})
        return clusters[:limit]

    def _sorted_cluster_items(self, cluster: dict[str, Any]) -> list[ContentItem]:
        return sorted(
            cluster["items"],
            key=lambda value: (value.publish_time or value.crawl_time, value.content_id),
            reverse=True,
        )

    def _cluster_report_detail(self, cluster: dict[str, Any]) -> dict[str, Any]:
        items = self._sorted_cluster_items(cluster)
        primary = items[0]
        text = "\n".join(self._report_source_text(item) for item in items)
        company_names = self._entity_names(items, {"company"}) or extract_company_display_candidates(text)
        investor_names = self._entity_names(items, {"investor"}) or extract_investor_texts(text)
        rounds = extract_financing_round_texts(text)
        amounts = extract_financing_amount_texts(text)
        project_intro = summary_chars(primary.summary or text, 220)
        investor_intro = self._investor_intro(text, investor_names)
        return {
            "company": "、".join(company_names[:3]) if company_names else primary.title,
            "round": "、".join(rounds) if rounds else "未披露",
            "amount": "、".join(amounts) if amounts else "未披露",
            "project_intro": project_intro or "未披露",
            "investor_intro": investor_intro,
            "source_names": "、".join(dict.fromkeys(item.source_name for item in items)),
            "reports": items,
        }

    def _report_source_text(self, item: ContentItem) -> str:
        body = first_chars(item.cache.clean_text, 1600) if item.cache else ""
        return f"{item.title}\n{item.summary or ''}\n{body}"

    def _investor_intro(self, text: str, investor_names: list[str]) -> str:
        sentences = [
            clean_text(sentence)
            for sentence in re.split(r"[。！？!?；;]\s*", text)
            if clean_text(sentence)
        ]
        evidence = [
            sentence
            for sentence in sentences
            if any(marker in sentence for marker in ("领投", "跟投", "参投", "投资方", "投资机构"))
            or any(name and name in sentence for name in investor_names)
        ]
        if evidence:
            return summary_chars("；".join(dict.fromkeys(evidence[:2])), 220)
        if investor_names:
            return f"公开报道提到投资方：{'、'.join(investor_names)}；暂无更详细机构简介。"
        return "未披露或原文未提供明确投资方信息。"

    def _entity_names(self, items: list[ContentItem], entity_types: set[str]) -> list[str]:
        names: list[str] = []
        for item in items:
            for rel in item.entities:
                entity = rel.entity
                if entity and entity.entity_type in entity_types:
                    name = clean_text(entity.display_name or entity.canonical_name)
                    if name:
                        names.append(name)
        return list(dict.fromkeys(names))

    def _content_body(self, content: ContentItem) -> str:
        return content.cache.clean_text if content.cache else content.summary or content.title

    def _metadata_snapshot(self, content: ContentItem) -> dict[str, Any]:
        return {
            "ai_related": content.ai_related,
            "llm_status": content.llm_status,
            "tags": [{"key": tag.tag_key, "value": tag.tag_value} for tag in content.tags],
            "entities": [
                {"type": rel.entity.entity_type, "name": rel.entity.display_name or rel.entity.canonical_name}
                for rel in content.entities
                if rel.entity
            ],
        }

    def _is_financing_candidate(self, content: ContentItem, llm_data: dict[str, Any] | None = None) -> bool:
        if llm_data and clean_text(llm_data.get("content_type")) == "financing":
            return True
        for tag in content.tags:
            if tag.tag_key == "content_type" and tag.tag_value == "financing":
                return True
            if tag.tag_key == "ai_financing_relevance" and tag.tag_value == "high":
                return True
        text = f"{content.title} {content.summary or ''}".lower()
        return any(keyword.lower() in text for keyword in FINANCING_KEYWORDS)

    def _resolve_report_path(self, output_location: str, start: datetime, end: datetime) -> Path:
        raw = (output_location or "").strip().strip('"')
        if not raw:
            raw = str(DB_PATH.parent / "reports")
        path = Path(raw).expanduser()
        if path.suffix.lower() == ".md":
            return path
        start_label = start.date().isoformat()
        end_label = (end - timedelta(days=1)).date().isoformat()
        return path / f"融资新闻周报_{start_label}_{end_label}.md"


class WeeklyCrawlService:
    COMPLETED_RUN_STATUSES = {"success", "partial_success"}

    def target_dates(self, target: datetime | None = None) -> list[str]:
        current_date = (target or db_now()).date()
        week_start = current_date - timedelta(days=current_date.weekday())
        days = (current_date - week_start).days + 1
        return [(week_start + timedelta(days=offset)).isoformat() for offset in range(days)]

    def status(self, db: Session, target: datetime | None = None) -> dict[str, Any]:
        dates = self.target_dates(target)
        if not dates:
            return {"week_start": "", "today": "", "days": [], "missing_dates": []}

        range_start = datetime.fromisoformat(dates[0])
        range_end = datetime.fromisoformat(dates[-1]) + timedelta(days=1)
        runs = db.scalars(
            select(CrawlRun)
            .where(
                CrawlRun.source_id.is_(None),
                CrawlRun.started_at >= range_start,
                CrawlRun.started_at < range_end,
            )
            .order_by(CrawlRun.started_at.desc(), CrawlRun.crawl_run_id.desc())
        ).all()

        latest_run_by_date: dict[str, CrawlRun] = {}
        completed_run_dates: set[str] = set()
        for run in runs:
            run_date = run.started_at.date().isoformat()
            latest_run_by_date.setdefault(run_date, run)
            if run.status in self.COMPLETED_RUN_STATUSES:
                completed_run_dates.add(run_date)

        summary_dates = set(
            db.scalars(select(DailySummary.summary_date).where(DailySummary.summary_date.in_(dates))).all()
        )

        days: list[dict[str, Any]] = []
        missing_dates: list[str] = []
        for date_str in dates:
            day_start = datetime.fromisoformat(date_str)
            day_end = day_start + timedelta(days=1)
            content_count = (
                db.scalar(
                    select(func.count(ContentItem.content_id)).where(
                        ContentItem.crawl_time >= day_start,
                        ContentItem.crawl_time < day_end,
                    )
                )
                or 0
            )
            run = latest_run_by_date.get(date_str)
            crawled = date_str in completed_run_dates
            if not crawled:
                missing_dates.append(date_str)
            days.append(
                {
                    "date": date_str,
                    "crawled": crawled,
                    "has_summary": date_str in summary_dates,
                    "content_count": content_count,
                    "run_status": run.status if run else "",
                    "run_started_at": run.started_at.isoformat(sep=" ", timespec="seconds") if run else "",
                    "run_finished_at": run.finished_at.isoformat(sep=" ", timespec="seconds") if run and run.finished_at else "",
                    "new_items": run.new_items if run else 0,
                    "failed_items": run.failed_items if run else 0,
                    "message": run.message or "" if run else "",
                }
            )

        return {
            "week_start": dates[0],
            "today": dates[-1],
            "days": days,
            "missing_dates": missing_dates,
        }


class DailySummaryService:
    SECTION_NAMES = {
        "venture_media": "中文投融资 / 创投信息",
        "ai_media": "中文 AI 垂直媒体",
        "tech_business_media": "中文科技商业媒体",
        "official_news": "海外官方新闻 / 研究更新",
        "official_research": "海外官方新闻 / 研究更新",
        "github": "GitHub Trending Daily",
        "product_hunt": "Product Hunt 今日榜单",
        "hacker_news": "Hacker News Top 20",
    }

    def generate(self, db: Session, target_date: str | None = None) -> DailySummary:
        date_str = target_date or now_bj().date().isoformat()
        day_start = datetime.fromisoformat(date_str)
        day_end = day_start + timedelta(days=1)
        items = db.scalars(
            select(ContentItem)
            .where(and_(ContentItem.crawl_time >= day_start, ContentItem.crawl_time < day_end))
            .order_by(ContentItem.source_category, ContentItem.crawl_time.desc())
        ).all()

        sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
        source_counts: dict[str, int] = defaultdict(int)
        for item in items:
            section = self.SECTION_NAMES.get(item.source_category, "其他内容")
            source_counts[item.source_name] += 1
            sections[section].append(
                {
                    "content_id": item.content_id,
                    "title": item.title,
                    "source": item.source_name,
                    "time": (item.publish_time or item.crawl_time).isoformat() if (item.publish_time or item.crawl_time) else "",
                    "summary": item.summary or "",
                    "url": item.url,
                    "tags": [{"key": tag.tag_key, "value": tag.tag_value} for tag in item.tags],
                    "entities": [
                        {"type": rel.entity.entity_type, "name": rel.entity.display_name or rel.entity.canonical_name}
                        for rel in item.entities
                    ],
                }
            )

        failed_count = len([item for item in items if item.extraction_status == "failed"])
        partial_count = len([item for item in items if item.extraction_status == "partial"])
        success_count = len(items) - failed_count - partial_count
        markdown = self.render_markdown(date_str, sections, source_counts, len(items), success_count, partial_count, failed_count)

        summary = db.scalar(select(DailySummary).where(DailySummary.summary_date == date_str))
        if not summary:
            summary = DailySummary(summary_date=date_str)
            db.add(summary)
        summary.generated_at = db_now()
        summary.total_items = len(items)
        summary.successful_items = success_count
        summary.partial_items = partial_count
        summary.failed_items = failed_count
        summary.source_counts_json = json_dumps(dict(source_counts))
        summary.sections_json = json_dumps(dict(sections))
        summary.markdown_text = markdown
        summary.llm_summary_status = "disabled"

        if get_setting(db, "daily_summary_use_llm", "false").lower() == "true":
            summary.llm_summary_status = "failed"
            summary.llm_summary_text = "LLM 每日汇总开关已启用，但当前版本仅保留任务入口；结构化汇总不受影响。"

        add_long_log(db, "daily_summary_generated", f"{date_str} 汇总已生成，共 {len(items)} 条")
        db.commit()
        return summary

    def render_markdown(
        self,
        date_str: str,
        sections: dict[str, list[dict[str, Any]]],
        source_counts: dict[str, int],
        total: int,
        success: int,
        partial: int,
        failed: int,
    ) -> str:
        lines = [f"# AI 信息收集汇总｜{date_str}", ""]
        lines.extend(
            [
                "## 一、今日抓取概览",
                "",
                f"- 总条数：{total}",
                f"- 成功处理：{success}",
                f"- 部分处理：{partial}",
                f"- 失败：{failed}",
                "",
            ]
        )
        if source_counts:
            lines.append("### 来源分布")
            for source, count in sorted(source_counts.items()):
                lines.append(f"- {source}: {count}")
            lines.append("")

        order = [
            "中文投融资 / 创投信息",
            "中文 AI 垂直媒体",
            "中文科技商业媒体",
            "海外官方新闻 / 研究更新",
            "GitHub Trending Daily",
            "Product Hunt 今日榜单",
            "Hacker News Top 20",
            "其他内容",
        ]
        for section in order:
            items = sections.get(section) or []
            if not items:
                continue
            lines.extend([f"## {section}", ""])
            for item in items:
                lines.append(f"### [{item['title']}]({item['url']})")
                lines.append(f"- 来源：{item['source']}")
                if item["summary"]:
                    lines.append(f"- 摘要：{item['summary']}")
                tag_text = ", ".join(f"{tag['key']}:{tag['value']}" for tag in item.get("tags", []))
                if tag_text:
                    lines.append(f"- 标签：{tag_text}")
                entity_text = ", ".join(f"{entity['type']}:{entity['name']}" for entity in item.get("entities", []))
                if entity_text:
                    lines.append(f"- 实体：{entity_text}")
                lines.append("")
        return "\n".join(lines).strip() + "\n"


class BackupService:
    def create_backup(self, db: Session, backup_type: str = "manual") -> BackupRecord:
        timestamp = now_bj().strftime("%Y%m%d_%H%M%S")
        target = BACKUP_DIR / f"ai_news_agent_{backup_type}_{timestamp}.sqlite3"
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(DB_PATH, target)

        integrity = self._integrity_check(target)
        record = BackupRecord(
            backup_type=backup_type,
            backup_path=str(target),
            status="success" if integrity == "ok" else "failed",
            integrity_status=integrity,
            message="备份完成" if integrity == "ok" else "备份完成但完整性检查失败",
        )
        db.add(record)
        add_long_log(db, "backup_created", f"备份 {target.name}: {integrity}", "info" if integrity == "ok" else "error")
        db.commit()
        return record

    def _integrity_check(self, path: Path) -> str:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            return result[0] if result else "unknown"
        finally:
            conn.close()


def cleanup_expired(db: Session) -> None:
    now = db_now()
    db.query(ContentCache).filter(ContentCache.expire_at < now).delete()
    cutoff_llm = now - timedelta(days=90)
    cutoff_crawl = now - timedelta(days=180)
    db.query(LLMLog).filter(LLMLog.created_at < cutoff_llm).delete()
    db.query(CrawlError).filter(CrawlError.occurred_at < cutoff_crawl).delete()
    add_session_log(db, "cleanup", "已清理过期缓存和日志")


def archive_content(db: Session, content: ContentItem) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    body = content.cache.clean_text if content.cache else ""
    text = body or f"{content.title}\n\n{content.summary or ''}\n\n{content.url}"
    filename = f"content_{content.content_id}_{now_bj().strftime('%Y%m%d_%H%M%S')}.md"
    path = ARCHIVE_DIR / filename
    path.write_text(text, encoding="utf-8")
    content.full_content_saved = True
    content.archive_object_path = str(path)
    content.extraction_status = "archived"
    add_long_log(db, "content_archived", f"已归档内容 {content.content_id}: {content.title}")


class AppScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone=TZ)
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self._reschedule()
        self.scheduler.start()
        self.started = True

    def _reschedule(self) -> None:
        with SessionLocal() as db:
            crawl_time = get_setting(db, "daily_crawl_time", "10:00")
        hour, minute = self._parse_time(crawl_time)
        self.scheduler.remove_all_jobs()
        self.scheduler.add_job(
            self.run_scheduled_crawl,
            CronTrigger(hour=hour, minute=minute, timezone=TZ),
            id="daily_crawl",
            replace_existing=True,
        )

    def refresh(self) -> None:
        self._reschedule()

    def run_scheduled_crawl(self) -> None:
        with SessionLocal() as db:
            today = now_bj().date().isoformat()
            set_setting(db, "last_auto_crawl_date", today)
            db.commit()
            CrawlService().run_all_sources(db)

    def run_startup_catchup_if_needed(self) -> None:
        with SessionLocal() as db:
            crawl_time = get_setting(db, "daily_crawl_time", "10:00")
            last_date = get_setting(db, "last_auto_crawl_date", "")
            hour, minute = self._parse_time(crawl_time)
            threshold = dt_time(hour=hour, minute=minute)
            now = now_bj()
            if now.time() >= threshold and last_date != now.date().isoformat():
                add_session_log(db, "startup_catchup", "已过今日抓取时间，启动后自动补抓")
                set_setting(db, "last_auto_crawl_date", now.date().isoformat())
                db.commit()
                CrawlService().run_all_sources(db)
            else:
                add_session_log(db, "startup", "启动完成，无需补抓")
                db.commit()

    def _parse_time(self, value: str) -> tuple[int, int]:
        try:
            hour, minute = value.split(":", 1)
            return int(hour), int(minute)
        except Exception:
            return 10, 0


app_scheduler = AppScheduler()


def query_content(
    db: Session,
    q: str = "",
    source_id: int | None = None,
    status: str = "",
    favorite: bool = False,
) -> list[ContentItem]:
    stmt = select(ContentItem)
    filters = []
    if q:
        like = f"%{q}%"
        matching_entities = select(ContentEntity.content_id).join(Entity).where(Entity.canonical_name.like(like))
        matching_tags = select(ContentTag.content_id).where(or_(ContentTag.tag_key.like(like), ContentTag.tag_value.like(like)))
        filters.append(
            or_(
                ContentItem.title.like(like),
                ContentItem.summary.like(like),
                ContentItem.source_name.like(like),
                ContentItem.content_id.in_(matching_entities),
                ContentItem.content_id.in_(matching_tags),
            )
        )
    if source_id:
        filters.append(ContentItem.source_id == source_id)
    if status:
        filters.append(ContentItem.extraction_status == status)
    if favorite:
        filters.append(ContentItem.is_favorite.is_(True))
    if filters:
        stmt = stmt.where(and_(*filters))
    return db.scalars(stmt.order_by(ContentItem.crawl_time.desc()).limit(300)).all()
