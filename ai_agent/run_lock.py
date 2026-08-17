"""Small, process-safe shared lock for daily runs and GUI write guards.

The lock file is intentionally a tiny, non-sensitive JSON document.  It is
used by the headless runner to prevent duplicate pipelines and by the API
process to reject writes while a pipeline is active.  The module keeps the
filesystem and process-liveness details behind one narrow interface so those
two callers cannot drift apart.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_MAX_RUNTIME_SECONDS = 6 * 60 * 60
LOCK_FILENAME = ".daily-run.lock"

# Lock content is diagnostic only.  Keep the same redaction boundary used by
# the orchestration layer in case a caller accidentally supplies a sensitive
# owner identifier or a malformed lock file contains a secret-looking value.
_SENSITIVE_RE = re.compile(
    r"(?is)(?:api[_ -]?key|authorization|password|secret|access[_ -]?token)\s*[:=]\s*[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_RE = re.compile(r"(?i)\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}\b")


def _safe_text(value: Any, *, limit: int = 128) -> str:
    text = str(value).replace("\x00", " ").replace("\r", " ").replace("\n", " ").strip()
    text = _SENSITIVE_RE.sub("<redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _KEY_RE.sub("<redacted>", text)
    return text[:limit]


def _max_runtime(value: int | float | str | None = None) -> int:
    if value is None:
        value = os.environ.get("VC_NEWS_MAX_RUNTIME_SECONDS", DEFAULT_MAX_RUNTIME_SECONDS)
    return max(60, int(value))


def _pid_alive(pid: int) -> bool:
    """Return whether *pid* can still be observed by this process."""

    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but this process is not allowed to inspect it.
        return True
    except OSError:
        return False
    return True


def _read_payload(path: Path) -> tuple[dict[str, Any] | None, bool]:
    """Read a lock payload and distinguish a missing file from bad JSON."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, True
    return (payload if isinstance(payload, dict) else None), True


def _is_stale(path: Path, payload: dict[str, Any] | None, max_runtime_seconds: int) -> bool:
    """Apply the conservative PID/age policy used by acquisition."""

    if not payload:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        return age > max_runtime_seconds

    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0 and not _pid_alive(pid):
        return True

    started_at = payload.get("started_at")
    if isinstance(started_at, str):
        try:
            parsed = datetime.fromisoformat(started_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TIMEZONE)
            age = (datetime.now(TIMEZONE) - parsed.astimezone(TIMEZONE)).total_seconds()
            return age > max_runtime_seconds
        except ValueError:
            pass
    return False


def _safe_owner(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Expose only the non-sensitive, bounded lock-owner fields."""

    if not isinstance(payload, dict):
        return None
    owner: dict[str, Any] = {}
    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0:
        owner["pid"] = pid
    for key in ("owner_id", "run_id", "kind", "started_at"):
        value = payload.get(key)
        if value is None:
            continue
        if key == "started_at":
            owner[key] = _safe_text(value, limit=64)
        else:
            owner[key] = _safe_text(value)
    # Older lock files only have run_id.  New callers use owner_id, but keep
    # the legacy field visible so existing manifests and operators remain
    # understandable during the migration.
    if "owner_id" not in owner and "run_id" in owner:
        owner["owner_id"] = owner["run_id"]
    return owner or None


def _default_runtime_root() -> Path:
    from .config import DATA_DIR

    configured_root = os.environ.get("VC_NEWS_RUNTIME_DIR") or os.environ.get("VC_NEWS_RUNS_DIR")
    return Path(configured_root).expanduser() if configured_root else DATA_DIR / "runs"


def run_lock_path(runtime_root: str | Path | None = None) -> Path:
    """Return the canonical shared lock path for a runtime root."""

    root = Path(runtime_root).expanduser() if runtime_root is not None else _default_runtime_root()
    return root / LOCK_FILENAME


def read_run_lock(runtime_root: str | Path | None = None) -> dict[str, Any]:
    """Return a bounded status snapshot for the shared daily-run lock.

    The result is deliberately a plain JSON-compatible dictionary for API and
    diagnostics callers.  ``active``/``locked`` are aliases for callers that
    prefer either vocabulary; stale locks never block a GUI request and will
    be reclaimed by the next :meth:`RunLock.acquire` call.
    """

    path = run_lock_path(runtime_root)
    try:
        max_runtime_seconds = _max_runtime()
    except (TypeError, ValueError, OverflowError):
        max_runtime_seconds = DEFAULT_MAX_RUNTIME_SECONDS
    payload, exists = _read_payload(path)
    if not exists:
        return {
            "status": "free",
            "active": False,
            "locked": False,
            "stale": False,
            "owner": None,
        }
    stale = _is_stale(path, payload, max_runtime_seconds)
    active = not stale
    return {
        "status": "active" if active else "stale",
        "active": active,
        "locked": active,
        "stale": stale,
        "owner": _safe_owner(payload),
    }


class RunLock:
    """Atomic cross-process lock with conservative stale-lock recovery."""

    def __init__(
        self,
        path: str | Path,
        owner_id: str,
        kind: str = "headless",
        *,
        max_runtime_seconds: int | float | str | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.owner_id = _safe_text(owner_id)
        self.kind = _safe_text(kind, limit=32) or "headless"
        self.max_runtime_seconds = _max_runtime(max_runtime_seconds)
        self.acquired = False
        self.owner: dict[str, Any] | None = None

    def acquire(self) -> tuple[bool, dict[str, Any] | None]:
        """Try to acquire the lock, returning ``(acquired, existing_owner)``."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "owner_id": self.owner_id,
            # ``run_id`` preserves the established headless lock-file shape.
            # It is harmless for other lock kinds and keeps old diagnostics
            # readable while every caller migrates to owner_id.
            "run_id": self.owner_id,
            "kind": self.kind,
            "started_at": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for _ in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(descriptor, encoded.encode("utf-8"))
                finally:
                    os.close(descriptor)
                self.acquired = True
                self.owner = _safe_owner(payload)
                return True, None
            except FileExistsError:
                existing, exists = _read_payload(self.path)
                if not exists:
                    continue
                if not _is_stale(self.path, existing, self.max_runtime_seconds):
                    owner = _safe_owner(existing)
                    self.owner = owner
                    return False, owner
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    owner = _safe_owner(existing)
                    self.owner = owner
                    return False, owner
        existing, _ = _read_payload(self.path)
        owner = _safe_owner(existing)
        self.owner = owner
        return False, owner

    def release(self) -> None:
        """Release only the lock currently owned by this instance."""

        if not self.acquired:
            return
        try:
            current, _ = _read_payload(self.path)
            if current:
                current_owner = current.get("owner_id", current.get("run_id"))
                current_kind = current.get("kind", "headless")
                if _safe_text(current_owner) != self.owner_id or _safe_text(current_kind, limit=32) != self.kind:
                    return
            self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False
            self.owner = None

    @contextlib.contextmanager
    def context(self) -> Iterator[bool]:
        """Acquire for a ``with`` block and yield whether it was obtained."""

        acquired, _owner = self.acquire()
        try:
            yield acquired
        finally:
            if acquired:
                self.release()


__all__ = ["RunLock", "read_run_lock", "run_lock_path", "DEFAULT_MAX_RUNTIME_SECONDS", "LOCK_FILENAME"]
