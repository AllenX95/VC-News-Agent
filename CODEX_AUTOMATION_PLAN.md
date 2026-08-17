# VC News Agent × Codex 定时任务规划方案

> 状态：规划稿 v1.0
> 日期：2026-08-17
> 适用项目：`D:\claude-projects\VC-news`

## 1. 方案结论

保留现有 GUI/WebUI 的全部能力，并新增一个无界面执行入口。GUI 与 Headless CLI 共用同一套数据库、配置和业务服务；Codex Automation 只承担定时唤醒、运行监督、结果验收与成品交付，不重新实现抓取和投资情报处理逻辑。

最终形态：

```text
                         GUI / WebUI
                 配置管理 · 手动执行 · 人工复核
                              │
                              ▼
                    SQLite + 加密配置
                              │
               ┌──────────────┴──────────────┐
               │                             │
          GUI / API                    Headless CLI
          人工触发                       自动触发
               │                             │
               └──────────────┬──────────────┘
                              ▼
                    Shared Service Layer
              抓取 · 清洗 · 去重 · LLM · 报告
                              │
                              ▼
                 report_data.json（稳定契约）
                              │
                              ▼
                 Jinja2 HTML 模板（版本化）
                              │
                              ▼
                       daily.html
                              ▲
                              │
                    Codex Automation
              定时 · preflight · 执行 · QA · 交付
```

核心原则：

1. GUI 保留配置控制和手动执行入口。
2. Headless 不维护第二套 provider、prompt 或 source 配置。
3. Codex 不进入应用内部逐步操作，不直接重做新闻研究。
4. HTML 使用固定骨架和动态内容，不由 Codex 每日自由生成整页代码。
5. 调度只能有一个主责方，避免 Codex 与 APScheduler 重复执行。

## 2. 目标与非目标

### 2.1 目标

- 每天由 Codex 在指定时间自动生成一份 HTML 投资情报日报。
- 日报固定包含“技术进展、产业新闻、融资新闻”三个一级类目。
- LLM provider、模型、API Key、Prompt、信息源和并发设置继续通过 GUI 管理。
- GUI 的“立即抓取、生成摘要、生成报告”等人工入口继续可用。
- 自动任务不依赖启动 Tauri、浏览器或 FastAPI 服务。
- 每次运行都有可机器检查的状态、产物、日志和退出码。
- 重复触发不会造成重复抓取、重复日报或并发写库冲突。

### 2.2 非目标

- 不让 Codex 替代现有 `CrawlService`、`LLMService` 和报告服务。
- 不在第一阶段迁移到云端常驻运行环境。
- 不在第一阶段重构全部数据库模型或前端页面。
- 不让定时任务每次创建新 worktree 后访问一份空的 runtime data。
- 不为 Headless CLI 再设计一套 `--api-key`、`--provider`、`--model` 参数。

## 3. 当前代码基础与差距

### 3.1 已具备的基础

当前项目已经有较完整的共享业务层：

- `CrawlService.run_all_sources()` 可执行全量信息源抓取。
- `DailySummaryService.generate()` 可生成结构化 section 快照和 Markdown。
- `LLMConfig`、`LLMTask`、`Prompt` 已保存在 SQLite 中。
- LLM 配置包含 provider、base URL、model、API Key、超时、重试和上下文窗口。
- OpenAI-compatible provider 已归一化为 OpenAI 协议，可继续承载第三方兼容服务。
- `ReportWorkspace` 已支持报告输入、版本、人工修订和导出。
- `jinja2` 已在项目依赖中，无需引入新的模板引擎。
- GUI 已具备数据源、LLM、设置、内容库、融资事件和报告工作区页面。

### 3.2 主要差距

1. 还没有面向无人值守运行的同步 Headless CLI。
2. 当前 `app.py` 启动时总会启动 APScheduler，并可能执行启动补抓。
3. 当前日报按信息源类别分组，不是目标中的三大投资情报类目。
4. 当前主要产物是 Markdown，缺少稳定的 `report_data.json → HTML` 渲染链路。
5. 缺少一次运行的 manifest、稳定退出码、锁和幂等语义。
6. runtime data 与代码目录仍耦合，worktree 无法天然读取 DB、密钥和历史产物。

