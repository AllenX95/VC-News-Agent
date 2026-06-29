# AI 投资情报 Agent 桌面 GUI 重构 PRD

版本：v0.1  
日期：2026-06-08  
目标形态：Tauri + Vue 桌面应用，保留 Python FastAPI 后端与现有 SQLite 数据  
代码阅读范围：`app.py`、`ai_agent/`、`templates/`、`static/`、启动脚本、主 SQLite 表结构

---

## 1. 背景

当前项目是一个本地 AI 投资情报 Agent，核心能力已经在 Python 后端中实现：

- `app.py` 提供 FastAPI 本地服务，默认运行在 `127.0.0.1:8011`。
- `ai_agent.services` 承载核心业务逻辑，包括抓取、LLM 处理、每日汇总、备份、调度、内容查询。
- `ai_agent.models` 和 `ai_agent.database` 使用 SQLAlchemy + SQLite。
- 现有 UI 是 Jinja 模板页面，位于 `templates/`，样式在 `static/style.css`。
- 当前启动方式是 `.venv\Scripts\python.exe -B app.py`，并通过 PowerShell 脚本查找 8011 到 8020 可用端口。

用户希望把项目重构为有独立 GUI 的程序，技术方向为 Tauri + Vue + Python 后端，同时尽可能不动后端以及现有数据。因此本 PRD 的重点不是重新设计业务能力，而是把既有本地 Web 应用升级为桌面 GUI 应用。

---

## 2. 产品目标

### 2.1 核心目标

将现有 AI 投资情报 Agent 重构为 Windows 优先的独立桌面应用：

1. 用户双击桌面程序即可启动，无需手动打开命令行和浏览器。
2. Tauri 负责桌面壳、窗口、应用生命周期和 Python 后端进程管理。
3. Vue 负责新的 GUI 前端，替代 Jinja 模板作为主要交互界面。
4. Python FastAPI 后端保留为本地服务，继续使用现有抓取、LLM、汇总、备份和数据访问逻辑。
5. 现有 SQLite 主库、密钥、备份、归档文件保持原路径与原结构，默认不迁移、不清空、不重建。

### 2.2 不做目标

P0 阶段明确不做：

- 不重写抓取算法。
- 不更换数据库。
- 不迁移 PostgreSQL。
- 不新增账号、权限、团队协作。
- 不改造为云端 SaaS。
- 不修改现有数据表结构，除非为 JSON API 增加只读查询需要且经过备份。
- 不重新设计 LLM Prompt 策略。
- 不引入新的信息源范围。
- 不做投资评分、投资建议、memo 生成。

---

## 3. 现状盘点

### 3.1 后端入口

当前后端入口为 `app.py`：

- FastAPI 应用名：AI 投资情报 Agent。
- 静态资源目录：`static/`。
- 模板目录：`templates/`。
- 启动参数：
  - `--host`，默认 `127.0.0.1`。
  - `--port`，默认 `8011`。
  - `--no-open-browser`，当前参数已存在，但后端未主动打开浏览器。
- 已有 JSON API：
  - `POST /api/crawl/run`
  - `GET /api/crawl/status`
  - `GET /api/app-info`
- 大部分业务交互仍是 HTML 页面和表单 POST。

### 3.2 现有页面

当前模板页面对应的功能如下：

| 页面 | 路径 | 功能 |
|---|---|---|
| 今日概览 | `/` | 今日新增、累计内容、启用源、失败源、按信息源展示今日内容、手动抓取进度 |
| 融资新闻 | `/financing` | 按融资标签和关键词聚合，做轻量事件去重 |
| 信息源管理 | `/sources` | 新增、编辑、启停、删除或禁用、单源测试抓取 |
| 内容库 | `/content` | 搜索、按来源筛选、状态筛选、收藏筛选 |
| 内容详情 | `/content/{id}` | 编辑标题和 summary、收藏、归档、加标签、加实体、重抓来源 |
| 每日汇总 | `/summaries` | 列表、手动生成、查看详情、导出 Markdown |
| 标签与实体 | `/taxonomy` | 标签新增启停、实体编辑 |
| LLM 与 Prompt | `/llm` | LLM 配置、连接测试、Prompt 保存、任务绑定、待处理内容补处理 |
| 系统设置 | `/settings` | 抓取时间、缓存小时数、并发数、LLM 汇总开关、备份、代理信息 |

