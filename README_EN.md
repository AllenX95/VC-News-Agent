[中文版](README.md)

# VC News Agent AI

VC News Agent AI is a local investment-intelligence workspace for VC/PE teams and AI industry researchers. It collects content from Chinese and international venture media, AI publications, official research blogs, Hacker News, GitHub Trending, Product Hunt, and other sources; deduplicates and classifies the results in a local SQLite database; and exposes them through a WebUI and a headless daily-report workflow.

The application runs locally by default, and both its database and LLM credentials remain on the local machine. Crawling, content management, and deterministic daily reports work without an LLM. When a model is configured, the application can also generate summaries, tags, entities, financing filters, and enhanced reports.

## Key features

- **Multi-source collection**: built-in sources include 36Kr, Cyzone, PEDaily, QbitAI, TechCrunch AI, The Verge AI, VentureBeat AI, OpenAI, Anthropic, Google DeepMind, Meta AI, Hugging Face, Y Combinator, GitHub Trending, Product Hunt, and Hacker News.
- **Intelligence processing**: deduplication, full-text caching, AI-relevance classification, content and sector tags, entity extraction, priority scoring, and human review.
- **Financing workflow**: identify high-relevance AI financing news, merge multi-source events, and confirm, exclude, edit, merge, split, or select primary sources.
- **Research workspace**: content library, intelligence inbox, financing events, watchlist, taxonomy, daily summaries, and a versioned report workspace.
- **Automated daily reports**: create HTML, Markdown, JSON, logs, and manifests from a fixed Beijing-time window; automatically fall back to a deterministic report when the LLM is unavailable.
- **Local-first security**: SQLite storage, locally encrypted API keys and Base URLs, and a shared run lock that prevents WebUI writes from colliding with headless jobs.

## Technology stack

| Layer | Technology |
| --- | --- |
| Backend | Python, FastAPI, SQLAlchemy, APScheduler, Jinja2 |
| Frontend | Vue 3, TypeScript, Vite, Element Plus, Pinia, Vue Router |
| Data | SQLite |
| Reports | HTML, Markdown, JSON |
| LLM | OpenAI / OpenAI-compatible APIs, Anthropic API |

## Repository layout

```text
.
├── app.py                         # FastAPI entry point and production frontend hosting
├── ai_agent/                      # Models, collection, LLM, reporting, and scheduling logic
│   ├── api_v1.py                  # /api/v1 REST API
│   ├── headless.py                # Headless CLI entry point
│   ├── orchestration.py           # Daily-run orchestration
│   ├── services.py                # Crawling, LLM, summaries, backups, and scheduler services
│   └── templates/                 # Daily-report HTML templates
├── frontend/                      # Vue WebUI
├── tests/                         # Python tests
├── 启动AI投资情报Agent.bat        # Windows one-click launcher
├── 启动AI投资情报Agent.ps1        # Windows launcher implementation
├── requirements.txt               # Python dependencies
└── package.json                   # Frontend workspace and build commands
```

## Requirements

- Python 3.10 or later (Python 3.11+ recommended)
- Node.js `^20.19.0` or `>=22.12.0` (required by the current Vite version)
- npm
- Windows for the bundled one-click launcher; use the manual commands on macOS/Linux

## Installation

```bash
git clone https://github.com/AllenX95/VC-News-Agent.git
cd VC-News-Agent
python -m venv .venv
```

