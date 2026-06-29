# AI 投资情报 Agent PRD｜开发确认版

版本：v1.0  
日期：2026-05-11  
状态：需求确认稿，后续开发以本文档为准  

---

## 1. 产品定位

### 1.1 产品名称

AI 投资情报 Agent：信息收集与整理模块。

### 1.2 目标用户

本产品服务于中国市场 VC/PE 机构中覆盖 AI 赛道的高级投资经理个人长期使用。

MVP 明确不做：

- 账号系统
- 多人协同
- 权限管理
- 云端团队部署
- 审批流
- CRM
- 投资 memo
- 投资评分
- 深度行业研究 Agent

### 1.3 产品形态

MVP 为本地 Web 应用：

- 用户在本机启动服务
- 浏览器打开本地 Web 页面使用
- 数据保存在本地 SQLite
- 适合个人长期运行和维护

### 1.4 MVP 核心目标

MVP 的核心不是生成投资判断，而是建立稳定、可维护的信息底座。

核心目标：

1. 可编辑的信息源管理
2. 每日自动抓取公开信息
3. 提取标题、URL、发布时间、来源、正文缓存等基础信息
4. 生成约 100 字中文 summary
5. 提取基础标签和轻量实体
6. 内容入库、去重、检索、筛选
7. 按分类生成每日信息汇总列表
8. 支持 Markdown 导出
9. 支持 LLM 配置和 Prompt 配置
10. 支持 SQLite 自动备份

### 1.5 当前阶段不做

| 不做内容 | 说明 |
|---|---|
| 投资分析 | 不自动判断是否值得投资 |
| 投资优先级评分 | 不对项目进行投资评分 |
| 投资 memo | 不生成完整投资 memo |
| 深度研究 | 不做论文、报告、公告深度分析 |
| 微信公众号 | MVP 暂缓 |
| 多人协同 | 不做账号、权限、团队共享 |
| 长期全文保存 | 不长期保存全文、raw HTML、图片、网页快照 |
| excerpt | 不做 300-1000 字短摘，仅保留约 100 字 summary |

---

## 2. MVP 定义

### 2.1 核心工作流

```text
可编辑信息源
  ↓
每日定时抓取 / 手动抓取
  ↓
URL 规范化与去重
  ↓
正文抽取或榜单字段抽取
  ↓
短期 clean_text 缓存
  ↓
LLM 生成 summary / tags / entities
  ↓
长期 metadata 入库
  ↓
内容库检索与人工修正
  ↓
每日信息汇总快照
  ↓
Markdown 导出
```

### 2.2 MVP 成功标准

| 指标 | 目标 |
|---|---|
| 本地 Web 可用 | 用户可在浏览器打开本地应用 |
| 信息源可维护 | 支持新增、编辑、删除、启用、停用、测试抓取 |
| 每日自动抓取 | 北京时间每天 10:00 自动抓取 |
| 错过补抓 | 若 10:00 程序未启动，当天首次启动且已过 10:00 时自动补抓 |
| 信息源首批跑通 | 跑通确认的中文源、海外源、GitHub Trending、Product Hunt、Hacker News |
| 去重 | 同一 canonical URL 不重复入库 |
| 存储轻量化 | 长期只保存 metadata、summary、tags、entities、fingerprint 等 |
| 内容库可用 | 支持搜索、筛选、按来源查看、编辑、收藏 |
| 每日汇总 | 每日生成结构化、精炼快照，可导出 Markdown |
| LLM 可配置 | 支持本地保存 LLM 配置和 Prompt |
| 备份 | 支持 SQLite 自动备份 |

---

## 3. 技术方案

### 3.1 技术栈

| 模块 | 技术选择 |
|---|---|
| 后端 | Python + FastAPI |
| 数据库 | SQLite |
| ORM | SQLAlchemy |
| 定时任务 | APScheduler |
| 前端 | 轻量 HTML / JS / FastAPI 模板 |
| 抓取 | API / RSS / Sitemap / HTTP / Playwright |
| 浏览器抓取 | Playwright Browser Worker |

不使用 React。MVP 以稳定、简单、可维护为优先。

### 3.2 SQLite 原则

MVP 使用 SQLite 作为本地主数据库。

硬性要求：

1. 开启 WAL 模式
2. 抓取可并发，写库必须单 writer 或低并发批量写入
3. 不长期保存全文
4. 不长期保存 raw HTML、图片、网页快照
5. 业务代码尽量通过 ORM / 数据访问层实现，保留未来迁移 PostgreSQL 的可能

