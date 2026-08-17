[英文版](README_EN.md)

# VC News Agent AI

VC News Agent AI 是一款面向 VC/PE 与 AI 行业研究人员的本地投资情报工具。它聚合国内外创投媒体、AI 媒体、官方研究博客、Hacker News、GitHub Trending、Product Hunt 等信息源，将内容去重、分类并存入本地 SQLite 数据库，再通过 WebUI 或无界面命令生成日报、融资报告和可持续跟踪的关注列表。

项目默认只在本机运行，数据库和 LLM 凭据均保存在本地。未配置 LLM 时，抓取、内容管理和确定性日报仍可使用；配置模型后可进一步完成摘要、标签、实体识别、融资筛选和报告增强。

## 主要功能

- **多源采集**：内置 36氪、创业邦、投资界、量子位、TechCrunch AI、The Verge AI、VentureBeat AI、OpenAI、Anthropic、Google DeepMind、Meta AI、Hugging Face、YC、GitHub Trending、Product Hunt 和 Hacker News 等信息源。
- **情报处理**：内容去重、全文缓存、AI 相关性判断、内容类型与赛道标签、实体抽取、情报优先级和人工复核。
- **融资工作流**：识别高相关 AI 融资新闻，合并多来源事件，支持确认、排除、编辑、合并、拆分与主要来源管理。
- **研究工作台**：内容库、情报收件箱、融资事件、关注列表、标签与实体、每日汇总及可版本化的报告工作区。
- **自动化日报**：按北京时间固定窗口抓取并生成 HTML、Markdown、JSON、运行日志与 manifest；LLM 不可用时自动回退到确定性报告。
- **本地安全**：SQLite 本地存储，API Key 与 Base URL 使用本地密钥加密；运行锁可避免 WebUI 写操作与 Headless 任务互相覆盖。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | Python、FastAPI、SQLAlchemy、APScheduler、Jinja2 |
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router |
| 数据 | SQLite |
| 报告 | HTML、Markdown、JSON |
| LLM | OpenAI / OpenAI-compatible API、Anthropic API |

## 项目结构

```text
.
├── app.py                         # FastAPI 入口及生产前端托管
├── ai_agent/                      # 数据模型、采集、LLM、报告和调度逻辑
│   ├── api_v1.py                  # /api/v1 REST API
│   ├── headless.py                # 无界面命令行入口
│   ├── orchestration.py           # 日报执行编排
│   ├── services.py                # 抓取、LLM、汇总、备份和调度服务
│   └── templates/                 # 日报 HTML 模板
├── frontend/                      # Vue WebUI
├── tests/                         # Python 测试
├── 启动AI投资情报Agent.bat        # Windows 一键启动入口
├── 启动AI投资情报Agent.ps1        # Windows 启动实现
├── requirements.txt               # Python 依赖
└── package.json                   # 前端 workspace 与构建命令
```

## 环境要求

- Python 3.10 或更高版本（推荐 Python 3.11+）
- Node.js `^20.19.0` 或 `>=22.12.0`（由当前 Vite 版本要求）
- npm
- Windows 可使用仓库自带的一键启动脚本；macOS/Linux 请使用手动命令

## 安装

```bash
git clone https://github.com/AllenX95/VC-News-Agent.git
cd VC-News-Agent
python -m venv .venv
```

激活虚拟环境并安装依赖：

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

首次启动时，程序会自动创建数据目录、SQLite 数据库、初始信息源、系统设置和默认 Prompt。

## 启动 WebUI

### Windows 一键启动

完成依赖安装后，双击 `启动AI投资情报Agent.bat`，或执行：

```powershell
.\启动AI投资情报Agent.ps1
```

脚本会重新构建前端、在 `8011`—`8020` 中选择可用端口、以 `external` 调度模式启动后端并打开浏览器。日志写入 `logs/`。

不自动打开浏览器：

```powershell
.\启动AI投资情报Agent.ps1 -NoBrowser
```

### 手动启动

先构建生产前端，再启动 FastAPI：

```powershell
$env:VC_NEWS_SCHEDULER_MODE = "external"
npm run frontend:build
.\.venv\Scripts\python.exe -B app.py
```

macOS/Linux：

```bash
VC_NEWS_SCHEDULER_MODE=external npm run frontend:build
VC_NEWS_SCHEDULER_MODE=external .venv/bin/python -B app.py
```

默认地址：<http://127.0.0.1:8011/>。后端 API 前缀为 `/api/v1`，健康检查为 `/api/v1/health`。

可用启动参数：

```bash
python app.py --host 127.0.0.1 --port 8011 --no-open-browser
```

### 前端开发模式

分别启动后端和 Vite 开发服务器：

```powershell
# 终端 1
.\.venv\Scripts\python.exe -B app.py

# 终端 2
npm run frontend:dev
```

Vite 默认监听 <http://127.0.0.1:5173/>，并将 `/api` 和 `/shutdown` 代理到 `127.0.0.1:8011`。

## 基本使用流程

1. 打开“信息源管理”，启用或调整需要采集的来源，然后执行单源或批量抓取。
2. 在“LLM / Prompt”中添加模型配置并测试连接，再为所需任务选择模型和 Prompt。LLM 为可选配置。
3. 在“情报收件箱”和“内容库”中筛选、复核、收藏、归档或重新处理内容。
4. 在“融资事件”中构建候选事件并处理重复或冲突来源。
5. 将公司或事件加入“关注列表”，设置优先级、状态、备注和下次回看日期。
6. 在“每日汇总”或“报告工作区”生成、编辑、版本化并导出报告。

