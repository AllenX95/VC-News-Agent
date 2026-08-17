# VC-news-agent-AI

本地个人版 AI 投资情报信息收集与整理工具。仓库和项目目录的规范名称均为 `VC-news-agent-AI`。开发依据见：

- `ai市场日报agent_prd_开发确认版.md`

## 启动

首次运行或前端变更后，安装依赖并构建生产 WebUI：

```powershell
npm install
npm run frontend:build
```

推荐双击 `启动AI投资情报Agent.bat`，它会构建 WebUI、以 external 调度模式启动 FastAPI，并在浏览器中打开应用。也可以手动启动：

```powershell
$env:VC_NEWS_SCHEDULER_MODE='external'
.\.venv\Scripts\python.exe -B app.py
```

打开：

```text
http://127.0.0.1:8011/
```

## Headless 自动执行

无需启动 GUI、浏览器或 FastAPI 即可执行健康检查和同步日报任务：

```powershell
.\.venv\Scripts\python.exe -m ai_agent.headless health
.\.venv\Scripts\python.exe -m ai_agent.headless daily
```

补跑或只基于数据库重新生成报告：

```powershell
.\.venv\Scripts\python.exe -m ai_agent.headless daily --date 2026-08-17 --skip-crawl
.\.venv\Scripts\python.exe -m ai_agent.headless daily --date 2026-08-17 --force
```

CLI 的 stdout 最后一行是机器可读 JSON，包含状态、退出码和本次 manifest 路径。用户交付 HTML 保存在 `data/runs/report/`：

- `<YYYYMMDD>-daily-report.html`

运行支撑文件统一保存在 `data/runs/artifacts/`，通过日期和 run ID 区分：

- `<YYYYMMDD>-<run-id>-run-manifest.json`
- `<YYYYMMDD>-<run-id>-report-data.json`
- `<YYYYMMDD>-<run-id>-daily-report.md`
- `<YYYYMMDD>-<run-id>-run.log`
- `<YYYYMMDD>-latest.json`

日报 HTML 固定包含“技术进展、产业新闻、融资新闻”三个一级类目。

GUI 的 LLM / Prompt 页面会显示“每日投资日报增强”任务。为该任务选择 provider、模型和 Prompt 后，Headless 会在确定性报告基础上调用模型增强 executive summary、条目摘要、投资意义和主题分类。原始 URL、来源和内容/事件 ID 始终由程序保留；未配置或模型失败时自动交付确定性降级版。

Dashboard 与报告工作区会显示最新自动运行状态、计数、告警和 HTML 日报入口。Headless 或 GUI 后台抓取持有共享运行锁时，冲突的 GUI 写请求返回 HTTP 423，状态和其他只读页面仍可访问。

### 调度模式

Codex Automation 是默认且唯一的调度方，WebUI 默认模式为 `external`，不会启动内部 APScheduler。只有明确不使用外部调度器时，才应手动选择 `internal`：

```powershell
$env:VC_NEWS_SCHEDULER_MODE='internal'
```

支持的值：

- `external`：默认值；不启动内部调度器，不执行启动补抓；
- `internal`：显式启用内部调度器与启动补抓；
- `disabled`：仅保留人工执行。

Headless CLI 本身永远不会启动 APScheduler。

每日任务的内容窗口统一为北京时间半开区间 `[前一天 10:00, 当天 10:00)`。Codex 或内部调度器只负责在每天 10:00 唤醒任务；抓取过滤和日报取数共用这一窗口，因此恰好在 10:00 发布的内容会归入下一期，避免重复或遗漏。

## 数据

- SQLite 数据库：`data/ai_market_daily_main.sqlite3`
- 本地密钥：`data/secret.key`
- 手动归档：`archives/`
- 备份：`backups/`

当前 Codex 沙箱不允许 SQLite WAL 的文件 rename/delete 操作，因此默认使用 `journal_mode=OFF` 以便本地 smoke test 可以运行。

在正常本机环境中，如需启用 PRD 要求的 WAL：

```powershell
$env:VC_NEWS_SQLITE_JOURNAL_MODE='WAL'
.\.venv\Scripts\python.exe -B app.py
```

## 已实现的 P0 骨架

- FastAPI 本地 Web
- SQLite + SQLAlchemy 数据模型
- Source Registry 初始信息源
- 每日 10:00 北京时间调度与错过补抓逻辑
- API / HTTP / Browser Worker 兜底的抓取分发框架
- Hacker News Top 20 端到端抓取
- 内容库、来源分类、搜索、收藏、编辑
- 标签与轻量实体
- LLM 配置加密保存与 Prompt 管理
- 每日汇总结构化快照与 Markdown 导出
- 自动/手动备份基础能力

## v0.3 工作流

- 融资事件：从已识别的融资内容生成候选事件，自动合并重复来源，并支持人工确认、排除、编辑、合并、拆分和主要来源管理。
- 关注列表：从内容库或融资事件手动加入关注，保存对象快照、备注、优先级、状态和下次回看日期。
- 报告工作区：报告生成前预览并排序输入，生成 Markdown 草稿，编辑/重新生成时创建新版本，并可导出任意历史版本。

数据库升级会在 v0.3 迁移前调用现有备份能力，迁移记录保存在 `schema_migrations` 表中。