建议初始化：

```sql
PRAGMA journal_mode=WAL;
```

### 3.3 未来迁移原则

如未来出现以下情况之一，再考虑迁移 PostgreSQL：

- SQLite DB 超过 50-100GB
- 内容记录超过 100 万-300 万条
- 多 worker 高频并发写入
- 多人同时使用
- 需要长期全文搜索
- 需要语义检索 / pgvector
- 需要复杂统计和趋势分析
- 需要云端多实例部署

---

## 4. 信息源范围

### 4.1 P0 首批中文信息源

先跑通以下 5 个中文信息源：

| 信息源 | 类型 | 备注 |
|---|---|---|
| 36氪 | 中文科技 / 创投媒体 | 综合创投与科技资讯 |
| 机器之心 | AI 垂直媒体 | AI 技术、模型、产业动态 |
| 量子位 | AI 垂直媒体 | AI 产业、机器人、模型、融资动态 |
| 晚点 LatePost | 科技商业媒体 | 信息质量高，抓取稳定性需验证 |
| 投资界 / PEDaily | 创投媒体 | 融资、基金、IPO、创投动态 |

### 4.2 P0 首批海外信息源

海外源先聚焦 OpenAI 和 Anthropic，但需要区分 News 与 Research：

| 信息源 | URL | 类型 |
|---|---|---|
| OpenAI News | https://openai.com/news/ | 官方新闻 |
| OpenAI Research | https://openai.com/research/ | 官方研究 |
| Anthropic News | https://www.anthropic.com/news | 官方新闻 |
| Anthropic Research | https://www.anthropic.com/research | 官方研究 |

### 4.3 P0 社区与榜单源

| 信息源 | 抓取范围 | Token 要求 |
|---|---|---|
| GitHub Trending | Daily | 不需要 |
| Product Hunt | 今日榜单 | 不需要 |
| Hacker News | 每次抓 Top 20 | 不需要 |

Hacker News 使用官方公开 API，不需要 API token。

Product Hunt MVP 使用浏览器抓取今日榜单，不使用官方 API，因此不需要 token。

GitHub Trending MVP 抓网页 Daily 页面，不需要 GitHub token。

### 4.4 暂缓信息源

| 信息源 | 状态 |
|---|---|
| 微信公众号 | P1/P2，MVP 暂缓 |
| 付费数据库 | 暂不接入 |
| 招聘平台 | 暂不接入 |
| 工商 / 招投标 | 暂不接入 |
| 论文深度分析 | 后续 Research Agent |
| 私域信息 | 暂不接入 |

---

## 5. 抓取架构

### 5.1 三层抓取架构

采用三层抓取架构：

```text
第一层：API / RSS / Sitemap
第二层：HTTP + 正文抽取
第三层：Playwright Browser Worker
```

原则：

1. API / RSS / Sitemap 优先
2. HTTP + 正文抽取作为主力方案
3. Playwright 只作为兜底或少数动态页面专用 Worker
4. Browser Worker 不作为默认抓取方式
5. 不长期保存 DOM、截图、raw HTML

### 5.2 Source Registry 抓取字段

Source Registry 需要保留以下抓取策略字段：

| 字段 | 说明 |
|---|---|
| access_method | api / rss / sitemap / http / browser / auto |
| requires_js | 是否需要 JS 渲染 |
| crawl_risk | low / medium / high |
| list_page_limit | 每次最多扫描列表条数 |
| item_limit_per_run | 每次最多新增内容条数 |
| timeout_seconds | 抓取超时 |
| parser_config | 针对特定源的解析配置 |

### 5.3 信息源抓取策略

| 信息源 | 优先策略 |
|---|---|
| Hacker News | API |
| GitHub Trending | HTTP 页面解析，必要时 Browser |
| Product Hunt | Browser Worker |
| OpenAI / Anthropic | Sitemap / HTTP |
| 中文媒体 | HTTP + 正文抽取，必要时 Browser |

### 5.4 抓取频率

默认每天北京时间 10:00 自动抓取一次。

规则：

1. 时区固定使用 Asia/Shanghai
2. 如果程序 10:00 未启动，则当天首次启动且已过 10:00 时自动补抓一次
3. 用户可在系统设置中修改每日抓取时间
4. 支持手动触发今日抓取
5. 抓取失败不自动重试，只记录错误，支持手动重试

### 5.5 增量与过量抓取控制

