# PRD 03：VC-news-agent 侧改动建议

> 状态：Ready for implementation
> 版本：1.0
> 日期：2026-08-17
> 依赖：整体架构设计 PRD

## Problem Statement

VC News Agent 当前以 GUI/FastAPI 为主要入口，应用启动时会启用内部 APScheduler，并可能执行启动补抓。虽然抓取、LLM、日报和报告服务已经存在，但尚无适合 Codex 无人值守调用的同步 Headless 入口，也没有标准运行锁、manifest、退出码、结构化三类日报数据和确定性 HTML 渲染链路。

当前日报按信息源类别组织，不等同于用户需要的技术进展、产业新闻、融资新闻三类投资情报。现有 LLM 配置已经支持 GUI 管理 provider、模型和 Prompt，但如果新增 Headless 时绕过现有任务绑定，或在 API route 中复制流水线，会破坏配置一致性。runtime data 目前与代码目录耦合，且 SQLite 使用特殊 journal mode，需要谨慎处理并发、补跑和历史产物。

应用侧需要在不破坏现有 GUI 人工流程的前提下，提供一个稳定、同步、可测试、可降级的领域执行引擎，并把 Codex 所需的全部状态通过文件契约和退出码暴露出来。

## Solution

新增一个共享 orchestration facade 和同步 Headless CLI。GUI/API 与 CLI 都通过该 facade 运行抓取、内容处理、融资事件、结构化日报和渲染步骤。Headless 从现有 SQLite 和加密配置层读取业务配置，并提供 health、daily 以及开发/补跑子命令。

应用生成版本化 run manifest 和 report data，使用固定 Jinja 模板输出单文件 HTML，同时保留 Markdown。为正式外部调度增加 scheduler mode，支持 internal、external、disabled；Headless 本身从不启动内部 scheduler。所有完整流水线共享单实例锁和幂等状态，强制补跑创建独立 run ID，不覆盖历史。

应用侧的最高测试 seam 是 orchestration facade：给定目标日期、运行选项和配置快照，返回一个完整 RunResult，并产生与其一致的 manifest 和产物。CLI 与 API 只做薄适配。

## User Stories

1. 作为 Codex 调度者，我希望通过单个同步命令执行完整日报，以便可靠等待最终状态。
2. 作为 Codex 调度者，我希望先运行 health 检查，以便在环境不完整时不启动业务流水线。
3. 作为 Codex 调度者，我希望 stdout 返回本次 manifest 的机器可读位置，以便准确验收结果。
4. 作为 Codex 调度者，我希望不同失败类型有稳定退出码，以便采取正确交付行为。
5. 作为 GUI 用户，我希望现有人工抓取和报告入口继续可用，以便自动化上线不改变日常操作。
6. 作为 GUI 用户，我希望 Headless 使用我在界面保存的 provider、模型、API Key 和 Prompt，以便配置只有一个入口。
7. 作为 GUI 用户，我希望看到自动任务的运行状态和最新 HTML，以便人工检查与补跑。
8. 作为 GUI 用户，我希望在自动流水线运行时不能误触冲突操作，以便避免数据库写入竞争。
9. 作为投资研究用户，我希望报告按技术进展、产业新闻、融资新闻组织，以便快速阅读。
10. 作为投资研究用户，我希望二级主题根据每日内容变化，以便保留信息表达能力。
11. 作为投资研究用户，我希望每条报告内容可追溯到原始内容或融资事件，以便验证来源。
12. 作为投资研究用户，我希望重复报道和重复融资事件被合并，以便日报简洁。
13. 作为投资研究用户，我希望 LLM 不可用时仍获得规则降级版，以便自动任务具有韧性。
14. 作为投资研究用户，我希望降级状态在页面中明确可见，以便不会误认为完整智能摘要。
15. 作为系统维护者，我希望 GUI 和 CLI 共用一个 orchestration seam，以便维护一份 pipeline。
16. 作为系统维护者，我希望每次运行固定使用启动时配置快照，以便运行中修改设置不会造成混合结果。
17. 作为系统维护者，我希望完整流水线持有单实例锁，以便防止重复抓取和 SQLite 冲突。
18. 作为系统维护者，我希望同日成功任务默认安全跳过，以便节省资源。
19. 作为系统维护者，我希望强制补跑创建新 run ID，以便保留所有历史结果。
20. 作为系统维护者，我希望所有阶段状态、耗时和计数进入 manifest，以便诊断故障。
21. 作为系统维护者，我希望日志不包含解密密钥或敏感请求头，以便保护凭据。
22. 作为系统维护者，我希望 HTML 模板有明确版本，以便历史报告可重现。
23. 作为系统维护者，我希望历史日期补跑不假设所有信息源支持回溯，以便避免制造虚假完整性。
24. 作为未来维护者，我希望 runtime data 可迁移到独立根目录，以便未来支持 worktree 或其他执行环境。