LLM Provider 支持 `openai` 和 `anthropic`。OpenAI-compatible 服务请选择 `openai` 并填写对应 Base URL、API Key 和模型名；未填写 Base URL 时分别使用 OpenAI 或 Anthropic 官方接口。凭据经 `data/secret.key` 加密后写入本地数据库，请勿将该文件公开。

## Headless 自动执行

Headless 模式无需启动 WebUI、浏览器或 FastAPI，适合 Codex Automation、Windows 任务计划程序、cron 或其他外部调度器。

```powershell
# 检查本地运行条件
.\.venv\Scripts\python.exe -m ai_agent.headless health

# 运行当天日报
.\.venv\Scripts\python.exe -m ai_agent.headless daily

# 指定北京时间日期；只使用数据库现有内容
.\.venv\Scripts\python.exe -m ai_agent.headless daily --date 2026-08-17 --skip-crawl

# 即使该日期已有成功记录，也创建一次新运行
.\.venv\Scripts\python.exe -m ai_agent.headless daily --date 2026-08-17 --force

# 将运行产物写入其他根目录
.\.venv\Scripts\python.exe -m ai_agent.headless daily --output-dir D:\vc-news-runs
```

stdout 最后一行始终为机器可读 JSON，其中包含状态、退出码、run ID、manifest 路径、产物和告警。

默认产物：

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

日报固定包含“技术进展、产业新闻、融资新闻”三个一级类目。内容窗口为北京时间半开区间 `[前一天 10:00, 当天 10:00)`；恰好在 10:00 发布的内容归入下一期，避免重复或遗漏。

## 调度模式

通过 `VC_NEWS_SCHEDULER_MODE` 选择调度所有者：

| 值 | 行为 |
| --- | --- |
| `external` | 默认。WebUI 不启动内部 APScheduler，也不执行启动补抓；由外部自动化唤醒 Headless 任务。 |
| `internal` | WebUI 进程启用 APScheduler，并执行启动补抓。 |
| `disabled` | 禁用自动调度，仅允许人工执行。 |

Headless CLI 本身不会启动 APScheduler。请避免同时启用内部和外部调度器；共享运行锁虽能阻止并发写入，但重复唤醒没有必要。

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `VC_NEWS_HOST` | `127.0.0.1` | Web 后端监听地址 |
| `VC_NEWS_PORT` | `8011` | Web 后端端口 |
| `VC_NEWS_SCHEDULER_MODE` | `external` | `external`、`internal` 或 `disabled` |
| `VC_NEWS_DISABLE_STARTUP_CATCHUP` | 空 | 设为 `1`/`true` 时跳过内部模式的启动补抓 |
| `VC_NEWS_DB_PATH` | `data/ai_market_daily_main.sqlite3` | 覆盖 SQLite 数据库路径 |
| `VC_NEWS_SQLITE_JOURNAL_MODE` | `OFF` | SQLite journal mode；正常本机环境可设为 `WAL` |
| `VC_NEWS_PROXY_MODE` | `off` | 网络代理模式：`off`、`system`、`custom` 或环境继承模式 |
| `VC_NEWS_RUNTIME_DIR` | `data/runs` | 覆盖 Headless 运行根目录 |
| `VC_NEWS_RUNS_DIR` | 空 | `VC_NEWS_RUNTIME_DIR` 的兼容别名 |
| `VC_NEWS_MAX_RUNTIME_SECONDS` | 程序默认值 | Headless 超时与过期运行锁判定 |

代理地址和 `NO_PROXY` 可在 WebUI“系统设置”中配置。`system` 模式读取 Windows 系统代理；`custom` 模式使用界面中保存的自定义代理。

## 本地数据与备份

```text
data/ai_market_daily_main.sqlite3   # 主数据库
data/secret.key                     # 本地凭据加密密钥
data/reports/                       # 融资等报告
data/runs/                          # Headless 报告与运行产物
archives/                           # 手动归档
backups/                            # 数据库备份
logs/                               # Windows 启动脚本日志
```

数据库迁移会在需要时调用已有备份能力，迁移记录保存在 `schema_migrations` 表。仓库默认使用 `journal_mode=OFF` 以兼容受限沙箱；普通本机可在启动前设置 `VC_NEWS_SQLITE_JOURNAL_MODE=WAL`。

## 测试与构建检查

```powershell
# Python 测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 前端类型检查与生产构建
npm run frontend:build

# Headless 运行条件检查
.\.venv\Scripts\python.exe -m ai_agent.headless health
```

## 常见问题

- **首页提示缺少 `frontend/dist`**：执行 `npm install` 和 `npm run frontend:build`，然后重启后端。
- **一键启动提示找不到 Python 环境**：确认项目根目录存在 `.venv`，并已执行 `pip install -r requirements.txt`。
- **端口被占用**：手动用 `--port` 或 `VC_NEWS_PORT` 指定其他端口；Windows 一键脚本会自动尝试 `8011`—`8020`。
- **API 返回 HTTP 423**：Headless 抓取或另一个写请求持有运行锁。等待当前任务结束后重试；只读页面仍可访问。
- **模型任务未运行**：在“LLM / Prompt”中确认配置测试成功、任务已启用，且任务已关联模型与 Prompt。
- **抓取受网络限制**：在“系统设置”中配置代理；部分来源依赖页面结构，站点改版或反爬策略可能造成单源失败。

## 设计文档

- `ai市场日报agent_prd_开发确认版.md`：产品需求与开发确认版
- `docs/prd/`：分模块 PRD
- `docs/design/vc-news-agent-ai-headless-design.md`：Headless 自动化设计
- `CODEX_AUTOMATION_PLAN.md`：Codex Automation 实施方案
- `CODEX_AUTOMATION_PROMPT.md`：Codex Automation 任务提示词