不能依赖 crawl_time 作为发布时间判断增量。

时间字段原则：

| 字段 | 说明 |
|---|---|
| publish_time | 真实发布时间，抓不到则为空 |
| crawl_time | 本次抓取时间，永远记录 |
| display_time | 前端展示时使用 publish_time，否则 fallback 到 crawl_time |
| publish_time_status | exact / missing / estimated |

防止抓取过多的规则：

1. 每个 source 每次最多扫描前 N 条
2. 每个 source 每次最多新增 M 条
3. 连续遇到若干条已入库 canonical URL 后停止继续扫描
4. 对没有发布时间的源，不深翻分页
5. 每日汇总按“当天新入库内容”统计，而不是按 crawl_time 伪装 publish_time

---

## 6. 存储策略

### 6.1 长期保存内容

长期保存：

- title
- url
- canonical_url
- source
- publish_time
- crawl_time
- publish_time_status
- summary
- tags
- entities
- language
- word_count
- content_fingerprint
- extraction_status
- is_favorite
- archive metadata

### 6.2 不长期保存内容

不长期保存：

- 全文
- raw HTML
- 页面截图
- 图片
- 附件
- excerpt

### 6.3 短期缓存

`clean_text` 只短期缓存 48 小时。

用途：

- 当天查看
- LLM 生成 summary / tags / entities
- 人工校验

缓存过期后自动删除。

### 6.4 历史详情页逻辑

```text
打开历史内容
  ↓
如果 clean_text 缓存仍存在，则展示缓存正文
  ↓
如果缓存已过期，则展示 metadata / summary / tags / entities
  ↓
用户可点击原文链接或手动重新抓取
```

### 6.5 手动归档原文

保留“手动归档原文”操作。

只有用户点击归档后，系统才保存全文或原始内容到本地归档目录，并在数据库记录归档路径。

适用场景：

- 重要融资
- 官方重大发布
- 关键产品更新
- 后续可能需要用于投资 memo 的内容

---

## 7. 去重策略

### 7.1 P0 去重范围

| 去重层级 | P0 是否实现 |
|---|---|
| URL 去重 | 是 |
| canonical URL 去重 | 是 |
| 标题相似去重 | 是 |
| content fingerprint 去重 | 否，P1 |
| 同一事件多来源合并 | 否，P1 |

### 7.2 URL 规范化

需要处理：

1. 去除 UTM 参数
2. 去除分享参数
3. 统一 http / https
4. 统一移动端 / PC 端链接
5. GitHub repo 链接归一化
6. Product Hunt 产品链接归一化
7. 微信链接归一化预留，P0 暂不启用

---

## 8. LLM 与 Prompt

### 8.1 LLM 配置原则

LLM 配置独立保存，任务级配置从已有 LLM 配置中选择，不在每个任务里重复填写 API 信息。

支持：

- OpenAI
- Anthropic
- OpenAI-compatible API

### 8.2 LLM 配置字段

| 字段 | 说明 |
|---|---|
| config_name | 配置名称 |
| provider_type | openai / anthropic / openai_compatible |
| base_url | API 地址 |
| api_key | 加密保存 |
| model_name | 模型名称 |
| enabled | 是否启用 |
| timeout_seconds | 超时时间 |
| max_retries | 最大重试次数 |

`base_url`、`api_key`、`model_name` 本地加密保存。前端展示时脱敏。

本地加密密钥可由首次启动生成的 secret 文件或 `.env` 提供。

### 8.3 Prompt 管理

P0 支持保存多个 Prompt 模板。

P0 不做：

- Prompt 回滚
- Prompt 测试
- Prompt 评测

### 8.4 任务绑定

任务配置表绑定：

- `llm_config_id`
- `prompt_id`
- `task_name`
- `enabled`

### 8.5 P0 LLM 主任务

P0 不把 summary、entities、tags 拆成多次 LLM 调用，而是使用一个主任务：

`process_content_metadata`

输入：

- title
- source
- url
- publish_time
- clean_text 或榜单描述字段

输出 JSON：

```json
{
  "summary": "不超过100字的中文摘要",
  "ai_related": true,
  "content_type": "product_update",
  "sector_tags": ["AI Agent", "AI Coding"],
  "region_tags": ["US"],
  "entities": [
    {"type": "company", "name": "OpenAI"},
    {"type": "product", "name": "ChatGPT"}
  ]
}
```

### 8.6 LLM 不可用时降级

如果未配置 LLM 或 LLM 调用失败：