## 4. 职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| GUI/WebUI | 配置、人工触发、进度查看、人工复核、报告编辑 | 自动调度主责 |
| Shared Services | 抓取、清洗、去重、LLM enrichment、分类、报告数据生成 | 命令行解析、Codex 通知 |
| Headless CLI | 初始化、读取配置、编排共享服务、写产物和 manifest | 保存另一套业务配置 |
| Jinja 模板 | 固定页面结构、样式、字段展示和空状态 | 新闻判断和自由文本推理 |
| Codex Automation | 定时唤醒、preflight、启动 CLI、检查 manifest、交付结果 | 逐条抓取、直接写数据库、每日自由写 HTML |

“Codex 输出 HTML”在本方案中的准确含义是：Codex 将最终 HTML 作为任务成品交付给用户；HTML 本身由可测试的模板渲染器生成。

## 5. 自动日报工作流

```text
Codex 定时唤醒
  ↓
检查项目目录、Python、数据库、密钥、网络前置条件
  ↓
执行 python -m ai_agent.headless daily
  ↓
获取单实例运行锁
  ↓
create_db + migration + seed + 应用 GUI 保存的代理配置
  ↓
读取 GUI 保存的 source / LLM / prompt / concurrency 配置
  ↓
CrawlService.run_all_sources()
  ↓
生成/更新融资事件与三大类目报告数据
  ↓
校验 report_data.json
  ↓
Jinja2 渲染 daily.html
  ↓
写 run_manifest.json，释放锁，返回退出码
  ↓
Codex 检查 manifest、HTML 和关键统计
  ↓
成功：交付 HTML；部分成功：交付 HTML + 告警；失败：报告根因和日志位置
```

## 6. Headless CLI 设计

建议新增：

```text
ai_agent/
  headless.py
  orchestration.py
  report_data.py
  html_renderer.py
  schemas/
    report_data.schema.json
    run_manifest.schema.json
  templates/
    daily_v1.html.j2
```

建议命令：

```powershell
.\.venv\Scripts\python.exe -m ai_agent.headless daily
.\.venv\Scripts\python.exe -m ai_agent.headless crawl
.\.venv\Scripts\python.exe -m ai_agent.headless summary --date 2026-08-17
.\.venv\Scripts\python.exe -m ai_agent.headless render --date 2026-08-17
.\.venv\Scripts\python.exe -m ai_agent.headless health
```

第一阶段对 Codex 只暴露 `daily` 和 `health`，其他子命令主要用于开发、补跑和排障。

`daily` 应是同步命令：只有整个流水线完成后才退出，不启动长期后台线程，不依赖 FastAPI。

### 6.1 参数边界

允许的运行控制参数：

- `--date`：补跑指定日期。
- `--force`：明确覆盖当日已成功状态，仅供人工补跑。
- `--skip-crawl`：只重新生成报告。
- `--output-dir`：测试或一次性导出覆盖。

不允许通过 CLI 传入：

- provider；
- model；
- API Key；
- prompt；
- source 列表；
- 业务分类规则。

这些必须继续从 GUI 共用的数据库配置读取。

## 7. 三大类目与结构化报告契约

当前 `DailySummaryService` 按来源类型分 section。新日报需要新增一层“投资情报分类”，不要直接把 source category 等同于最终版面类目。

固定一级类目：

1. `technology`：技术进展；
2. `industry`：产业新闻；
3. `funding`：融资新闻。

可选动态二级主题由规则和 LLM 根据当日内容生成，例如基础模型、Agent、AI Infra、具身智能、芯片、企业服务等。一级类目必须稳定，二级主题允许动态变化。

建议的 `report_data.json`：

