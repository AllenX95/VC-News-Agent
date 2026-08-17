# PRD 02：Codex 侧定时执行、验收与交付方案

> 状态：Ready for implementation
> 版本：1.0
> 日期：2026-08-17
> 依赖：整体架构设计 PRD、VC-news-agent 提供稳定 Headless 契约

## Problem Statement

用户需要 Codex 每天自动运行 VC News Agent 并交付 HTML 日报，但 Codex 不应进入 GUI 操作，也不应替代应用执行抓取、去重、LLM 分类和报告生成。若 Codex 依赖自然语言猜测运行是否成功、从不稳定路径寻找产物、看到错误后自行重做新闻研究，自动任务将不可预测且难以审计。

本地任务还受到机器在线、Codex Desktop 运行状态、项目目录、Python 环境、SQLite 数据、密钥和网络权限的约束。Codex 侧需要一个最小、确定、可恢复的自动化方案，能够按明确状态处理成功、部分成功、重复任务、锁冲突和失败，并把正确的 HTML 成品呈现给用户。

## Solution

在 Codex Desktop 中创建绑定真实本地项目目录的每日 Automation。任务只执行两类稳定命令：运行前健康检查和每日 Headless 流水线。Codex 从命令 stdout 获取本次 manifest 位置，解析 manifest 和 report data，对 HTML 做确定性验收，然后根据退出码和状态交付 HTML、提示警告或报告失败。

Codex 不启动 GUI、Tauri、浏览器或 FastAPI，不修改源代码，不直接读写业务数据库，不自行补充新闻。自动任务使用真实项目目录而非 worktree，以确保访问现有虚拟环境、数据库、密钥和 runtime 产物。正式自动化显式采用 external scheduler mode，避免与应用内部 APScheduler 重复触发。

Codex 侧的最高测试 seam 是“给定 Headless 的退出码、stdout 定位信息和产物集合，Codex 是否产生正确的用户交付结果”。

## User Stories

1. 作为投资研究用户，我希望 Codex 每天在固定时间运行日报任务，以便无需手动启动应用。
2. 作为投资研究用户，我希望成功时直接获得 HTML 文件，以便立即阅读。
3. 作为投资研究用户，我希望部分成功时仍获得日报，并清楚知道哪些信息源或阶段有问题。
4. 作为投资研究用户，我希望失败时看到简洁、可行动的原因和日志位置，而不是含糊的失败消息。
5. 作为投资研究用户，我希望任务重复触发时安全跳过，以便不会重复消耗抓取和 LLM 配额。
6. 作为投资研究用户，我希望历史成功报告在本次跳过时仍可重新交付。
7. 作为系统维护者，我希望 Automation 不修改代码，以便定时运行不会污染工作区。
8. 作为系统维护者，我希望 Automation 不启动 GUI 或 Web 服务，以便减少无人值守运行的故障面。
9. 作为系统维护者，我希望 Codex 在执行前检查 Python、数据库、密钥、输出目录和基本网络条件，以便尽早发现环境问题。
10. 作为系统维护者，我希望 Codex 从机器可读 stdout 定位本次 manifest，以便不依赖目录猜测。
11. 作为系统维护者，我希望 Codex 校验 manifest schema 和状态，以便错误状态不会被误报为成功。
12. 作为系统维护者，我希望 Codex 校验三个固定类目，以便日报结构不会静默退化。
13. 作为系统维护者，我希望 Codex 校验 HTML 卡片数、内容 ID 和 URL，以便产物与 report data 保持一致。
14. 作为系统维护者，我希望锁冲突不被视为系统故障，以便正在运行的任务不被干扰。
15. 作为系统维护者，我希望 preflight 失败时不继续抓取，以便保护数据库和外部服务。
16. 作为系统维护者，我希望 Codex 不在应用失败后自行搜索新闻或写数据库，以便职责边界保持清晰。
17. 作为系统维护者，我希望 Codex 的成功通知包含关键计数，以便快速判断当日报告覆盖质量。
18. 作为未来运维者，我希望 Automation prompt 与产物契约版本对应，以便应用升级后可以安全演进。

## Implementation Decisions