## Implementation Decisions

- 新增一个高层 orchestration facade，封装初始化、配置快照、抓取、融资事件更新、报告数据生成、schema 校验、模板渲染、manifest 更新和清理。
- facade 接受目标日期和少量运行选项，返回稳定 RunResult；CLI 和 API 不直接编排底层服务。
- Headless 提供 health 和 daily 作为正式稳定命令；crawl、summary、render 可作为开发和人工补跑命令，但不得成为 Codex 正常流程的多个拼接步骤。
- daily 必须同步执行，完成所有阶段后退出，不创建长期后台线程，不依赖 FastAPI 生命周期。
- health 验证 Python 运行环境、数据库可读写、schema/migration、密钥可读取、runtime 目录可写、有效信息源、日级报告 LLM 任务绑定和必要网络前置条件。
- health 不执行真实全量抓取，不消耗大量 LLM 配额；外部连通性只做轻量检查。
- 继续使用现有数据库中的 LLMConfig、LLMTask、Prompt 和安全模块作为唯一模型配置来源。
- 新增日级投资报告任务绑定，由 GUI 选择 provider、模型和 Prompt；OpenAI-compatible 服务继续走已有协议归一化。
- 启动完整 run 后读取非敏感配置快照；本次运行使用快照中的 source、任务绑定、并发和 Prompt 版本标识。
- 调度模式支持 internal、external、disabled。兼容迁移期默认 internal；external 时 GUI/FastAPI 不启动 APScheduler、不执行 startup catch-up。
- Headless 永远不启动 APScheduler，因此其运行不依赖调度模式。
- 所有完整流水线和冲突写操作共享同一运行锁。锁记录 PID、run ID 和开始时间，并支持基于 PID 存活与最大运行时长判断陈旧锁。
- 幂等键为目标日期和命令。当已有成功结果时默认返回 skipped；partial 允许重试；force 创建新 run ID。
- 所有业务日期按 Asia/Shanghai 处理。默认日报窗口为目标日自然日；历史补跑优先使用数据库已有内容，只有明确支持历史窗口的信息源才重新抓取。
- 报告分类增加独立投资情报层，不把 source category 直接当成最终一级类目。
- 固定一级类目为 technology、industry、funding；允许规则或 LLM 生成动态二级主题。
- 融资新闻优先使用已合并融资事件作为报告实体，避免同一事件因多来源重复入选。
- 每个 report item 必须携带 content ID 或 event ID，URL 只能从已有记录复制，不能信任模型生成 URL。
- 模型输出先解析和 schema 校验；非法输出按有限次数修复/重试，之后使用确定性规则降级。
- 规则降级必须生成合法 report data 并标记 partial；最终数据仍不合法时返回数据校验失败，不渲染 HTML。
- 模板渲染采用版本化 Jinja 模板，首版输出自包含单文件 HTML，并为每个报告条目输出稳定 DOM 标识、内容或事件 ID。
- HTML、Markdown、report data、manifest 和日志写入 run ID 独立目录；日期级 latest 指针原子更新。
- manifest 从运行开始即创建并逐阶段更新。若 runtime 根目录不可写，则以 stderr 单行 JSON 返回最小错误。
- 退出码稳定区分 success、skipped、partial、preflight failure、pipeline failure、schema failure、render failure 和 lock conflict。
- GUI Dashboard 展示最近自动运行状态、关键计数和 HTML 打开入口。
- GUI Settings 展示有效调度模式及其来源；LLM 页面支持日级报告任务绑定；Reports 页面支持 HTML 主产物和 Markdown 兼容产物。
- 自动运行期间首版保守禁用所有 GUI 写操作；后续根据 SQLite 并发测试放行收藏、备注等低风险操作。
- 第一阶段保留当前 SQLite journal mode，不在本功能中切换 WAL。
- 第二阶段引入统一 runtime data 根目录，并继续允许数据库路径做细粒度覆盖；该演进不得改变 Headless 契约。