### 3.3 核心后端服务

| 模块 | 现有职责 | 重构策略 |
|---|---|---|
| `CrawlService` | 全量抓取、单源抓取、去重、AI 相关过滤、保存内容、补处理 | 保留，不重写 |
| `LLMService` | LLM 配置读取、单条内容元数据处理、连接测试 | 保留，不重写 |
| `DailySummaryService` | 按日期生成结构化汇总和 Markdown | 保留，不重写 |
| `BackupService` | 复制 SQLite 主库并做 integrity check | 保留，不重写 |
| `AppScheduler` | APScheduler 每日抓取和启动补抓 | 保留，不重写 |
| `query_content` | 内容库搜索与筛选 | 保留，可作为 API 查询层复用 |
| `crawl_progress` | 进度状态内存快照 | 保留，供 Vue 轮询 |

### 3.4 数据现状

当前主库为：

```text
data/ai_market_daily_main.sqlite3
```

本次反查结果：

- 主库大小约 6 MB。
- `PRAGMA integrity_check = ok`。
- 实际 journal mode 为 `delete`。
- 现有业务表 17 张。
- 当前记录量：

| 表 | 记录数 |
|---|---:|
| `sources` | 29 |
| `content_items` | 1370 |
| `content_cache` | 132 |
| `content_tags` | 8335 |
| `content_entities` | 7903 |
| `entities` | 4267 |
| `daily_summaries` | 15 |
| `crawl_runs` | 524 |
| `crawl_errors` | 103 |
| `llm_configs` | 1 |
| `prompts` | 11 |
| `llm_tasks` | 10 |
| `llm_logs` | 1605 |
| `system_settings` | 9 |
| `backups` | 3 |
| `long_term_logs` | 59 |
| `session_logs` | 56 |

必须保留的本地数据目录：

```text
data/
backups/
archives/
```

必须保留的敏感文件：

```text
data/secret.key
```

`data/secret.key` 用于解密 LLM 配置中的 API Key、Base URL、Model Name。应用打包和升级不得覆盖该文件。

---

## 4. 目标架构

### 4.1 总体架构

```text
Tauri Desktop App
  |
  |-- Vue 3 SPA
  |     |-- Vue Router
  |     |-- Pinia 状态管理
  |     |-- API Client
  |
  |-- Tauri Rust Layer
  |     |-- 启动 Python sidecar
  |     |-- 探测端口和健康检查
  |     |-- 管理退出和异常恢复
  |     |-- 打开外部链接
  |
  |-- Python FastAPI Backend
        |-- 现有 app.py
        |-- 新增薄 JSON API router
        |-- 复用 ai_agent.services
        |-- 复用 ai_agent.models
        |-- 复用 SQLite 主库
```

### 4.2 推荐实现方式

P0 推荐使用 Python sidecar 方式：

1. 使用 PyInstaller 或同等方式把 Python 后端打成可执行 sidecar。
2. Tauri 启动时先探测已有后端实例。
3. 如果没有可用实例，Tauri 选择可用端口启动 sidecar。
4. Vue SPA 通过 `http://127.0.0.1:{port}/api/v1/...` 调用后端。
5. 退出桌面应用时，Tauri 调用 `/shutdown` 或直接终止自己启动的 sidecar 进程。

选择 sidecar 的原因：

- 对现有 Python 后端侵入最小。
- 保留 FastAPI、APScheduler、SQLAlchemy、requests、BeautifulSoup 等现有依赖。
- 不需要把 Python 逻辑改写为 Rust。
- 有利于后续继续独立调试后端。

### 4.3 兼容策略

保留现有 HTML 页面作为兼容入口，至少在 P0 开发期保留：

- Vue GUI 是主界面。
- Jinja 模板页面保留为调试和回退路径。
- 后端现有 form POST 路由不删除。
- 新增 API router 只作为薄适配层，不搬运业务逻辑。

---

## 5. 数据保护要求

### 5.1 硬性原则

1. 不删除 `data/`、`backups/`、`archives/`。
2. 不覆盖 `data/ai_market_daily_main.sqlite3`。
3. 不覆盖 `data/secret.key`。
4. 不在首次启动时清空或重建数据库。
5. 不改变 `content_items.canonical_url` 去重语义。
6. 不改变 LLM 加密字段读取方式。
7. 不改变备份记录与实际备份文件路径语义。
8. 应用升级不得把用户数据目录当成静态资源覆盖。