```json
{
  "schema_version": "1.0",
  "template_version": "daily-v1",
  "report_date": "2026-08-17",
  "generated_at": "2026-08-17T10:26:18+08:00",
  "headline": "AI VC Daily",
  "executive_summary": "今日最值得关注的三条变化……",
  "stats": {
    "sources_attempted": 28,
    "sources_succeeded": 25,
    "raw_items": 186,
    "included_items": 24,
    "failed_sources": 3
  },
  "sections": [
    {
      "key": "technology",
      "title": "技术进展",
      "groups": [
        {
          "title": "基础模型",
          "items": [
            {
              "content_id": 123,
              "title": "示例标题",
              "summary": "事实摘要与投资含义。",
              "why_it_matters": "对行业或投资判断的影响。",
              "source": "示例来源",
              "url": "https://example.com/news",
              "published_at": "2026-08-17T07:20:00+08:00",
              "tags": ["基础模型"],
              "confidence": 0.91
            }
          ]
        }
      ]
    }
  ],
  "warnings": []
}
```

约束：

- URL 必须来自原始数据，不允许模型编造。
- 每条 item 必须可追溯到 `content_id` 或融资事件 `event_id`。
- 同一事件的多篇报道应合并，保留主要来源和相关来源。
- 空类目保留 section，但显示明确空状态，不虚构内容补齐版面。
- 所有模型输出必须经过 JSON 解析、schema 校验和字段清洗后才能渲染。

### 7.1 内容生成职责

推荐由现有应用的 `LLMTask` 体系新增任务，例如：

```text
generate_daily_investment_report
```

它使用 GUI 绑定的 LLMConfig 与 Prompt 生成结构化内容。这样后续切换 OpenAI、Anthropic 或 OpenAI-compatible 服务时，无需修改 Headless CLI 或 Codex Automation。

当 LLM 不可用时，允许按规则输出降级版 HTML，但 manifest 必须标记 `partial`，页面也要显示“智能摘要不可用，当前为结构化数据降级版”。

降级状态机必须固定：模型原始输出非法时先执行有限次数的解析修复/重试；仍失败则完全丢弃该原始输出，由确定性规则构造一份符合 schema 的 report data，并标记 `partial`。如果规则降级后的最终内部数据仍无法通过 schema 校验，则返回退出码 30，且不渲染 HTML。模型原始输出不得绕过 schema 直接进入模板。

## 8. HTML 输出策略

采用“固定页面骨架 + 动态内容”的 Jinja2 模板。

固定部分：

- 页面标题、日期和整体视觉体系；
- 三个一级类目的顺序；
- 新闻卡片字段和来源链接；
- 运行统计、失败提示、页脚和打印样式；
- 响应式布局、长文本折行和空状态。

动态部分：

- executive summary；
- 每个类目的二级主题；
- 新闻数量、排序和卡片内容；
- 当日异常与数据质量提示。

首版建议输出为单文件 HTML，CSS 内联、无外部 JS 依赖，使其可以离线打开、归档和转发。模板需要显式版本号，例如 `daily-v1`；结构或视觉大改时升级版本，不直接破坏历史报告。

推荐产物目录：

```text
runs/
  2026-08-17/
    latest.json
    20260817T100000+0800-4f82c9/
      run_manifest.json
      report_data.json
      daily.html
      daily.md
      run.log
```

每次运行使用独立的 `run_id` 目录，`--force` 不覆盖历史产物。任务完成后原子更新 `latest.json`，其中只保存当前 run_id 和 manifest 路径。Headless 同时在 stdout 最后一行输出一条 JSON，包含 `run_id`、`status` 和 manifest 的绝对路径，Codex 优先使用这条输出定位本次运行，`latest.json` 只作为恢复手段。

Markdown 作为兼容和排障产物保留，但 HTML 是主交付物。

### 8.1 日期与数据窗口