1. 内容仍然入库
2. summary 使用网页 meta description 或留空
3. tags 只使用规则标签
4. entities 为空
5. 状态标记为 `partial`
6. 页面上明确标识 LLM 不可用或 LLM 处理失败

### 8.7 默认基础 Prompt

```text
你是一个服务于中国 VC/PE AI 赛道投资经理的信息整理助手。

请阅读输入内容，输出结构化 JSON。不要输出投资建议、投资判断、项目评分或主观推荐。只做客观信息整理。

要求：
1. 用中文生成不超过 100 字的 summary。
2. 判断内容是否与 AI 相关，允许不确定。
3. 提取内容类型 content_type，只能从以下值中选择：
financing, product_update, research, model_release, company_news, repo, product_launch, discussion, other
4. 提取赛道标签 sector，可多选：
AI Infra, AI Agent, AI Coding, AI Search, AI Video, Robotics, LLM, AI Application, Semiconductor, Other
5. 提取地区 region，可多选：
China, US, Global, Other
6. 提取实体 entities，包括 company, product, investor, person, org。
7. 如果输入信息不足，不要编造，使用空数组或 null。

只输出 JSON，不要输出解释文字。
```

---

## 9. 标签与实体

### 9.1 基础标签

P0 内置基础标签，后续可编辑。

#### source_category

- venture_media
- ai_media
- tech_business_media
- official_news
- official_research
- github
- product_hunt
- hacker_news

#### content_type

- financing
- product_update
- research
- model_release
- company_news
- repo
- product_launch
- discussion
- other

#### sector

- AI Infra
- AI Agent
- AI Coding
- AI Search
- AI Video
- Robotics
- LLM
- AI Application
- Semiconductor
- Other

#### region

- China
- US
- Global
- Other

#### priority

- P0
- P1
- P2

`priority` 来自信息源优先级，不代表投资评分。

### 9.2 ai_related

保留 `ai_related` 字段，但 P0 不作为内容筛选硬条件。

该字段仅用于展示、标签和后续分析扩展。

### 9.3 标签生成方式

| 标签类型 | 生成方式 |
|---|---|
| 来源类别 | 规则 |
| source priority | 规则 |
| GitHub / Product Hunt / HN 类型 | 规则 |
| content_type | LLM |
| sector | LLM |
| region | LLM |

用户可人工修正标签。

### 9.4 实体设计

P0 做轻量实体表。

支持实体类型：

- company
- product
- investor
- person
- org

不做：

- 复杂实体图谱
- 自动主体合并
- 投资关系图谱

### 9.5 实体生成方式

实体主要由 LLM 提取，辅以少量规则。

用户可人工修正：

- 实体名称
- 实体类型
- 标准名
- 别名

---

## 10. 内容库

### 10.1 页面功能

内容库是查看、检索、修正信息的主页面。

P0 支持：

1. 内容列表
2. 按来源分类的子页面或子列表
3. 搜索
4. 筛选
5. 查看详情
6. 编辑内容
7. 重新抓取
8. 手动归档原文
9. 收藏

### 10.2 列表字段

| 字段 | 说明 |
|---|---|
| 标题 | title |
| 来源 | source_name |
| 来源类别 | source_category |
| 发布时间 / 抓取时间 | display_time |
| summary | 约 100 字摘要 |
| 标签 | key:value tags |
| 实体 | companies / products / investors / people / orgs |
| 解析状态 | extraction_status |
| LLM 状态 | llm_status |
| 收藏 | is_favorite |
| 操作 | 查看 / 编辑 / 重新抓取 / 归档原文 |

### 10.3 搜索范围

P0 搜索：

- title
- summary
- source
- entity name
- tag
- time

不搜索：

- 全文
- excerpt
- raw HTML

### 10.4 人工编辑字段

P0 支持人工编辑：

- title
- summary
- tags
- entities
- extraction_status
- is_favorite

### 10.5 收藏字段

收藏使用：

- `is_favorite`
- `favorited_at`

支持在内容库筛选收藏内容。

---

## 11. 每日汇总

### 11.1 默认生成方式

每日汇总默认不使用 LLM 生成正文。

默认流程：

```text
每日抓取完成
  ↓
按来源、类别、时间聚合当天新入库内容
  ↓
生成结构化 JSON 快照
  ↓
前端渲染
  ↓
支持 Markdown 导出
```

每日汇总是信息列表，不是投资日报，不输出投资观点。

### 11.2 汇总结构