Activate the virtual environment and install dependencies:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
npm install
npm run frontend:build
```

```bash
# macOS / Linux
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
npm install
npm run frontend:build
```

On first startup, the application creates its data directories, SQLite database, initial source registry, system settings, and default prompts.

## Start the WebUI

### One-click launch on Windows

After installing dependencies, double-click `启动AI投资情报Agent.bat` or run:

```powershell
.\启动AI投资情报Agent.ps1
```

The script rebuilds the frontend, selects an available port from `8011` through `8020`, starts the backend in `external` scheduler mode, and opens the browser. Logs are written to `logs/`.

To avoid opening a browser:

```powershell
.\启动AI投资情报Agent.ps1 -NoBrowser
```

### Manual launch

Build the production frontend and start FastAPI:

```powershell
$env:VC_NEWS_SCHEDULER_MODE = "external"
npm run frontend:build
.\.venv\Scripts\python.exe -B app.py
```

macOS/Linux:

```bash
VC_NEWS_SCHEDULER_MODE=external npm run frontend:build
VC_NEWS_SCHEDULER_MODE=external .venv/bin/python -B app.py
```

The default URL is <http://127.0.0.1:8011/>. The backend API prefix is `/api/v1`, and the health endpoint is `/api/v1/health`.

Available startup options:

```bash
python app.py --host 127.0.0.1 --port 8011 --no-open-browser
```

### Frontend development mode

Run the backend and Vite development server separately:

```powershell
# Terminal 1
.\.venv\Scripts\python.exe -B app.py

# Terminal 2
npm run frontend:dev
```

Vite listens on <http://127.0.0.1:5173/> by default and proxies `/api` and `/shutdown` to `127.0.0.1:8011`.

## Typical workflow

1. Open **Sources**, enable or adjust the sources you need, and run a single-source or batch crawl.
2. In **LLM / Prompt**, add and test a model configuration, then assign a model and prompt to the required tasks. The LLM is optional.
3. Use the **Intelligence Inbox** and **Content Library** to filter, review, favorite, archive, or reprocess content.
4. Build candidate events in **Financing Events** and resolve duplicate or conflicting sources.
5. Add companies or events to the **Watchlist**, then set priority, status, notes, and the next review date.
6. Generate, edit, version, and export work in **Daily Summaries** or the **Report Workspace**.

Supported LLM provider types are `openai` and `anthropic`. For an OpenAI-compatible service, select `openai` and enter its Base URL, API key, and model name. When Base URL is blank, the official OpenAI or Anthropic endpoint is used. Credentials are encrypted using `data/secret.key` before being stored in the local database; do not publish that file.

## Headless automation

Headless mode does not require the WebUI, a browser, or FastAPI. It is suitable for Codex Automation, Windows Task Scheduler, cron, or another external scheduler.

```powershell
# Check local prerequisites
.\.venv\Scripts\python.exe -m ai_agent.headless health

# Run today's daily report
.\.venv\Scripts\python.exe -m ai_agent.headless daily

# Select a Beijing-time date and use only existing database content
.\.venv\Scripts\python.exe -m ai_agent.headless daily --date 2026-08-17 --skip-crawl

# Create a new run even if the date already has a successful run
.\.venv\Scripts\python.exe -m ai_agent.headless daily --date 2026-08-17 --force

# Write runtime artifacts under another root directory
.\.venv\Scripts\python.exe -m ai_agent.headless daily --output-dir D:\vc-news-runs
```

The final stdout line is always machine-readable JSON containing the status, exit code, run ID, manifest path, artifacts, and warnings.

Default artifacts:

```text
data/runs/
├── report/<YYYYMMDD>-daily-report.html
└── artifacts/
    ├── <YYYYMMDD>-<run-id>-run-manifest.json
    ├── <YYYYMMDD>-<run-id>-report-data.json
    ├── <YYYYMMDD>-<run-id>-daily-report.md
    ├── <YYYYMMDD>-<run-id>-run.log
    └── <YYYYMMDD>-latest.json