### 5.2 启动前保护

桌面应用首次启动时应执行：

1. 检查主库是否存在。
2. 检查 `secret.key` 是否存在。
3. 调用后端 `/api/app-info` 或未来 `/api/v1/health` 验证服务。
4. 如需要执行任何 schema migration，必须先自动创建一次备份。
5. P0 默认不做 schema migration。

### 5.3 数据路径策略

P0 可以继续使用项目根目录下的相对路径：

```text
data/ai_market_daily_main.sqlite3
data/secret.key
backups/
archives/
```

正式安装包可在 P1 支持用户数据目录，但必须提供迁移向导，并且不得静默搬迁数据。

---

## 6. 功能需求

### 6.1 应用启动与生命周期

用户故事：

作为用户，我希望双击桌面应用即可打开 AI 投资情报 Agent，无需手动启动 Python 和浏览器。

需求：

1. Tauri 启动后自动探测 8011 到 8020 是否已有本应用后端。
2. 如果已有后端，则直接连接。
3. 如果没有后端，则启动 Python sidecar。
4. 启动过程中显示加载状态。
5. 后端启动失败时显示错误详情和本地日志入口。
6. 关闭窗口时可选择：
   - 只关闭窗口，后端继续运行。
   - 退出应用并关闭后端。
7. 默认行为建议为退出应用并关闭由当前 Tauri 实例启动的后端。

验收标准：

- 双击应用后 10 秒内进入主界面。
- 端口被占用时能自动换端口。
- 后端不可用时 UI 不白屏。
- 退出后不会遗留多个无主 Python 进程。

### 6.2 今日概览

目标：

用 Vue 重建当前 `/` 页面能力。

需求：

1. 展示今日新增内容数。
2. 展示累计内容数。
3. 展示启用信息源数。
4. 展示有失败记录的信息源数。
5. 展示每日自动抓取时间。
6. 展示最近自动抓取日期。
7. 展示最近汇总日期和条数。
8. 按信息源展示今日内容。
9. 支持每个信息源分页。
10. 支持手动抓取。
11. 抓取时每秒轮询进度。
12. 抓取完成后刷新首页数据。

验收标准：

- 数据与现有首页一致。
- 手动抓取不会重复启动并发任务。
- 进度条显示 completed、total、new_items、failed_items。

### 6.3 融资新闻

目标：

重建当前 `/financing` 页面。

需求：

1. 展示今日融资信息。
2. 如果今日无融资信息，展示最新融资信息。
3. 展示去重后事件数。
4. 展示今日原始命中数。
5. 展示累计原始命中数。
6. 每个事件展示标题、summary、来源、时间、LLM 状态、原文链接。
7. 对相关报道进行折叠展示。

验收标准：

- 复用现有 `query_financing_items` 和 `dedupe_financing_items`。
- 不改变去重阈值和规则。

### 6.4 信息源管理

目标：

重建当前 `/sources` 页面。

需求：

1. 信息源列表。
2. 新增信息源。
3. 编辑信息源。
4. 启用或停用。
5. 删除无历史内容的信息源。
6. 对已有历史内容的信息源，删除动作降级为停用。
7. 单源测试抓取。
8. 展示 source category、access method、priority、risk、limit、timeout、error_count、last_success_at。

验收标准：

- 操作结果与现有后端一致。
- 含历史内容的信息源不会被物理删除。

### 6.5 内容库

目标：

重建当前 `/content` 和 `/content/{id}`。

列表需求：

1. 按标题、summary、来源、标签、实体搜索。
2. 按来源筛选。
3. 按 extraction_status 筛选。
4. 按收藏筛选。
5. 展示标题、来源、发布时间或抓取时间、summary、标签、LLM 状态。
6. 支持进入详情页。
7. 支持列表快速收藏或取消收藏。

详情需求：

1. 展示标题、来源、URL、summary、状态、标签、实体。
2. 支持编辑标题、summary、extraction_status。
3. 支持收藏或取消收藏。
4. 支持手动归档原文。
5. 支持添加标签。
6. 支持添加实体。
7. 支持重新抓取来源。
8. 如存在 `content_cache`，展示 clean_text 和 expire_at。