```text
AI 信息收集汇总｜YYYY-MM-DD

一、今日抓取概览
二、中文媒体新增信息
三、海外官方新闻 / 研究更新
四、GitHub Trending Daily
五、Product Hunt 今日榜单
六、Hacker News Top 20
七、解析失败 / 需人工处理内容
```

每条内容展示：

- 标题
- 来源
- 时间
- summary
- 标签
- 实体
- 原文链接

### 11.3 汇总快照

每日汇总保存一份结构化、精炼快照。

建议字段：

| 字段 | 说明 |
|---|---|
| summary_date | 汇总日期 |
| generated_at | 生成时间 |
| source_counts | 各来源新增数量 |
| total_items | 总条数 |
| successful_items | 成功处理条数 |
| partial_items | partial 条数 |
| failed_items | 失败条数 |
| sections_json | 结构化分组内容 |
| markdown_text | 可选，导出时生成或缓存 |
| llm_summary_status | disabled / success / failed |
| llm_summary_text | 可选 LLM 汇总文本 |

### 11.4 手动重新生成

P0 支持手动生成 / 重新生成今日汇总按钮。

用途：

- 当天手动抓取后刷新汇总
- 人工编辑内容后刷新汇总
- LLM 汇总失败后手动重试

### 11.5 Markdown 导出

P0 支持每日汇总 Markdown 导出。

Markdown 由结构化快照模板渲染生成。

### 11.6 可选 LLM 每日汇总

系统保留每日汇总是否使用 LLM 的选项，默认关闭。

系统设置字段：

| 字段 | 默认值 | 说明 |
|---|---|---|
| daily_summary_use_llm | false | 是否使用 LLM 生成每日汇总文本 |
| daily_summary_llm_task | 空 | 选择 LLM 配置与 Prompt |
| daily_summary_max_items | 50 | 最多送入 LLM 的 item 数 |
| daily_summary_input_mode | structured_items | 只输入精炼结构化字段 |

可选 LLM 流程：

```text
先生成结构化 JSON 快照
  ↓
截取最多 N 条精炼 item
  ↓
调用 LLM 生成汇总文本
  ↓
保存为 daily_summary 的可选字段
```

限制：

1. 不传全文，只传标题、来源、summary、标签、实体
2. LLM 汇总不作为事实来源
3. LLM 失败不影响结构化汇总
4. UI 明确显示 LLM 汇总状态
5. 超过 max_items 时按分类截断，避免某一类内容占满输入

---

## 12. 页面结构

### 12.1 首页 / 今日概览

展示：

- 今日抓取状态
- 上次抓取时间
- 今日新增数量
- 失败源数量
- 最近每日汇总入口
- 手动触发今日抓取按钮

首页不显示当次运行短期日志。

### 12.2 信息源管理

功能：

- 新增信息源
- 编辑信息源
- 删除信息源
- 启用 / 停用
- 测试抓取
- 查看最近成功时间
- 查看连续失败次数

字段：

- name
- category
- url
- access_method
- priority
- frequency
- enabled
- include_keywords
- exclude_keywords
- login_required
- crawl_risk
- last_success_at
- error_count

删除信息源不删除历史内容。

### 12.3 内容库

包含：

- 主列表
- 按来源分类的子页面或子列表
- 搜索
- 筛选
- 查看详情
- 编辑
- 重新抓取
- 手动归档
- 收藏

推荐布局：

- 左侧来源分类
- 右侧内容列表

### 12.4 每日汇总

功能：

- 查看历史每日汇总快照
- 手动生成 / 重新生成今日汇总
- Markdown 导出
- 显示 LLM 汇总状态

### 12.5 标签与实体管理

P0 轻量版：

- 标签新增、编辑、停用
- 实体查看、编辑标准名、别名、类型

不做复杂合并工具。

### 12.6 LLM 与 Prompt 设置

功能：

- 新增 / 编辑 / 启用 / 停用 LLM 配置
- API key 脱敏展示
- 保存多个 Prompt
- 配置任务绑定：任务选择 LLM 配置和 Prompt

### 12.7 备份与系统设置

功能：

- 查看备份列表
- 手动创建备份
- 设置每日抓取时间
- 设置时区
- 设置缓存保留时间
- 设置是否启用 LLM 每日汇总
- 设置 LLM 每日汇总最大 item 数

P0 不做前台恢复功能。

---

## 13. 状态设计

### 13.1 内容状态