- 所有业务日期统一使用 `Asia/Shanghai`，`--date` 的含义是北京时间自然日。
- 默认 `daily` 抓取当前可用信息源，并从目标日 `00:00:00`（含）到次日 `00:00:00`（不含）的内容生成日报。
- 补跑历史日期时，默认不假设所有 source 支持历史抓取；应优先使用数据库中已存在的目标日数据。只有 source 明确支持历史窗口时才执行历史抓取。
- `--skip-crawl` 始终只使用数据库快照重新分类和渲染，不访问外部信息源。
- 内容归属优先使用可信 `publish_time`；缺失或仅为 estimated 时使用 `crawl_time`，并在 report data 中保留时间质量标识。

## 9. 运行 manifest、状态和退出码

每次运行必须写 `run_manifest.json`，即使失败也尽量留下记录。

建议字段：

```json
{
  "schema_version": "1.0",
  "run_id": "20260817T100000+0800-4f82c9",
  "command": "daily",
  "target_date": "2026-08-17",
  "status": "success",
  "started_at": "2026-08-17T10:00:00+08:00",
  "finished_at": "2026-08-17T10:26:18+08:00",
  "stages": {
    "preflight": "success",
    "crawl": "partial",
    "enrichment": "success",
    "report_data": "success",
    "html_render": "success"
  },
  "counts": {
    "sources_succeeded": 25,
    "sources_failed": 3,
    "items_created": 41,
    "items_in_report": 24
  },
  "artifacts": {
    "html": "runs/2026-08-17/daily.html",
    "report_data": "runs/2026-08-17/report_data.json",
    "markdown": "runs/2026-08-17/daily.md",
    "log": "runs/2026-08-17/run.log"
  },
  "warnings": ["3 个信息源抓取失败"],
  "error": null
}
```

建议退出码：

| 退出码 | 含义 | Codex 行为 |
|---:|---|---|
| 0 | 成功 | 交付 HTML 和摘要 |
| 1 | 当日已有成功结果，本次安全跳过 | 读取已有 manifest 并交付已有 HTML |
| 2 | 部分成功，已有可用 HTML | 交付 HTML，同时醒目标注告警 |
| 10 | preflight 失败 | 报告环境问题，不继续执行 |
| 20 | 抓取/业务流水线失败，无可用报告 | 报告根因和日志 |
| 30 | 报告数据校验失败 | 不交付错误 HTML，报告 schema 问题 |
| 40 | HTML 渲染失败 | 可交付 JSON/Markdown 作为降级产物 |
| 50 | 已有运行占锁 | 报告跳过，不启动第二份任务 |

manifest 状态除 `success`、`partial`、`failed` 外，增加 `skipped_already_success` 和 `lock_conflict`。preflight 开始前先创建最小 run 目录和初始 manifest；若 runtime 目录本身不可写，无法落盘时则向 stderr 输出单行 JSON 错误对象，字段至少包含 `status`、`exit_code`、`stage` 和 `error`。锁冲突不得改写正在运行任务的 `latest.json`。

## 10. 调度迁移方案

当前 `app.py` 每次启动都会启动内部 APScheduler，并可能执行启动补抓。如果再增加 Codex 定时任务，会出现双重调度。

建议分两步迁移：

### 阶段 A：兼容迁移

新增明确的 scheduler mode：

```text
VC_NEWS_SCHEDULER_MODE=internal | external | disabled
```

- `internal`：保留现有 APScheduler 行为，便于回滚。
- `external`：不启动 APScheduler、不执行 startup catch-up；由 Codex 调度。
- `disabled`：只保留纯手动执行。

兼容迁移期默认值为 `internal`，避免现有桌面启动行为静默改变。正式 Codex Automation 的任务环境必须显式注入 `VC_NEWS_SCHEDULER_MODE=external`；Headless CLI 本身从不启动 APScheduler，因此该变量主要约束 GUI/FastAPI 进程。GUI 设置页显示“有效模式 + 配置来源（默认值/环境变量）”，避免数据库显示值与进程实际值不一致。

GUI/API 的手动执行与此开关无关，始终保留。

### 阶段 B：稳定后收敛

