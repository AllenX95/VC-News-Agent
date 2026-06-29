from __future__ import annotations

from pathlib import Path
import os
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "backups"
ARCHIVE_DIR = BASE_DIR / "archives"

DEFAULT_DB_PATH = DATA_DIR / "ai_market_daily_main.sqlite3"
MIGRATED_DB_PATH = DATA_DIR / "ai_market_daily_main_v2.sqlite3"
DB_PATH = Path(
    os.environ.get("VC_NEWS_DB_PATH")
    or (MIGRATED_DB_PATH if MIGRATED_DB_PATH.exists() else DEFAULT_DB_PATH)
)
SECRET_KEY_PATH = DATA_DIR / "secret.key"
TIMEZONE_NAME = "Asia/Shanghai"
TZ = ZoneInfo(TIMEZONE_NAME)
SQLITE_JOURNAL_MODE = os.environ.get("VC_NEWS_SQLITE_JOURNAL_MODE", "OFF").upper()
PROXY_MODE = os.environ.get("VC_NEWS_PROXY_MODE", "off").strip().lower()

DEFAULT_CRAWL_TIME = "10:00"
DEFAULT_CACHE_HOURS = 48

PROXY_ENV_NAMES = {
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
}
INVALID_PROXY_VALUES = {
    "127.0.0.1:9",
    "http://127.0.0.1:9",
    "https://127.0.0.1:9",
}


def _normalize_proxy(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        return f"http://{value}"
    return value


def _parse_proxy_server(proxy_server: str | None) -> dict[str, str]:
    proxy_server = (proxy_server or "").strip()
    if not proxy_server:
        return {}
    proxies: dict[str, str] = {}
    parts = [part.strip() for part in proxy_server.split(";") if part.strip()]
    keyed_parts = [part for part in parts if "=" in part]
    if keyed_parts:
        for part in keyed_parts:
            scheme, value = part.split("=", 1)
            scheme = scheme.strip().lower()
            if scheme in {"http", "https"}:
                proxies[scheme] = _normalize_proxy(value)
        if "http" in proxies and "https" not in proxies:
            proxies["https"] = proxies["http"]
        if "https" in proxies and "http" not in proxies:
            proxies["http"] = proxies["https"]
        return proxies
    proxy = _normalize_proxy(proxy_server)
    return {"http": proxy, "https": proxy}


def _read_windows_system_proxy() -> tuple[dict[str, str], str]:
    if os.name != "nt":
        return {}, ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            proxy_enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            if proxy_enabled != 1:
                return {}, ""
            proxy_server = winreg.QueryValueEx(key, "ProxyServer")[0]
            try:
                proxy_override = winreg.QueryValueEx(key, "ProxyOverride")[0]
            except FileNotFoundError:
                proxy_override = ""
    except OSError:
        return {}, ""
    return _parse_proxy_server(proxy_server), str(proxy_override or "")


def _clear_proxy_env() -> None:
    for key in list(os.environ):
        if key.lower() in PROXY_ENV_NAMES:
            os.environ.pop(key, None)


def _env_proxy_is_invalid() -> bool:
    for key, value in os.environ.items():
        if key.lower() in PROXY_ENV_NAMES and (value or "").strip().lower() in INVALID_PROXY_VALUES:
            return True
    return False


def _normalize_no_proxy(proxy_override: str) -> str:
    values = ["localhost", "127.0.0.1", "::1"]
    for part in (proxy_override or "").replace(";", ",").split(","):
        item = part.strip()
        if not item or item == "<local>":
            continue
        values.append(item)
    return ",".join(dict.fromkeys(values))


def normalize_proxy_mode(value: str | None) -> str:
    mode = (value or "off").strip().lower()
    aliases = {
        "none": "off",
        "direct": "off",
        "disable": "off",
        "disabled": "off",
        "inherit": "system",
        "inherit_system": "system",
        "windows_system": "system",
        "manual": "custom",
    }
    return aliases.get(mode, mode)


def apply_network_proxy_settings(
    mode: str | None = None,
    proxy_url: str | None = None,
    no_proxy: str | None = None,
) -> dict[str, str]:
    active_mode = normalize_proxy_mode(mode or PROXY_MODE)
    if active_mode == "off":
        _clear_proxy_env()
        return {"mode": active_mode, "source": "disabled", "http": "", "https": "", "all": "", "no_proxy": ""}

    if active_mode == "custom":
        proxy = _normalize_proxy(proxy_url)
        _clear_proxy_env()
        if not proxy:
            return {"mode": active_mode, "source": "custom_missing", "http": "", "https": "", "all": "", "no_proxy": ""}
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["ALL_PROXY"] = proxy
        os.environ["NO_PROXY"] = _normalize_no_proxy(no_proxy or "")
        return current_proxy_info(active_mode, "custom")

    if active_mode == "system":
        system_proxies, proxy_override = _read_windows_system_proxy()
        _clear_proxy_env()
        if system_proxies:
            os.environ["HTTP_PROXY"] = system_proxies.get("http", "")
            os.environ["HTTPS_PROXY"] = system_proxies.get("https", system_proxies.get("http", ""))
            os.environ["ALL_PROXY"] = system_proxies.get("https", system_proxies.get("http", ""))
            os.environ["NO_PROXY"] = _normalize_no_proxy(proxy_override)
            return current_proxy_info(active_mode, "windows_system")
        return {"mode": active_mode, "source": "windows_system_unavailable", "http": "", "https": "", "all": "", "no_proxy": ""}

    if _env_proxy_is_invalid():
        _clear_proxy_env()
        return {"mode": active_mode, "source": "invalid_env_cleared", "http": "", "https": "", "all": "", "no_proxy": ""}

    return current_proxy_info(active_mode, "environment")


def current_proxy_info(mode: str | None = None, source: str = "environment") -> dict[str, str]:
    return {
        "mode": normalize_proxy_mode(mode or PROXY_MODE),
        "source": source,
        "http": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy", ""),
        "https": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy", ""),
        "all": os.environ.get("ALL_PROXY") or os.environ.get("all_proxy", ""),
        "no_proxy": os.environ.get("NO_PROXY") or os.environ.get("no_proxy", ""),
    }


INITIAL_PROXY_INFO = apply_network_proxy_settings()


def ensure_directories() -> None:
    for path in (DATA_DIR, BACKUP_DIR, ARCHIVE_DIR):
        path.mkdir(parents=True, exist_ok=True)