- 使用 Codex Desktop 的本地项目 Automation，绑定真实项目目录；第一阶段不使用 dedicated worktree。
- 默认计划时间为北京时间每日 10:05，避免整点边界波动；具体时间允许用户在创建 Automation 时调整。
- 本地机器和 Codex Desktop 必须在触发时间保持可运行状态；错过的任务通过用户手动补跑处理，首版不假设云端接管。
- Automation 环境显式采用 external scheduler mode，确保 Codex 是正式调度主责。
- 每次运行先调用 health；health 非成功时立即停止，不调用 daily。
- daily 为同步前台命令，Codex 等待其退出，不启动后台常驻进程。
- Codex 以退出码为第一层控制信号，以 stdout 最后一行的 JSON 为本次 manifest 定位，以 manifest 为详细事实来源。
- 当 stdout 定位信息缺失时，Codex 可以读取日期级 latest 指针恢复；不得扫描并猜测多个运行目录中的“最新文件”。
- 成功状态交付 HTML，并报告目标日期、成功/失败信息源数、新增条目数和入选条目数。
- 部分成功状态仍交付 HTML，明确呈现 manifest warnings，避免将可用结果隐藏。
- 已有成功结果的跳过状态读取并交付既有 HTML，不再次执行或生成新版本。
- 锁冲突状态说明已有任务运行，不改写 latest，不终止或重启现有进程。
- preflight、流水线、schema 和 HTML 渲染失败按专用退出码映射为不同用户消息，并提供阶段、错误和日志位置。
- Codex 验证 manifest 声明的 HTML 存在、非空且位于允许的 runtime 目录下。
- Codex 验证 report data 包含 technology、industry、funding 三个一级类目，且每类最多出现一次。
- Codex 使用 DOM 解析进行黑盒验收：report data item 总数等于报告条目节点数，条目 ID 和 href 精确匹配。
- Codex 不评判每条投资结论是否“足够好”，只检查结构、追溯、完整性和显式状态；内容质量调优属于应用及 Prompt 配置。
- Codex 不修改源代码、不运行代码格式化、不自动提交、不直接访问或修复 SQLite。
- Codex 不在失败后调用网络搜索重做报告；重试只允许通过同一 Headless 接口，并遵守幂等和锁规则。
- Automation prompt 必须注明预期 manifest schema 版本；遇到不支持的新版本时停止并提示契约不兼容。
- 自动化创建属于上线阶段操作，不在仅生成 PRD 时提前创建。

## Testing Decisions

- Codex 侧测试只模拟 Headless 外部结果，不依赖其内部服务实现。
- 用固定 fixture 覆盖退出码 0、1、2、10、20、30、40、50，并验证每种结果的交付、告警和停止行为。
- 验证 health 失败后 daily 从未执行。
- 验证 stdout JSON 优先于 latest 指针，stdout 缺失时才使用 latest 恢复。
- 验证 manifest 路径越出允许 runtime 目录时拒绝读取或交付。
- 验证 HTML 缺失、空文件或不可读时不会报告成功。
- 验证三个一级类目缺失、重复或 key 错误时报告 schema/QA 失败。
- 验证 DOM 条目数量、内容 ID、事件 ID和 URL 任一不匹配时停止成功交付。
- 验证 partial 状态保留 HTML 交付，同时完整呈现 warnings。
- 验证 skipped 状态复用既有成功产物且不会产生新运行目录。
- 验证 lock conflict 不触发强制重试或进程终止。
- 完成至少一次人工触发和一次真实定时触发的端到端验收。

## Out of Scope

- Codex 不负责信息源发现、抓取、清洗、去重和融资事件合并。
- Codex 不负责选择 LLM provider、模型、Prompt 或 API Key。
- Codex 不负责生成 report data 或 HTML 模板。
- Codex 不直接修改 SQLite、配置文件或业务记录。
- Codex 不负责 GUI 状态展示和人工编辑功能。
- 首版不在用户电脑关机时转为云端运行。
- 首版不自动发送邮件、Slack 或其他外部分发渠道。
- 首版不自动修复应用代码或创建 PR。

## Further Notes

- 创建 Automation 前必须先用同一用户、同一项目目录和同一权限环境手工跑通 health 与 daily。
- 推荐连续完成五次手动 Headless 成功运行后，再启用真实定时触发。
- 若未来 runtime data 解耦完成，可重新评估 worktree 或云端触发，但 Codex 侧仍应保持相同 manifest 契约。