验收标准：

- 搜索结果与现有 `query_content` 一致。
- 手动归档写入 `archives/`，并更新 `full_content_saved` 和 `archive_object_path`。

### 6.6 每日汇总

目标：

重建当前 `/summaries`、`/summaries/{date}`、Markdown 导出。

需求：

1. 汇总列表。
2. 手动生成今日汇总。
3. 汇总详情。
4. 展示 total、successful、partial、failed。
5. 展示 LLM 汇总状态。
6. 展示 markdown_text。
7. 支持导出或复制 Markdown。

验收标准：

- 复用现有 `DailySummaryService.generate`。
- 不改变每日汇总按当天入库内容生成的逻辑。

### 6.7 标签与实体

目标：

重建当前 `/taxonomy`。

需求：

1. 标签列表。
2. 新增标签。
3. 启停标签。
4. 实体列表，默认限制 300 条或支持分页。
5. 编辑实体 type、canonical_name、aliases。

验收标准：

- 保留 `tag_definitions` 和 `entities` 当前字段语义。
- 不自动合并实体。

### 6.8 LLM 与 Prompt

目标：

重建当前 `/llm`。

需求：

1. 展示 LLM 配置列表。
2. 新增 LLM 配置。
3. API Key、Base URL、Model Name 继续加密保存。
4. 支持 OpenAI-compatible 和 Anthropic provider。
5. 测试连接。
6. 展示最近 5 条连接测试日志。
7. 展示 LLM 状态统计。
8. 支持重新处理 not_configured 或 pending 内容。
9. 新增 Prompt。
10. 展示任务绑定。
11. 批量保存任务与 LLM 配置、Prompt 的绑定关系。

验收标准：

- 不在前端保存 API Key 明文。
- API Key 只通过后端加密写入。
- 前端展示 masked key。
- 连接测试写入 `llm_logs`。

### 6.9 系统设置与备份

目标：

重建当前 `/settings`。

需求：

1. 编辑每日抓取时间。
2. 编辑 clean_text 缓存小时数。
3. 编辑每日汇总最大 item 数。
4. 编辑信息源并发数。
5. 编辑 LLM 并发数。
6. 开关 LLM 每日汇总文本。
7. 展示当前代理信息。
8. 展示最近备份。
9. 手动创建备份。
10. 创建备份后展示 integrity check 结果。

验收标准：

- 保存设置后刷新 APScheduler。
- 备份文件写入 `backups/`。
- 备份记录写入 `backups` 表。

---

## 7. API 需求

### 7.1 API 设计原则

1. 新增 `/api/v1` 命名空间。
2. API router 只做参数解析、序列化、调用现有 service。
3. 不把业务逻辑从 `ai_agent.services` 复制到 API 层。
4. 保留现有 HTML 路由。
5. Vue 只调用 JSON API，不提交 HTML form。
6. 所有写操作返回 `{ "ok": true, ... }` 或 `{ "ok": false, "message": "..." }`。
7. 错误响应包含明确 message，便于桌面端展示。

### 7.2 建议 API 清单

#### 健康与应用信息

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/health` | 后端健康检查、版本、端口、数据路径 |
| GET | `/api/v1/app-info` | 应用 ID、名称、后端版本 |

#### 首页与抓取

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/dashboard` | 首页统计、最近汇总、按源内容 |
| POST | `/api/v1/crawl/run` | 启动全量抓取 |
| GET | `/api/v1/crawl/status` | 获取抓取进度 |

#### 融资新闻

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/financing` | 获取融资事件聚合结果 |

#### 信息源

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/sources` | 信息源列表 |
| POST | `/api/v1/sources` | 新增信息源 |
| GET | `/api/v1/sources/{id}` | 信息源详情 |
| PATCH | `/api/v1/sources/{id}` | 编辑信息源 |
| POST | `/api/v1/sources/{id}/toggle` | 启停信息源 |
| DELETE | `/api/v1/sources/{id}` | 删除或停用信息源 |
| POST | `/api/v1/sources/{id}/crawl` | 单源抓取 |