Codex Automation 连续稳定运行 2 周后，将默认模式改为 `external`，并在 GUI 中明确显示“当前调度由 Codex 管理”。是否完全删除 APScheduler 可在后续版本决定；第一阶段不必为了架构纯度牺牲回滚能力。

## 11. 幂等、并发与补跑

### 11.1 单实例锁

在 runtime data 目录创建锁文件，并在锁内记录 PID、run_id、started_at。新任务发现有效锁时返回退出码 50。发现陈旧锁时，应结合 PID 存活状态和最大运行时长谨慎回收。

### 11.2 每日运行状态

以 `target_date + command` 为幂等键：

- 当日已有 `success`：默认跳过；
- 当日已有 `partial`：允许正常重试；
- 人工明确使用 `--force`：重新执行并创建新的 run_id；
- HTML 采用临时文件渲染完成后原子替换，避免留下半页文件。

默认跳过时返回退出码 1，stdout 指向已有成功运行的 manifest，不创建新的日报版本。`--force` 创建全新的 run_id 和产物目录。

### 11.3 数据库写入

第一阶段沿用当前 `journal_mode=OFF`，不要在无人值守链路中顺手切换 WAL。自动任务获取锁后立即读取并记录一份非敏感配置快照（source ID、任务绑定 ID、并发数、prompt/version ID；不记录密钥），本次运行始终使用该快照。GUI 和 Headless 不应同时执行全量抓取；GUI 需要显示“自动任务运行中”，并禁用所有可能与流水线冲突的抓取、重新处理、报告生成和配置写操作。内容收藏、备注等低风险写操作是否放行应由 SQLite 并发集成测试决定；首版可保守禁用所有写操作。

## 12. Runtime data 规划

### 12.1 第一阶段

Codex Automation 直接绑定当前本地项目目录运行，不使用临时 worktree。原因是以下内容均被 `.gitignore` 排除，不会自然出现在新 worktree：

- `.venv/`；
- `data/*.sqlite3`；
- `data/secret.key`；
- `archives/`、`backups/` 和未来的 `runs/`。

### 12.2 第二阶段

新增统一环境变量：

```text
VC_NEWS_DATA_DIR=D:\VC-News-Agent-Data
```

并由它派生：

```text
main.sqlite3
secret.key
archives/
backups/
runs/
locks/
```

保留 `VC_NEWS_DB_PATH` 作为更细粒度覆盖。完成解耦后，主 repo、Codex worktree 或未来云端执行器才可以安全共享同一 runtime 位置。

## 13. GUI 调整

GUI 不需要大改，但建议增加以下状态与入口：

- 设置页显示调度模式、下次计划时间和最近一次自动运行结果。
- Dashboard 显示最新日报状态、HTML 打开按钮、失败 source 数量。
- LLM 页面新增 `generate_daily_investment_report` 的模型和 Prompt 绑定。
- Reports 页面支持查看 HTML 主产物，同时保留 Markdown 版本与人工修订记录。
- 自动运行期间显示锁状态，阻止重复的全量任务。
- 提供“仅重新生成日报”和“补跑指定日期”，不必重复抓取全部信息源。

GUI 与 Headless 的执行入口必须最终调用同一个 orchestration/service 方法，API route 不应包含独有的 pipeline 逻辑。

## 14. Codex Automation 设计

建议每天北京时间 10:00 或 10:05 运行。若上游站点通常在整点更新，可设置为 10:05 减少边界波动。

建议任务提示词：

```text
在项目 D:\claude-projects\VC-news 中执行每日 VC 新闻自动任务。

1. 不修改源代码，不启动 GUI，不启动 FastAPI。
2. 先执行 .\.venv\Scripts\python.exe -m ai_agent.headless health。
3. health 成功后执行 .\.venv\Scripts\python.exe -m ai_agent.headless daily。
4. 读取本次 run_manifest.json，并验证：
   - status 是 success 或 partial；
   - artifacts.html 存在且非空；
   - report_data 包含 technology、industry、funding 三个一级类目；
   - 所有已展示新闻 URL 均非空；
   - report_data 中 item 总数必须等于 HTML 中 `[data-report-item]` 节点数；
   - 每个 `[data-report-item]` 的 `data-content-id` 或 `data-event-id`、链接 href 与 report_data 精确匹配。
5. 成功时将 HTML 文件作为主要结果交付，并简要报告抓取源、入选条目和失败源数量。
6. partial 时仍交付 HTML，但明确列出 warnings。
7. 失败时不要自行重做新闻研究，不要直接修改数据库；报告退出码、失败阶段、错误和日志路径。
```