```

Every daily report has three top-level sections: technology progress, industry news, and financing news. Its content window is the half-open Beijing-time interval `[10:00 on the previous day, 10:00 on the target day)`. Content published exactly at 10:00 belongs to the next report, preventing duplicates or gaps.

## Scheduler modes

Use `VC_NEWS_SCHEDULER_MODE` to select the scheduling owner:

| Value | Behavior |
| --- | --- |
| `external` | Default. The WebUI does not start APScheduler or run startup catch-up; an external automation wakes the headless job. |
| `internal` | The WebUI process starts APScheduler and performs startup catch-up. |
| `disabled` | Automatic scheduling is disabled; jobs run only when requested manually. |

The headless CLI never starts APScheduler. Avoid enabling both internal and external scheduling. The shared run lock protects concurrent writes, but duplicate wakeups are unnecessary.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `VC_NEWS_HOST` | `127.0.0.1` | Web backend bind address |
| `VC_NEWS_PORT` | `8011` | Web backend port |
| `VC_NEWS_SCHEDULER_MODE` | `external` | `external`, `internal`, or `disabled` |
| `VC_NEWS_DISABLE_STARTUP_CATCHUP` | empty | Set to `1`/`true` to skip startup catch-up in internal mode |
| `VC_NEWS_DB_PATH` | `data/ai_market_daily_main.sqlite3` | Override the SQLite database path |
| `VC_NEWS_SQLITE_JOURNAL_MODE` | `OFF` | SQLite journal mode; use `WAL` in a normal local environment if desired |
| `VC_NEWS_PROXY_MODE` | `off` | Proxy mode: `off`, `system`, `custom`, or environment inheritance mode |
| `VC_NEWS_RUNTIME_DIR` | `data/runs` | Override the headless runtime root |
| `VC_NEWS_RUNS_DIR` | empty | Compatibility alias for `VC_NEWS_RUNTIME_DIR` |
| `VC_NEWS_MAX_RUNTIME_SECONDS` | application default | Headless timeout and stale-lock detection |

Configure the proxy URL and `NO_PROXY` in **Settings**. `system` mode reads the Windows system proxy; `custom` mode uses the custom proxy saved in the UI.

## Local data and backups

```text
data/ai_market_daily_main.sqlite3   # Primary database
data/secret.key                     # Local credential-encryption key
data/reports/                       # Financing and other reports
data/runs/                          # Headless reports and runtime artifacts
archives/                           # Manual archives
backups/                            # Database backups
logs/                               # Windows launcher logs
```

Database migrations invoke the existing backup capability when required, and migration records are stored in the `schema_migrations` table. The repository defaults to `journal_mode=OFF` for restricted-sandbox compatibility. In a normal local environment, set `VC_NEWS_SQLITE_JOURNAL_MODE=WAL` before startup if desired.

## Tests and build checks

```powershell
# Python tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Frontend type check and production build
npm run frontend:build

# Headless prerequisite check
.\.venv\Scripts\python.exe -m ai_agent.headless health
```

## Troubleshooting

- **The home page says `frontend/dist` is missing**: run `npm install` and `npm run frontend:build`, then restart the backend.
- **The one-click launcher cannot find Python**: confirm that `.venv` exists in the repository root and that `pip install -r requirements.txt` completed successfully.
- **The port is occupied**: select another port with `--port` or `VC_NEWS_PORT`. The Windows launcher automatically tries ports `8011` through `8020`.
- **The API returns HTTP 423**: a headless crawl or another write request owns the run lock. Wait for it to finish and retry; read-only pages remain available.
- **An LLM task does not run**: verify that the connection test succeeds, the task is enabled, and both a model and prompt are assigned in **LLM / Prompt**.
- **A source cannot be crawled**: configure a proxy in **Settings** if network access is restricted. Some extractors depend on page structure, so site redesigns or anti-bot policies can break an individual source.

## Design documents

- `ai市场日报agent_prd_开发确认版.md`: product requirements and implementation confirmation
- `docs/prd/`: module-level PRDs
- `docs/design/vc-news-agent-ai-headless-design.md`: headless automation design
- `CODEX_AUTOMATION_PLAN.md`: Codex Automation implementation plan
- `CODEX_AUTOMATION_PROMPT.md`: Codex Automation task prompt