## Testing Decisions

- 以 orchestration facade 为主要测试 seam，验证外部 RunResult 和产物，不对内部服务调用顺序做脆弱断言。
- CLI 测试验证参数解析、stdout 单行 JSON、stderr 失败 JSON 和所有退出码。
- health 测试覆盖数据库缺失、密钥缺失、输出目录不可写、任务绑定缺失和轻量网络失败。
- scheduler mode 测试验证 external 和 disabled 下均不启动 APScheduler、不执行 startup catch-up，internal 保持现有行为。
- 配置一致性测试验证 GUI 更新 provider、模型、Prompt、信息源和并发后，下一次 run 读取新快照。
- 锁测试覆盖首次获取、并发冲突、正常释放、异常释放和陈旧锁回收。
- 幂等测试覆盖成功后跳过、partial 重试、force 新版本和 latest 原子更新。
- 日期测试覆盖北京时间边界、历史补跑、skip-crawl、可信发布时间、估算时间和抓取时间回退。
- 分类测试覆盖三个固定一级类目、动态二级主题、空类目、排序和未知类型回退。
- 融资测试验证同一事件多来源合并、主要来源选择和 event ID 追溯。
- LLM 测试使用 fake provider，覆盖合法 JSON、包装 JSON、非法 JSON、超时、重试、规则降级和最终 schema 失败。
- 安全测试验证 manifest、日志、stdout 和错误信息不包含 API Key、Authorization header 或解密凭据。
- HTML 测试通过 DOM 解析验证条目数、稳定 data 属性、ID、href、HTML escaping、长标题、空状态和打印所需结构。
- 文件测试验证每个 run 独立保存、force 不覆盖历史、latest 只在合法完成状态下更新。
- API/GUI 集成测试验证人工入口与 Headless 通过同一 facade 获得一致结果，且运行锁状态正确映射到 UI。
- 复用现有临时 SQLite、fake LLM、融资过滤、报告版本、周报 Prompt 和日期测试的先例。

## Out of Scope

- 不由 VC News Agent 创建或管理 Codex Automation。
- 不在应用内实现第二套 cron 作为 Codex 的备份调度器。
- 不新增独立 CLI provider、模型、API Key 或 Prompt 参数。
- 不将 API route 作为 Headless 内部调用接口。
- 不在首版迁移到 Postgres、任务队列或分布式锁。
- 不在首版支持多个完整日报流水线并行运行。
- 不在首版完成云端部署或 worktree runtime data 共享。
- 不在本 PRD 中决定最终视觉品牌或外部分发渠道。

## Further Notes

- 推荐第一个 tracer-bullet 只完成 health、daily、共享 facade、manifest、退出码、运行锁、external scheduler mode 和最简 HTML，先证明 Codex 可稳定验收。
- 第二个切片再引入三类结构化报告、LLM 日级任务绑定、融资事件合并和完整模板。
- 在真实 Automation 上线前，要求连续五次手动 Headless 运行成功，并验证至少一次 LLM 降级和一次锁冲突。