自动任务运行的本机需要保持在线，Codex Desktop 需要能够访问项目目录和网络。无人值守所需的文件、进程和网络权限应在正式启用前一次性验证。

## 15. 可观测性与故障处理

每次运行至少记录：

- run_id、目标日期、开始和结束时间；
- 每个 stage 的状态和耗时；
- source 成功/失败、新增/重复 item 数；
- LLM provider/model 的非敏感标识、token、耗时和错误；
- 报告入选条数、去重数量和三个类目分布；
- 产物绝对路径和模板版本。

日志不得写入 API Key、Authorization header、解密后的 base URL 凭据或完整敏感响应。

故障策略：

- 单 source 失败：继续运行，最终状态通常为 `partial`。
- 主 LLM 失败：按现有重试策略处理；仍失败则生成规则降级版。
- schema 校验失败：保留原始模型响应到受控调试文件，不渲染为 HTML。
- HTML 渲染失败：保留 JSON/Markdown，返回专用退出码。
- 数据库或密钥不可用：preflight 直接失败，不启动抓取。

## 16. 实施阶段

### Phase 0：基线确认（0.5 天）

- 固化当前 GUI 手动抓取、LLM 配置和日报生成的 smoke test。
- 确认实际数据库路径、密钥路径、Python 路径和每日执行时间。
- 记录当前 source 数量、平均运行时间和常见失败源。

交付：基线检查清单，不改变运行方式。

### Phase 1：Headless 与运行契约（1–2 天）

- 新增 `headless.py` 和共享 orchestration 方法。
- 实现 `health`、`daily`、运行锁、manifest 和退出码。
- 增加 scheduler mode；Codex 环境使用 `external`。
- 保证 GUI/API 继续使用共享业务服务。

交付：可在 PowerShell 中同步、无人值守执行 daily，并输出 manifest。

### Phase 2：结构化日报与 HTML（2–3 天）

- 定义并校验 report data schema。
- 新增三大类目分类与去重逻辑。
- 将日级报告任务接入 `LLMTask`，由 GUI 配置 provider/model/prompt。
- 明确融资事件生成调用现有 `FinancingService`（或抽取出的共享 facade），Headless 不通过 API route 间接调用。
- 完成 `daily-v1.html.j2`、离线样式和降级版输出。
- 在 Reports/Dashboard 中增加 HTML 入口。

交付：稳定的 `report_data.json + daily.html + daily.md`。

### Phase 3：Codex Automation 上线（0.5–1 天）

- 绑定真实本地项目目录，不使用 worktree。
- 创建每日任务，完成一次手动触发和一次真实定时触发。
- 验证成功、部分成功、锁冲突、LLM 失败和断网路径。
- 保留内部 scheduler 回滚开关。

交付：Codex 每日自动交付 HTML 日报。

### Phase 4：运行稳定化（持续 2 周）

- 统计成功率、平均时长、source 失败率和 LLM 成本。
- 调整日报入选阈值、二级主题和页面信息密度。
- 两周稳定后将默认 scheduler mode 收敛到 external。
- 再决定是否实施 `VC_NEWS_DATA_DIR` 解耦和 worktree 支持。

## 17. 测试计划

### 单元测试

- CLI 参数、退出码和 manifest schema。
- scheduler mode 在 external 下不启动 APScheduler 和 startup catch-up。
- 三大类目映射、排序、去重和空类目。
- URL 追溯、HTML escaping、超长标题和缺失时间。
- Jinja 模板对 0、1、50 条新闻的渲染。
- 锁获取、锁冲突和陈旧锁处理。