| 状态 | 说明 |
|---|---|
| new | 新入库 |
| partial | 部分解析成功或 LLM 不可用 |
| processed | 已完成 summary / tags / entities |
| failed | 抓取或解析失败 |
| archived | 已手动归档原文 |

### 13.2 LLM 状态

| 状态 | 说明 |
|---|---|
| not_configured | 未配置 LLM |
| pending | 等待处理 |
| success | 处理成功 |
| failed | 处理失败 |
| skipped | 无正文或无需处理 |

### 13.3 抓取运行状态

| 状态 | 说明 |
|---|---|
| running | 抓取中 |
| success | 抓取完成 |
| partial_success | 部分源失败 |
| failed | 抓取任务失败 |
| cancelled | 用户取消或系统中断 |

---

## 14. 数据模型

### 14.1 核心表

P0 核心表：

1. `sources`
2. `crawl_runs`
3. `crawl_errors`
4. `content_items`
5. `content_cache`
6. `tag_definitions`
7. `content_tags`
8. `entities`
9. `content_entities`
10. `llm_configs`
11. `prompts`
12. `llm_tasks`
13. `llm_logs`
14. `daily_summaries`
15. `system_settings`
16. `backups`
17. `long_term_logs`
18. `session_logs`

### 14.2 sources

| 字段 | 说明 |
|---|---|
| source_id | 主键 |
| source_name | 信息源名称 |
| source_category | 来源类别 |
| source_url | 抓取入口 |
| access_method | api / rss / sitemap / http / browser / auto |
| priority | P0 / P1 / P2 |
| crawl_frequency | daily / manual |
| enabled | 是否启用 |
| include_keywords | 包含关键词 |
| exclude_keywords | 排除关键词 |
| login_required | 是否需要登录 |
| paid_required | 是否需要付费 |
| requires_js | 是否需要 JS 渲染 |
| crawl_risk | low / medium / high |
| list_page_limit | 列表扫描上限 |
| item_limit_per_run | 单次新增上限 |
| timeout_seconds | 超时 |
| parser_config | 解析配置 |
| last_success_at | 最近成功时间 |
| error_count | 连续失败次数 |
| created_at | 创建时间 |
| updated_at | 更新时间 |

### 14.3 content_items

| 字段 | 说明 |
|---|---|
| content_id | 主键 |
| source_id | 来源 ID |
| title | 标题 |
| url | 原始 URL |
| canonical_url | 规范化 URL |
| source_name | 来源名冗余 |
| source_category | 来源类别冗余 |
| publish_time | 真实发布时间，可空 |
| crawl_time | 抓取时间 |
| publish_time_status | exact / missing / estimated |
| summary | 约 100 字摘要 |
| language | 语言 |
| word_count | 字数 |
| content_fingerprint | 内容指纹 |
| extraction_status | new / partial / processed / failed / archived |
| llm_status | not_configured / pending / success / failed / skipped |
| ai_related | true / false / null |
| full_content_cached | 是否存在短期正文缓存 |
| content_cache_until | 缓存到期时间 |
| full_content_saved | 是否手动归档 |
| archive_object_path | 归档路径 |
| is_favorite | 是否收藏 |
| favorited_at | 收藏时间 |
| created_at | 创建时间 |
| updated_at | 更新时间 |

### 14.4 content_cache

| 字段 | 说明 |
|---|---|
| content_id | 内容 ID |
| clean_text | 清洗正文 |
| cached_at | 缓存时间 |
| expire_at | 到期时间 |

### 14.5 tag_definitions

| 字段 | 说明 |
|---|---|
| tag_id | 标签 ID |
| tag_key | source_category / content_type / sector / region / priority |
| tag_value | 标签值 |
| display_name_cn | 中文展示名 |
| display_name_en | 英文展示名 |
| aliases | 别名 |
| parent_tag_id | 父标签 |
| enabled | 是否启用 |

### 14.6 content_tags

| 字段 | 说明 |
|---|---|
| content_id | 内容 ID |
| tag_id | 标签 ID |
| tag_key | 冗余字段，便于查询 |
| tag_value | 冗余字段，便于查询 |
| confidence | 置信度 |
| source | rule / llm / manual |

### 14.7 entities

| 字段 | 说明 |
|---|---|
| entity_id | 实体 ID |
| entity_type | company / product / investor / person / org |
| canonical_name | 标准名 |
| display_name | 展示名 |
| aliases | 别名 |
| enabled | 是否启用 |
| created_at | 创建时间 |
| updated_at | 更新时间 |

