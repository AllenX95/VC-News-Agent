# VC News Agent Headless 改动设计

> 状态：Implementation design
> 日期：2026-08-17
> 上游：整体架构设计 PRD、VC-news-agent 侧改动建议 PRD

## 1. 设计结论

新增一个深模块 `DailyRunOrchestrator`，将初始化、配置读取、抓取、日报生成、产物写入、状态转换、幂等和运行锁隐藏在两个外部 interface 后：

- `health()`：返回不产生业务副作用的环境检查结果。
- `run(options)`：执行或安全跳过一次完整日报，返回 `RunResult`。

Headless CLI 和后续 GUI/API 都是这个 seam 上的 adapter，不自行编排底层步骤。

## 2. 模块与责任

### DailyRunOrchestrator

负责：

- 创建数据库、迁移和 seed；
- 应用数据库中保存的代理配置；
- 获取和释放完整流水线运行锁；
- 计算目标北京时间自然日；
- 执行抓取或按选项跳过抓取；
- 调用日报数据模块和 HTML 渲染模块；
- 保留 Markdown 兼容产物；
- 从运行开始持续写 manifest；
- 写独立 run ID 目录并原子更新 latest；
- 将内部异常映射为稳定状态和退出码。

不负责：

- 解析命令行；
- 启动 FastAPI 或 APScheduler；
- 保存 provider、模型、Prompt 或信息源配置；
- Codex 通知或外部分发。

### Report Data 模块

负责：

- 从目标日的内容和融资事件构造稳定 report data；
- 固定 technology、industry、funding 三个一级类目；
- 保留 content ID 或 event ID、来源 URL 和时间质量；
- 确定性分类、去重、排序和空类目；
- schema 级校验。

首个开发切片使用确定性规则。后续接入日级 LLMTask 时，LLM 输出仍必须回到同一 report data interface，不能绕过校验。

### HTML Renderer 模块

负责：

- 只接收已校验 report data；
- 使用版本化 Jinja 模板生成自包含 HTML；
- 输出稳定 DOM 标识供 Codex 黑盒验收；
- HTML escaping、空状态、长文本和打印布局。

### Scheduler Mode

负责 GUI/FastAPI 启动时是否启用内部调度：

- `internal`：保持 APScheduler 与 startup catch-up。
- `external`：不启动 APScheduler，不执行 startup catch-up。
- `disabled`：只允许人工执行，同样不启动内部调度。

Headless 不读取该模式决定是否运行，也永远不启动 APScheduler。

## 3. 核心 interface

### DailyRunOptions

- `target_date`：可选，北京时间 ISO 日期；默认今天。
- `force`：已有 success 时仍创建新 run。
- `skip_crawl`：只基于数据库数据生成报告。
- `output_root`：可选，仅用于测试或明确导出覆盖。

### RunResult

- `run_id`
- `status`
- `exit_code`
- `target_date`
- `manifest_path`
- `artifacts`
- `warnings`
- `error`

### 报告 interface

- `build_daily_report_data(db, target_date, warnings=None) -> dict`
- `validate_report_data(data) -> list[str]`
- `render_daily_html(report_data, output_path) -> Path`

## 4. 状态机

| 状态 | 退出码 | 是否有 HTML | latest 是否更新 |
|---|---:|---:|---:|
| success | 0 | 是 | 是 |
| skipped_already_success | 1 | 复用已有 | 否 |
| partial | 2 | 是 | 是 |
| preflight_failed | 10 | 否 | 否 |
| pipeline_failed | 20 | 否 | 否 |
| report_data_invalid | 30 | 否 | 否 |
| html_render_failed | 40 | 否 | 否 |
| lock_conflict | 50 | 否 | 否 |

单信息源失败不自动等于完整 pipeline 失败：只要 report data 合法且 HTML 可用，最终通常为 partial，并在 warnings 中记录失败来源。

## 5. 文件与原子性

```text
runs/<target-date>/
  latest.json
  <run-id>/
    run_manifest.json
    report_data.json
    daily.html
    daily.md
    run.log
```

- 所有 JSON 和 HTML 先写同目录临时文件，再原子替换目标。
- `force` 永远创建新 run ID，不覆盖旧产物。
- stdout 最后一行只输出一条机器可读 JSON，用于定位本次 manifest。
- 无法创建 runtime 目录时，stderr 输出最小 JSON 错误。

## 6. 并发与配置一致性

- 完整 run 在 runtime 根目录持有单实例锁。
- 锁内容包含 PID、run ID 和开始时间。
- 陈旧锁只有在 PID 不存活或超过保守最大时长时才可回收。
- 获取锁后读取本次非敏感配置快照；密钥不写 manifest。
- 首版 GUI 在完整 run 期间应保守阻止冲突写操作；API 适配放到后续切片。

## 7. 测试 seam

主要测试只跨越 `DailyRunOrchestrator.health/run`：

- 注入本地 fake 抓取、报告构建和渲染 adapter；
- 断言 RunResult、退出码、manifest 和产物；
- 不断言底层函数调用顺序以外的实现细节。

独立契约测试跨越 report data 和 renderer interface，验证三类结构、追溯、URL、escaping 和 DOM item 数。

调度模式通过应用 startup 的外部行为测试，不测试 APScheduler 私有实现。

## 8. 首个开发切片范围

本轮实现：

- scheduler mode；
- health 与 daily CLI；
- orchestration、运行锁、幂等、manifest、latest 和退出码；
- 确定性三类 report data；
- 自包含 HTML 与 Markdown 兼容产物；
- 对应单元及集成测试。

后续切片：

- runtime data 根目录解耦；
- Codex Automation 正式创建与真实定时验收。

## 9. 首版完整功能补充

首版正式验收已补充：

- `generate_daily_investment_report` 的 LLMTask、默认 Prompt 和 GUI 模型绑定；
- LLM 只可通过已有 content/event ID 修改摘要、投资意义、类目和主题，不能修改 URL、来源或引入新 ID；
- LLM 未配置或失败时保留确定性报告并标记降级状态；
- Dashboard 与报告工作区展示自动任务状态、计数、告警和 HTML 入口；
- Headless 与 GUI 共用跨进程运行锁；所有 GUI 写请求原子获取锁，后台抓取接管锁直到任务真正结束。

runtime data 根目录解耦仍属于 v1.1，不阻塞首版在真实本地项目目录中的交付。
