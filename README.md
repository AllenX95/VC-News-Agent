# AI 投资情报 Agent

本地个人版 AI 投资情报信息收集与整理工具。开发依据见：

- `ai市场日报agent_prd_开发确认版.md`

## 启动

```powershell
.\.venv\Scripts\python.exe -B app.py
```

打开：

```text
http://127.0.0.1:8011/
```

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