### 14.8 content_entities

| 字段 | 说明 |
|---|---|
| content_id | 内容 ID |
| entity_id | 实体 ID |
| confidence | 置信度 |
| source | rule / llm / manual |

### 14.9 daily_summaries

| 字段 | 说明 |
|---|---|
| summary_id | 主键 |
| summary_date | 日期 |
| generated_at | 生成时间 |
| total_items | 总条数 |
| successful_items | 成功条数 |
| partial_items | partial 条数 |
| failed_items | 失败条数 |
| source_counts_json | 各来源统计 |
| sections_json | 结构化汇总 |
| markdown_text | Markdown 缓存，可选 |
| llm_summary_status | disabled / success / failed |
| llm_summary_text | LLM 汇总文本，可选 |

---

## 15. 索引建议

至少需要：

```sql
CREATE UNIQUE INDEX idx_content_canonical_url ON content_items(canonical_url);
CREATE INDEX idx_content_publish_time ON content_items(publish_time);
CREATE INDEX idx_content_crawl_time ON content_items(crawl_time);
CREATE INDEX idx_content_source_id ON content_items(source_id);
CREATE INDEX idx_content_favorite ON content_items(is_favorite);
CREATE INDEX idx_content_status ON content_items(extraction_status);
CREATE INDEX idx_content_tag_key_value ON content_tags(tag_key, tag_value);
CREATE INDEX idx_content_tag_content_id ON content_tags(content_id);
CREATE INDEX idx_entity_type_name ON entities(entity_type, canonical_name);
CREATE INDEX idx_content_entity_content_id ON content_entities(content_id);
CREATE INDEX idx_content_entity_entity_id ON content_entities(entity_id);
CREATE INDEX idx_crawl_runs_source_time ON crawl_runs(source_id, started_at);
```

P1 可考虑 SQLite FTS5，对 title 和 summary 建全文索引。

---

## 16. 日志、错误与清理

### 16.1 抓取失败

抓取失败不自动重试。

系统需要：

1. 记录失败原因
2. 记录错误类型
3. 支持手动重试
4. 不让单个源失败影响整体抓取任务

### 16.2 错误日志

不做前台错误日志页面。

后台保存错误日志，字段建议：

- source_id
- crawl_run_id
- error_type
- error_message
- url
- occurred_at
- stack_trace 可选
- resolved
- manual_note

### 16.3 长期日志

长期日志只保存重大事件：

- 抓取批次结果
- 备份成功 / 失败
- Source 配置变更
- LLM 调用失败摘要
- 系统异常

### 16.4 短期日志

短期日志每次程序启动时刷新，只保存当次运行期间的详细操作记录：

- 服务启动
- 是否触发补抓
- 每个源抓取状态
- 解析状态
- 缓存清理
- 备份状态

首页不展示短期日志。

### 16.5 清理周期

| 数据 | 保留时间 |
|---|---|
| content_cache | 48 小时 |
| crawl_logs 明细 | 180 天 |
| llm_logs 明细 | 90 天 |
| session_logs | 每次启动清空 |

每天抓取后执行清理任务。

---

## 17. 备份

### 17.1 备份策略

P0 必须支持自动备份。

默认策略：

| 备份类型 | 保留策略 |
|---|---|
| 每日备份 | 最近 14 个 |
| 每周备份 | 最近 8 个 |
| 每月备份 | 12 个月或长期 |

默认备份目录：

```text
backups/
```

### 17.2 备份内容

备份内容包括：

- SQLite DB
- LLM 配置加密后的数据
- Source 配置
- Prompt 配置
- 手动归档文件目录，如存在

### 17.3 备份功能

P0 支持：

- 自动备份
- 手动触发备份
- 备份文件列表
- 备份后执行 SQLite integrity check

P0 不做前台恢复功能。

恢复机制可放 P1，或先提供后台操作预案。

---

## 18. 安全

### 18.1 API Key

API Key 必须本地加密保存。

要求：

- 不明文保存在数据库
- 前端只展示尾号
- 日志不得出现 API Key
- 备份不得明文暴露敏感配置

### 18.2 本地密钥

加密密钥方案：

- 首次启动生成本地 secret 文件，或
- 从 `.env` 读取

密钥文件不应进入版本控制。

### 18.3 日志脱敏

日志中不得出现：

- API Key
- Authorization header
- Cookie
- 敏感请求体

---

## 19. P0 功能清单