#### 内容库

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/content` | 搜索和筛选内容 |
| GET | `/api/v1/content/{id}` | 内容详情 |
| PATCH | `/api/v1/content/{id}` | 编辑内容 |
| POST | `/api/v1/content/{id}/favorite` | 收藏或取消收藏 |
| POST | `/api/v1/content/{id}/archive` | 手动归档 |
| POST | `/api/v1/content/{id}/tags` | 添加标签 |
| POST | `/api/v1/content/{id}/entities` | 添加实体 |
| POST | `/api/v1/content/{id}/recrawl` | 重抓来源 |

#### 每日汇总

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/summaries` | 汇总列表 |
| POST | `/api/v1/summaries/generate` | 生成今日汇总 |
| GET | `/api/v1/summaries/{date}` | 汇总详情 |
| GET | `/api/v1/summaries/{date}/markdown` | Markdown 文本 |

#### 标签与实体

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/taxonomy` | 标签和实体列表 |
| POST | `/api/v1/taxonomy/tags` | 新增标签 |
| POST | `/api/v1/taxonomy/tags/{id}/toggle` | 启停标签 |
| PATCH | `/api/v1/taxonomy/entities/{id}` | 编辑实体 |

#### LLM 与 Prompt

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/llm` | LLM 页面聚合数据 |
| POST | `/api/v1/llm/configs` | 新增 LLM 配置 |
| POST | `/api/v1/llm/configs/{id}/test` | 测试连接 |
| POST | `/api/v1/llm/prompts` | 新增 Prompt |
| POST | `/api/v1/llm/reprocess-not-configured` | 补处理内容 |
| PATCH | `/api/v1/llm/tasks/bulk` | 批量保存任务绑定 |