### 集成测试

- 使用临时 SQLite 和 fake LLM 完成一次 `daily`。
- GUI 配置切换 provider/model 后，Headless 读取到新配置。
- LLM 返回非法 JSON 时生成受控降级结果。
- 部分 source 失败时仍生成 HTML，manifest 为 partial。
- 同日重复运行不会无意创建重复记录。

### 手工验收

- GUI 可继续完整执行抓取和报告生成。
- 不启动 GUI/FastAPI 也可运行 Headless。
- HTML 在 Chrome/Edge 离线打开正常，链接可点击，打印布局可读。
- Codex 能依据 manifest 准确区分 success、partial 和 failure。
- 自动任务与 GUI 同时触发时不会产生两份全量流水线。

## 18. 验收标准

首版上线需要同时满足：

1. 连续 5 次手动 Headless 运行均产生合法 manifest。
2. 至少完成 1 次真实 Codex 定时触发。
3. GUI 修改日级报告的 model/provider 后，下一次 Headless 无需改代码即可生效。
4. HTML 始终包含三个固定一级类目和可点击来源链接。
5. 单 source 失败不阻断整份日报，且失败会在 manifest 和页面中体现。
6. 同时触发第二次任务会被锁拒绝，不产生并发全量抓取。
7. Codex 不需要启动 GUI、FastAPI 或自行搜索新闻。
8. 历史 HTML、report data、manifest 与模板版本可追溯。

## 19. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| Codex 与 APScheduler 双触发 | 重复抓取、SQLite 冲突 | scheduler mode + 单实例锁 |
| 本机离线或 Desktop 未运行 | 错过自动任务 | GUI 补跑入口；后续再评估云端化 |
| provider 切换后输出格式变化 | JSON 校验失败 | schema、重试修复、规则降级版 |
| 每日自由生成 HTML | 样式漂移、结构不稳定 | 固定版本化 Jinja 模板 |
| worktree 缺少 DB/密钥 | 任务无法运行 | 第一阶段绑定真实目录；后续数据目录解耦 |
| GUI 和 Headless 逻辑分叉 | 结果不一致、维护成本上升 | 共享 orchestration/service 层 |
| 模型编造来源 | 投资情报不可追溯 | URL 和 ID 必须来自数据库，渲染前校验 |
| 抓取部分失败 | 日报不完整 | partial 状态、来源告警、允许补跑 |

## 20. 推荐的首个开发切片

首个切片只做最小闭环：

1. `python -m ai_agent.headless health`；
2. `python -m ai_agent.headless daily` 调用现有 CrawlService 和 DailySummaryService；
3. 写 `run_manifest.json + daily.md + exit code`；
4. 增加 `VC_NEWS_SCHEDULER_MODE=external`；
5. 用现有 Markdown 数据通过一个最简 Jinja 模板生成 HTML。

这个切片先证明“GUI 与 Headless 共存、Codex 可稳定调度、产物可验收”。三大类目的 LLM 结构化组织和精细视觉模板放到第二个切片，能够显著降低一次性改动风险。

## 21. 最终决策摘要

- **执行入口**：GUI + Headless 双入口，业务服务单实现。
- **配置权**：继续归 GUI/SQLite，Headless 和 Codex 不复制配置。
- **Provider 切换**：通过现有 `LLMConfig + LLMTask + Prompt` 生效。
- **调度权**：目标状态由 Codex 统一调度，内部 scheduler 先保留回滚开关。
- **报告组织**：固定三大类目，动态二级主题。
- **HTML 生成**：结构化 JSON + 版本化 Jinja，不让 Codex每天自由写整页。
- **Codex 角色**：scheduler、supervisor、QA 和交付层。
- **第一阶段运行位置**：真实本地项目目录，不使用 worktree。
- **运行契约**：manifest + report data + HTML/Markdown + exit code。
