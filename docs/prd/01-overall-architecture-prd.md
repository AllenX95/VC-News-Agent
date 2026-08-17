# PRD 01：VC News Agent × Codex 整体架构设计

> 状态：Ready for implementation
> 版本：1.0
> 日期：2026-08-17
> 关联文档：Codex 侧方案 PRD、VC-news-agent 侧改动建议 PRD

## Problem Statement

VC News Agent 已经具备 GUI/WebUI、信息源抓取、内容处理、LLM 配置、融资事件和报告工作区，但当前自动运行依赖应用内部调度器，主要产物仍是 Markdown，也缺少一个适合 Codex 无人值守调用的稳定执行契约。

用户希望每天由 Codex 自动产出并交付 HTML 投资情报日报，同时继续在 GUI 中管理信息源、LLM provider、模型、API Key、Prompt、并发参数，并保留人工抓取、补跑、重新生成和复核入口。如果让 Codex 自行抓新闻、生成自由格式 HTML，或为 Headless 入口再建立一套配置，会造成业务逻辑分叉、日报样式漂移、配置不一致和难以排障。若 Codex 定时任务与当前 APScheduler 同时启用，还会产生重复抓取和 SQLite 并发风险。

需要建立一个职责清晰、可追溯、可降级和可逐步迁移的整体架构，使 GUI 人工流程与 Codex 自动流程共享同一业务内核，并通过稳定的机器契约完成协作。

## Solution

采用“共享业务内核、双执行入口、单一调度主责、结构化产物契约”的架构：

- GUI/WebUI 继续负责业务配置、人工执行、状态查看和人工复核。
- VC News Agent 新增同步 Headless 入口，但不新增 provider、模型、Prompt 或信息源配置体系。
- GUI/API 与 Headless 调用同一个高层 orchestration seam，内部复用抓取、清洗、去重、LLM、融资事件和报告服务。
- Codex Automation 负责定时唤醒、运行前检查、调用 Headless、验证产物并向用户交付结果，不重新执行领域研究。
- VC News Agent 输出版本化 `run_manifest`、`report_data`、HTML、Markdown 和日志；Codex 只依据这些契约判断成功、部分成功、跳过或失败。
- HTML 由结构化报告数据和版本化 Jinja 模板确定性渲染，固定“技术进展、产业新闻、融资新闻”三个一级类目，允许动态二级主题。
- 兼容迁移期保留内部调度回滚能力，但正式运行时 Codex 是唯一调度主责；单实例锁防止 GUI 与自动任务重复执行。
- 第一阶段直接在真实本地项目目录运行；后续再将 runtime data 与代码目录解耦。

端到端验收只跨越一个最高层测试缝隙：给定一次计划触发和一份有效业务配置，系统最终产生可验证的运行 manifest 与 HTML，并由 Codex按状态正确交付或告警。

## User Stories

1. 作为投资研究用户，我希望每天自动收到结构稳定的 HTML 日报，以便快速形成固定阅读习惯。
2. 作为投资研究用户，我希望日报固定包含技术进展、产业新闻和融资新闻，以便按投资主题浏览。
3. 作为投资研究用户，我希望每条新闻都能回溯原始来源，以便验证事实和继续研究。
4. 作为投资研究用户，我希望同一融资事件的多篇报道被合并，以便减少重复阅读。
5. 作为投资研究用户，我希望自动任务部分失败时仍能收到可用日报，并清楚看到缺失来源。
6. 作为投资研究用户，我希望历史日报及其生成配置可追溯，以便比较不同日期和版本。
7. 作为 GUI 用户，我希望继续通过界面切换 LLM provider 和模型，以便不修改自动任务配置就能更换服务。
8. 作为 GUI 用户，我希望继续管理信息源、Prompt、并发数和代理设置，以便自动任务使用我最新确认的业务配置。
9. 作为 GUI 用户，我希望保留立即抓取、指定日期补跑和仅重新生成报告的入口，以便处理遗漏和人工复核。
10. 作为 GUI 用户，我希望自动任务运行期间看到明确状态，以便避免误触重复执行。
11. 作为系统维护者，我希望 GUI 和 Headless 共用同一个业务编排入口，以便避免两套 pipeline 演化出不同结果。
12. 作为系统维护者，我希望只有一个组件承担正式调度职责，以便消除双重触发。
13. 作为系统维护者，我希望每次运行拥有唯一 run ID 和独立产物目录，以便强制补跑不会覆盖历史。
14. 作为系统维护者，我希望重复触发能够被幂等规则或运行锁安全处理，以便保护 SQLite 和外部 API 配额。
15. 作为系统维护者，我希望每个流水线阶段有机器可读状态和耗时，以便快速定位抓取、LLM、数据校验或渲染故障。
16. 作为系统维护者，我希望敏感配置只存在于现有加密配置层，以便日志、manifest 和 Codex 提示词不泄漏密钥。
17. 作为系统维护者，我希望 LLM 输出失败时有确定性降级路径，以便模型波动不必然中断日报。
18. 作为系统维护者，我希望无法生成可信 report data 时停止 HTML 渲染，以便不向用户交付错误页面。
19. 作为 Codex 任务执行者，我希望通过退出码、stdout 和 manifest 精确定位本次结果，以便无需猜测产物位置。
20. 作为 Codex 任务执行者，我希望只调用稳定 Headless 命令，以便不依赖 GUI、浏览器或 FastAPI 生命周期。
21. 作为未来部署维护者，我希望 runtime data 可以逐步迁移到独立目录，以便未来支持 worktree 或云端执行。