#### 设置与备份

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/v1/settings` | 读取设置、备份、代理信息 |
| PATCH | `/api/v1/settings` | 保存设置 |
| POST | `/api/v1/settings/backup` | 创建手动备份 |

---

## 8. 前端需求

### 8.1 技术栈

推荐：

- Vue 3
- TypeScript
- Vite
- Vue Router
- Pinia
- Axios 或 Fetch wrapper
- TanStack Table 或轻量表格组件
- ECharts 可作为 P1 数据可视化增强，不作为 P0 必需

UI 组件库建议二选一：

| 方案 | 优点 | 约束 |
|---|---|---|
| Naive UI | 桌面工具感强，表格、表单、弹窗成熟 | 需要统一主题 |
| Element Plus | 中文后台应用常用，表单和表格稳定 | 风格较传统 |

P0 建议选择 Element Plus 或 Naive UI，不自研基础组件。

### 8.2 页面路由

| Vue Route | 页面 |
|---|---|
| `/` | 今日概览 |
| `/financing` | 融资新闻 |
| `/sources` | 信息源管理 |
| `/content` | 内容库 |
| `/content/:id` | 内容详情 |
| `/summaries` | 每日汇总 |
| `/summaries/:date` | 汇总详情 |
| `/taxonomy` | 标签与实体 |
| `/llm` | LLM 与 Prompt |
| `/settings` | 系统设置 |

### 8.3 UI 原则

1. 采用桌面工具布局，而不是营销页布局。
2. 保留左侧导航，主区域用于高密度信息展示。
3. 表格、筛选、状态标签、进度条、抽屉和弹窗优先。
4. 所有写操作需要明确 loading、成功、失败状态。
5. 抓取任务、LLM 测试、备份属于长耗时操作，必须有状态反馈。
6. 外部原文链接通过 Tauri 打开系统浏览器，不在桌面应用内嵌外站。
7. 中文文案统一由 Vue 前端管理，避免依赖后端模板字符串。

### 8.4 编码要求

代码阅读中，部分 Python 或模板字符串在当前环境输出中出现中文编码异常。重构时需要：

1. 确认所有源文件使用 UTF-8。
2. Vue 前端文案统一使用 UTF-8。
3. API 响应声明 `charset=utf-8`。
4. 对已有数据库内容不做批量编码修复，除非单独备份并确认是数据层问题。

---

## 9. Tauri 桌面端需求

### 9.1 窗口

需求：

1. 默认窗口尺寸建议 1280 x 820。
2. 最小窗口尺寸建议 1024 x 700。
3. 支持最大化和恢复。
4. 标题为 AI 投资情报 Agent。
5. P0 可不做系统托盘，P1 再支持后台常驻。

### 9.2 Python sidecar 管理

需求：

1. Tauri 启动时检查后端是否已运行。
2. 如未运行，启动 sidecar。
3. 记录 sidecar PID。
4. 轮询健康检查直到 ready。
5. 后端异常退出时，前端展示服务中断页。
6. 用户可点击重启后端。
7. Tauri 退出时只关闭自己启动的后端，不关闭用户手动启动的同类服务。

### 9.3 端口策略

沿用现有启动脚本思路：

1. 优先使用 8011。
2. 如被占用，扫描 8012 到 8020。
3. 如果端口上已有 `/api/app-info` 且 `app_id = ai-investment-agent`，复用该服务。
4. 如果端口被其他程序占用，跳过。
5. 端口选择结果写入前端运行时配置。

### 9.4 安全策略

1. 后端只绑定 `127.0.0.1`。
2. 不允许绑定公网地址。
3. Vue 只调用本机后端。
4. Tauri 不把 API Key 存到前端 localStorage。
5. 后端 CORS 仅允许 Tauri 前端来源和 localhost 调试来源。
6. 外部链接使用系统浏览器打开。

---

## 10. 后端改造需求

### 10.1 改造边界

允许改动：

- 新增 `ai_agent/api.py` 或 `ai_agent/api_v1.py`。
- 新增 Pydantic schemas。
- 在 `app.py` include 新 router。
- 新增少量 health/version API。
- 新增序列化 helper。
- 增加 CORS 配置。
- 增加启动时日志和版本信息。

不建议改动：

- `CrawlService` 主逻辑。
- `LLMService` 主逻辑。
- `DailySummaryService` 主逻辑。
- `BackupService` 主逻辑。
- SQLAlchemy model 字段。
- seed 逻辑。
- 数据库路径和密钥路径。

### 10.2 兼容性要求

1. 老的 HTML 路由继续可用。
2. 老的启动脚本继续可用。
3. `.venv\Scripts\python.exe -B app.py --port 8011` 继续可用。
4. API 层不得破坏现有 form POST 行为。
5. 后端单独运行时也应可被浏览器访问调试。

---

## 11. 打包与发布

### 11.1 P0 打包目标

Windows 桌面程序：

- 一个 Tauri 应用目录或安装包。
- 内含 Vue 静态资源。
- 内含 Python sidecar。
- 用户数据目录不被安装包覆盖。

### 11.2 Python 打包

建议：

1. 使用 PyInstaller 打包 Python 后端为 sidecar。
2. 确保包含：
   - `ai_agent/`
   - `templates/`
   - `static/`
   - 运行时依赖
3. 不把现有 `data/` 作为只读应用资源打入并覆盖用户数据。
4. 首次运行如果没有 `data/`，再初始化空库和默认 seed。
5. 如果已有 `data/`，直接使用。

### 11.3 发布包目录建议

```text
AIInvestmentAgent/
  AIInvestmentAgent.exe
  sidecars/
    ai-investment-backend.exe
  resources/
    web/
  data/
    ai_market_daily_main.sqlite3
    secret.key
  backups/
  archives/