| 模块 | P0 功能 |
|---|---|
| 本地 Web | FastAPI 本地 Web 应用 |
| SQLite 主库 | SQLite + SQLAlchemy + WAL |
| 信息源管理 | 新增、编辑、删除、启用、停用、测试抓取 |
| 抓取任务 | 每日 10:00 自动抓取、错过补抓、手动抓取 |
| 抓取架构 | API / RSS / Sitemap、HTTP、Playwright Worker |
| 中文源 | 36氪、机器之心、量子位、晚点、投资界 |
| 海外源 | OpenAI News / Research、Anthropic News / Research |
| 社区榜单 | GitHub Trending Daily、Product Hunt 今日榜单、HN Top 20 |
| 正文策略 | clean_text 缓存 48 小时，不长期保存全文 |
| 摘要 | 约 100 字 summary |
| 去重 | URL / canonical URL / 标题相似去重 |
| 标签 | 内置基础标签，支持人工修正 |
| 实体 | 轻量 entities / content_entities |
| 内容库 | 列表、来源分类、搜索、筛选、详情、编辑、收藏 |
| 每日汇总 | 结构化快照、手动重新生成、Markdown 导出 |
| LLM 汇总选项 | 默认关闭，可手动开启 |
| LLM 配置 | 本地加密保存，可配置多个 |
| Prompt | 可保存多个 Prompt |
| 日志 | 长期重大日志 + 当次运行短期日志 |
| 备份 | 自动备份、手动备份、备份列表、integrity check |

---

## 20. P1 / P2 后续功能

### 20.1 P1

| 模块 | 功能 |
|---|---|
| 微信公众号 | 跑通公众号抓取或正文读取工具 |
| 恢复机制 | 前台从备份恢复 |
| 去重增强 | content fingerprint 去重 |
| 多来源合并 | 同一事件多来源合并 |
| FTS5 | title / summary 全文索引 |
| Prompt 测试 | Prompt 测试与输出预览 |
| Prompt 回滚 | Prompt 历史版本恢复 |
| Source 质量统计 | 有效率、失败率、重复率 |
| 数据清理管理 | 前台配置日志和缓存保留周期 |

### 20.2 P2

| 模块 | 功能 |
|---|---|
| 投资相关性评分 | 对信息做优先级排序 |
| 项目线索识别 | 自动识别潜在项目公司 |
| 投资日报 | 从信息库生成投资人可读日报 |
| 赛道周报 | 趋势分析 |
| Research Agent | 论文、报告、公告深度分析 |
| PostgreSQL 迁移 | 团队化或大规模数据后迁移 |
| 内部知识库 | 接入 FA 邮件、项目库、会议纪要 |

---

## 21. 验收标准

MVP 验收标准：

1. 能启动本地 Web 应用
2. 能在浏览器使用主要页面
3. 能新增、编辑、删除、启用、停用信息源
4. 能测试抓取单个信息源
5. 能按北京时间每天 10:00 自动抓取
6. 程序错过 10:00 后，当天首次启动能自动补抓
7. 能跑通 5 个中文源
8. 能跑通 4 个海外官方源
9. 能抓取 GitHub Trending Daily
10. 能抓取 Product Hunt 今日榜单
11. 能抓取 Hacker News Top 20
12. 内容能入库并按 canonical URL 去重
13. 能生成约 100 字 summary
14. 能生成基础标签
15. 能生成轻量实体
16. LLM 不可用时内容仍可入库，并明确标识状态
17. 内容库支持搜索、筛选、按来源查看
18. 内容支持人工编辑
19. 内容支持收藏
20. 每日汇总能生成结构化快照
21. 每日汇总支持手动重新生成
22. 每日汇总支持 Markdown 导出
23. LLM 配置能保存并加密
24. Prompt 能保存多个
25. 自动备份能运行
26. 不长期保存全文、raw HTML、图片、网页快照、excerpt

---

## 22. 关键产品原则

1. 先稳定信息底座，再做分析能力。
2. 信息源必须可编辑，不写死在代码中。
3. Playwright 是兜底，不是默认抓取方式。
4. crawl_time 不等于 publish_time。
5. 每日汇总默认由程序模板生成，不依赖 LLM。
6. LLM 只处理单条内容，避免每日汇总上下文过长。
7. 每日汇总 LLM 文本是可选增强，不是事实来源。
8. 不长期保存全文，降低版权风险、容量压力和备份压力。
9. SQLite 适合个人版，但必须控制写入并发。
10. 没有备份，不应进入长期使用。