## Implementation Decisions

- 系统划分为 GUI/WebUI、共享业务内核、Headless CLI、结构化报告与模板渲染、Codex Automation 五个责任域。
- GUI/API 与 Headless 必须调用同一个高层 orchestration facade；API route 只负责请求转换和响应映射，不承载独有业务步骤。
- GUI 保存的数据库配置是信息源、LLM、Prompt、代理和并发参数的唯一事实来源。
- Headless 只接受日期、强制补跑、跳过抓取和测试输出目录等运行控制参数，不接受 provider、模型、API Key、Prompt 或业务规则参数。
- 正式调度的目标状态是 Codex 单一主责。兼容迁移期提供 internal、external、disabled 三种模式，默认先保持 internal，正式 Codex 环境显式使用 external。
- 运行锁覆盖所有可能启动完整流水线或与其冲突的写操作。首版在自动运行期间保守禁用 GUI 写操作，之后再通过并发测试逐步放行低风险写入。
- 每次运行读取一份非敏感配置快照，并在整个 run 中保持一致；密钥本身不得进入快照或日志。
- 所有业务日期采用 Asia/Shanghai 自然日。可信发布时间优先决定内容归属，缺失或估算时间回退到抓取时间并保留时间质量标识。
- 每次运行使用唯一 run ID 和独立目录；日期级 latest 指针只指向最近完成结果，不覆盖历史产物。
- Headless 的 stdout 最后一行提供机器可读的本次结果定位；latest 指针作为异常恢复手段。
- `run_manifest` 是运行控制契约，记录状态、阶段、计数、警告、错误和产物位置。
- `report_data` 是内容与展示之间的契约，固定三个一级类目，支持动态二级主题，并要求每条内容关联已有 content ID 或 event ID。
- 所有 URL 必须来自持久化原始数据，不允许由 LLM 生成或改写为未知地址。
- LLM 原始输出必须解析、清洗并通过 schema 校验。修复重试失败后，由确定性规则构造合法降级数据并标记 partial；降级数据仍不合法时停止渲染。
- HTML 使用版本化、无外部运行依赖的单文件模板，内联样式，支持离线打开、归档和打印。
- 历史 Markdown 继续作为兼容和排障产物，但 HTML 是主要用户交付物。
- 第一阶段直接在真实本地项目目录运行，以访问现有虚拟环境、SQLite、密钥和历史数据；runtime data 解耦属于后续演进。
- 整体上线按基线确认、Headless 契约、结构化 HTML、Codex Automation、两周稳定化分阶段进行。

## Testing Decisions

- 最高层验收测试以一次完整计划触发为输入，以 Codex 最终交付状态和 HTML 产物为输出，不断言内部函数调用顺序。
- 契约测试验证 `run_manifest` 和 `report_data` 的 schema、版本兼容、状态转换和路径解析。
- 集成测试使用临时 SQLite、受控信息源和 fake LLM，覆盖 success、partial、skipped、lock conflict、preflight failure、schema failure 和 render failure。
- 一致性测试验证 GUI 修改 provider、模型、Prompt 或信息源后，下一次 Headless 读取同一配置且无需修改 Codex 任务。
- 并发测试验证 GUI 与 Headless 同时触发时只有一个完整流水线运行，且不会产生重复记录或破坏数据库。
- 追溯测试验证所有 HTML 卡片的内容 ID 或事件 ID、标题和 URL 与 report data 及数据库记录一致。
- HTML 黑盒测试使用 DOM 解析，要求 report data item 总数等于带报告条目标识的节点数，并校验三个一级类目、空状态、链接和转义行为。
- 日期测试覆盖北京时间跨日、缺失发布时间、估算时间、历史补跑和跳过抓取。
- 降级测试验证非法模型输出不会直接进入模板，规则降级仍可生成 partial 页面，最终 schema 失败则不生成 HTML。
- 参考现有抓取、融资过滤、周报 Prompt、报告版本和 Dashboard 日期测试的 fixture 与临时数据库模式，避免另建测试框架。

## Out of Scope

- 第一阶段不迁移到云端常驻 worker。
- 第一阶段不支持在临时 Git worktree 中直接运行生产日报。
- 不由 Codex 自主搜索、抓取或直接写业务数据库。
- 不让 Codex每日自由生成新的 HTML/CSS 结构。
- 不重构全部现有模型、API 和前端页面。
- 不在本 PRD 中决定日报视觉品牌的最终美术稿。
- 不在本 PRD 中定义对外邮件、Slack、公众号或公开网站分发。
- 不在首版切换 SQLite journal mode。

## Further Notes

- 建议先完成最小闭环：health、daily、manifest、退出码、最简 HTML 和 external scheduler mode，再增加 LLM 驱动的三类报告组织。
- 连续两周自动运行稳定后，再评估是否将 internal scheduler 默认切换为 external，并启动 runtime data 解耦。
- 本 PRD 关注跨组件契约；组件内部实现分别由另外两份 PRD约束。