```

P0 如继续在项目目录运行，可不迁移到该目录结构。正式安装版再落地用户数据目录策略。

---

## 12. 非功能需求

### 12.1 性能

1. 冷启动到 UI 可交互不超过 10 秒。
2. 首页数据加载不超过 2 秒。
3. 内容库默认列表不超过 300 条，P1 增加分页。
4. 抓取进度轮询间隔 1 秒。
5. 写入操作不得阻塞整个 GUI。

### 12.2 稳定性

1. 单个信息源抓取失败不影响整体抓取。
2. 后端重启后仍能读取原库。
3. GUI 刷新不导致抓取任务重复启动。
4. 应用崩溃后下次启动能连接或重启后端。
5. 备份失败必须展示错误。

### 12.3 可维护性

1. API schemas 独立管理。
2. 前端 API client 独立管理。
3. 不在 Vue 里拼后端业务规则。
4. 不在 API router 中复制复杂抓取逻辑。
5. 新旧 UI 可并存一段时间，便于回归验证。

---

## 13. 实施阶段

### Phase 0：重构准备

目标：

- 明确 API 合同。
- 确认数据保护策略。
- 增加自动备份检查。

交付：

- `/api/v1/health`
- `/api/v1/dashboard`
- `/api/v1/crawl/status`
- API schema 初稿
- 数据备份流程验证

### Phase 1：Tauri 壳与 Vue 基础

目标：

- 跑通桌面壳、Vue、后端 sidecar。

交付：

- Tauri 项目初始化。
- Vue Router 和布局。
- Python sidecar 启动和健康检查。
- 服务不可用页面。
- 端口探测。

### Phase 2：核心页面迁移

目标：

- 迁移用户日常使用的核心页面。

交付：

- 今日概览。
- 手动抓取和进度。
- 融资新闻。
- 内容库。
- 内容详情。

### Phase 3：管理页面迁移

目标：

- 迁移配置和管理能力。

交付：

- 信息源管理。
- 每日汇总。
- 标签与实体。
- LLM 与 Prompt。
- 系统设置和备份。

### Phase 4：打包与回归

目标：

- 形成可交付桌面程序。

交付：

- Windows 安装包或便携目录。
- 后端 sidecar 包。
- 数据不丢失回归测试。
- 启动、退出、崩溃恢复测试。

---

## 14. 验收标准

### 14.1 数据验收

1. 使用现有 `data/ai_market_daily_main.sqlite3` 启动成功。
2. 启动前后 `content_items` 记录数不因启动动作减少。
3. `llm_configs` 可正常解密和展示 masked key。
4. 手动备份能生成新备份文件。
5. `PRAGMA integrity_check` 仍为 `ok`。

### 14.2 功能验收

1. 桌面应用可打开今日概览。
2. 手动抓取可启动并显示进度。
3. 内容库搜索可用。
4. 内容详情编辑可保存。
5. 收藏、归档、标签、实体操作可用。
6. 每日汇总可生成并查看 Markdown。
7. LLM 配置可新增并测试。
8. 系统设置可保存并刷新调度。

### 14.3 兼容验收

1. 现有 `app.py` 仍可单独运行。
2. 现有 Jinja 页面仍可访问。
3. 现有启动脚本不被破坏。
4. Tauri 关闭后不会损坏 SQLite 主库。

---

## 15. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Python sidecar 打包遗漏依赖 | 桌面版启动失败 | 建立打包 smoke test，启动后调用 health、dashboard、settings |
| SQLite 并发写入冲突 | 抓取时写入失败 | 保留现有低并发设置，避免前端发起重复抓取 |
| 数据路径被安装包覆盖 | 用户历史数据丢失 | data/backups/archives 作为用户数据处理，不作为静态资源覆盖 |
| API 层复制业务逻辑 | 后端维护成本升高 | API 只调用现有 service |
| 端口冲突 | 应用无法启动 | 沿用 8011 到 8020 扫描和 app_id 校验 |
| 中文编码异常 | UI 文案乱码 | Vue 统一前端文案，后端 API 统一 UTF-8 |
| 退出时误杀用户进程 | 用户手动运行的后端被关闭 | Tauri 只关闭自己启动并记录 PID 的 sidecar |
| LLM Key 泄漏 | 敏感信息暴露 | 前端不保存明文，后端继续 Fernet 加密 |

---

## 16. 待确认问题

1. 桌面程序是否只需要 Windows，还是后续要支持 macOS。
2. 正式安装版是否要把用户数据迁移到系统用户目录，还是继续放在应用目录。
3. 是否需要系统托盘后台常驻。
4. 关闭窗口时默认退出后端，还是默认后台继续运行。
5. Vue UI 组件库选择 Element Plus 还是 Naive UI。
6. 是否要在 P0 中保留 Jinja 页面入口，还是只作为开发调试保留。
7. 是否允许在 P1 增加内容库分页和索引优化。

---

## 17. P0 结论

本项目适合采用“桌面壳 + Vue 前端 + Python sidecar 后端”的低侵入重构方式。现有后端业务能力集中在 `ai_agent.services`，现有数据模型清晰，主库完整性检查通过，因此 P0 不应重写后端和迁移数据库。

最稳妥的路径是：

1. 保留现有 Python 后端。
2. 新增薄 JSON API。
3. 用 Tauri 管理 Python sidecar。
4. 用 Vue 重建现有页面。
5. 保留 `data/`、`backups/`、`archives/` 原样。
6. 完成桌面打包后，再考虑数据目录迁移、分页索引、系统托盘等 P1 能力。
