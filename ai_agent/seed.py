from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import LLMConfig, LLMTask, Prompt, Source, SystemSetting, TagDefinition


DEFAULT_PROMPT = """你是一个服务于中国 VC/PE AI 赛道投资经理的信息整理助手。

请阅读输入内容，输出结构化 JSON。不要输出投资建议、投资判断、项目评分或主观推荐。只做客观信息整理。

要求：
1. 用中文生成不超过 180 字的 summary，尽量表达完整，不要在半句话处结束。
2. 判断内容是否与 AI 相关，允许不确定。
3. 提取内容类型 content_type，只能从以下值中选择：
financing, product_update, research, model_release, company_news, repo, product_launch, discussion, other
4. 提取赛道标签 sector，可多选：
AI Infra, AI Agent, AI Coding, AI Search, AI Video, Robotics, LLM, AI Application, Semiconductor, Other
5. 提取地区 region，可多选：
China, US, Global, Other
6. 提取实体 entities，包括 company, product, investor, person, org。
7. 如果输入信息不足，不要编造，使用空数组或 null。

只输出 JSON，不要输出解释文字。"""


CURRENT_WEEK_FINANCING_REPORT_PROMPT = """你是一名服务于 VC/PE 投资团队的融资新闻研究员和报告编辑。

输入 JSON 包含 period、raw_news_count、input_truncated 和 news_items。请仅基于这些输入，将本周融资新闻整理成一份中文 Markdown 报告。

时间范围：
1. period.start_date 是本周周一。
2. period.end_date 是程序运行当日。
3. 只处理该日期区间内发生或正式公布的融资事件。

数据处理：
1. 排除融资传闻、计划融资、历史融资回顾、二级市场交易、产品发布、合作签约及其他非融资事件。
2. 同一公司、同一轮次、同一时间段的多篇报道应合并为一个项目。
3. 对照多篇相关新闻核验公司、轮次、金额、投资人和融资时间。信息一致时合并；发生冲突时填写“待核验”并在简介中说明冲突。
4. 融资轮次、融资金额或投资人没有明确披露时填写“未披露”，不得推测或使用外部知识补全。
5. 只能使用 news_items 中提供的事实和 URL，不得编造公司信息、交易信息或链接。
6. 如果 input_truncated=true，在报告开头注明“输入新闻数量超过处理上限，本报告仅基于已提供材料”。

地区分类：
1. 报告必须按照“国内”和“海外”划分为两个大栏目。
2. 中国大陆公司归入“国内”，其他国家或地区的公司归入“海外”。
3. 优先根据输入中的地区标签、注册地、总部或主要经营主体判断；证据不足时不得仅凭公司名称猜测，并在简介中标注“地区归属待核验”。

每个项目严格使用以下格式：

### 序号. 公司名称

- 融资公司：公司正式名称
- 融资轮次：具体轮次或“未披露”
- 融资金额：金额及币种或“未披露”
- 投资人：领投方、跟投方；未披露时填写“未披露”
- 融资时间：正式公布日期或“未披露”
- 信息来源：
  - 相关新闻原文 URL
  - 相关新闻原文 URL

融资简介：
使用一段 100—180 字的中文介绍本次融资和公司情况，包括公司主要业务、产品或技术方向、本轮融资核心事实及已明确披露的资金用途。只写事实，不作投资建议、价值判断或主观评价。

信息来源规则：
1. 逐行列出该项目对应相关新闻的原文 URL。
2. 只能使用输入 news_items 中实际存在的 url。
3. URL 去重后原样输出，不得改写、缩短、生成新链接或使用 Markdown 超链接格式。

输出结构：

# 本周融资报告

统计期间：period.start_date 至 period.end_date

本周共收录 X 起融资事件，其中国内 X 起、海外 X 起。

在详细项目前，先输出以下汇总表格。表格每行对应一个融资项目；如果某项信息在输入新闻中没有明确披露，则填写“NA”，不得推测：

| 项目名称 | 融资金额 | 融资估值 | 融资轮次 | 项目简介 |
|---|---|---|---|---|
| 公司A | ... | ... | ... | ... |

项目简介只需介绍公司业务、产品或技术方向，无需介绍本轮融资核心事实，控制在50字以内。

## 国内

按上述项目格式输出；没有项目时写“本周暂无经核验的国内融资事件”。

## 海外

按上述项目格式输出；没有项目时写“本周暂无经核验的海外融资事件”。

只输出最终 Markdown 报告，不要输出分析过程、JSON、代码围栏或额外解释。"""


PREVIOUS_WEEK_FINANCING_REPORT_PROMPT = """你是一名服务于 VC/PE 投资团队的融资新闻研究员和报告编辑。

输入 JSON 包含 period、raw_news_count、input_truncated 和 news_items。请仅基于这些输入，将上周融资新闻整理成一份中文 Markdown 报告。

时间范围：
1. period.start_date 是上一自然周周一。
2. period.end_date 是上一自然周周日。
3. 只处理该日期区间内发生或正式公布的融资事件。

数据处理：
1. 排除融资传闻、计划融资、历史融资回顾、二级市场交易、产品发布、合作签约及其他非融资事件。
2. 同一公司、同一轮次、同一时间段的多篇报道应合并为一个项目。
3. 对照多篇相关新闻核验公司、轮次、金额、投资人和融资时间。信息一致时合并；发生冲突时填写“待核验”并在简介中说明冲突。
4. 融资轮次、融资金额或投资人没有明确披露时填写“未披露”，不得推测或使用外部知识补全。
5. 只能使用 news_items 中提供的事实和 URL，不得编造公司信息、交易信息或链接。
6. 如果 input_truncated=true，在报告开头注明“输入新闻数量超过处理上限，本报告仅基于已提供材料”。

地区分类：
1. 报告必须按照“国内”和“海外”划分为两个大栏目。
2. 中国大陆公司归入“国内”，其他国家或地区的公司归入“海外”。
3. 优先根据输入中的地区标签、注册地、总部或主要经营主体判断；证据不足时不得仅凭公司名称猜测，并在简介中标注“地区归属待核验”。

每个项目严格使用以下格式：

### 序号. 公司名称

- 融资公司：公司正式名称
- 融资轮次：具体轮次或“未披露”
- 融资金额：金额及币种或“未披露”
- 投资人：领投方、跟投方；未披露时填写“未披露”
- 融资时间：正式公布日期或“未披露”
- 信息来源：
  - 相关新闻原文 URL
  - 相关新闻原文 URL

融资简介：
使用一段 100—180 字的中文介绍本次融资和公司情况，包括公司主要业务、产品或技术方向、本轮融资核心事实及已明确披露的资金用途。只写事实，不作投资建议、价值判断或主观评价。

信息来源规则：
1. 逐行列出该项目对应相关新闻的原文 URL。
2. 只能使用输入 news_items 中实际存在的 url。
3. URL 去重后原样输出，不得改写、缩短、生成新链接或使用 Markdown 超链接格式。

输出结构：

# 上周融资报告

统计期间：period.start_date 至 period.end_date

上周共收录 X 起融资事件，其中国内 X 起、海外 X 起。

在详细项目前，先输出以下汇总表格。表格每行对应一个融资项目；如果某项信息在输入新闻中没有明确披露，则填写“NA”，不得推测：

| 项目名称 | 融资金额 | 融资估值 | 融资轮次 | 项目简介 |
|---|---|---|---|---|
| 公司A | ... | ... | ... | ... |

项目简介只需介绍公司业务、产品或技术方向，无需介绍本轮融资核心事实，控制在50字以内。

## 国内

按上述项目格式输出；没有项目时写“上周暂无经核验的国内融资事件”。

## 海外

按上述项目格式输出；没有项目时写“上周暂无经核验的海外融资事件”。

只输出最终 Markdown 报告，不要输出分析过程、JSON、代码围栏或额外解释。"""


DEFAULT_PROMPTS = {
    "generate_current_week_financing_report": (
        "默认 - 本周融资总结",
        CURRENT_WEEK_FINANCING_REPORT_PROMPT,
    ),
    "generate_previous_week_financing_report": (
        "默认 - 上周融资总结",
        PREVIOUS_WEEK_FINANCING_REPORT_PROMPT,
    ),
    "process_content_metadata": (
        "默认 - 单条内容综合处理",
        """你是一个服务于中国 VC/PE AI 赛道投资经理的信息整理助手。输入 JSON 中的 text 是新闻原文或原文主体截取。

请阅读输入内容，输出结构化 JSON。不要输出投资建议、投资判断、项目评分或主观推荐，只做客观信息整理。

字段要求：
1. summary：中文，不超过 180 字，概括事实和核心变化，尽量表达完整，不要在半句话处结束。
2. ai_related：true / false / null，判断内容是否与 AI 相关；证据不足时用 null。
3. content_type：只能从 financing, product_update, research, model_release, company_news, repo, product_launch, discussion, other 中选择。
4. sector_tags：数组，可选 AI Infra, AI Agent, AI Coding, AI Search, AI Video, Robotics, LLM, AI Application, Semiconductor, Other。
5. region_tags：数组，可选 China, US, Global, Other。
6. entities：数组，每项为 {"type": "company|product|investor|person|org", "name": "..."}。

只输出 JSON，不要输出解释文字。""",
    ),
    "classify_ai_financing_relevance": (
        "默认 - AI融资高相关筛选",
        """你是一个面向中国 VC/PE 投资经理的 AI 投融资新闻严格筛选器。输入 JSON 中的 text 是新闻原文或原文主体截取；metadata 是上游综合处理给出的初步标签，仅供参考，最终必须以原文事实为准。

目标：判断这篇新闻是否属于“高度相关的 AI 投融资新闻”。

判定规则：
1. ai_related=true：被投企业、被收购标的或基金的核心业务/明确投资方向是 AI，包括大模型、AI Infra、Agent、AI 应用、具身智能、自动驾驶、AI 芯片等。仅出现“智能”“算法”“自动化”或把 AI 当作普通功能点，不足以判定为 true。
2. content_type=financing：新闻的核心事件必须是已经宣布或完成的股权融资、战略投资、并购收购，或明确面向 AI 的基金募集/关账。历史融资背景、融资传闻、计划融资、市场评论、产品发布、订单、合作、人事和经营动态不能标为 financing。
3. ai_financing_relevance=high：必须同时满足 ai_related=true、content_type=financing，且原文包含明确交易证据，例如标的公司/基金、轮次或交易类型、金额、投资方、宣布时间中的至少两项；投融资事件必须是文章主线，而不是顺带提及。
4. 不满足上述全部条件时，ai_financing_relevance 必须为 none。宁可漏掉边缘信息，也不要把无关内容标为 high。
5. 以下情况必须排除：普通行业公司的融资仅附带 AI 概念；文章只回顾过往融资；泛基金/募资观点；二级市场涨跌；未经证实的传闻；榜单盘点；核心事件不是真实投融资交易。

输出字段：
1. ai_related：true / false / null。
2. content_type：只能从 financing, product_update, research, model_release, company_news, repo, product_launch, discussion, other 中选择。
3. ai_financing_relevance：只能是 high 或 none。
4. financing_reason：不超过 80 字，说明判定依据；非高相关时说明最主要的排除原因。
5. sector_tags：数组，可选 AI Infra, AI Agent, AI Coding, AI Search, AI Video, Robotics, LLM, AI Application, Semiconductor, Other。
6. entities：数组，每项为 {"type": "company|product|investor|person|org", "name": "..."}。

只输出 JSON，不要输出解释文字。""",
    ),
    "summarize_content": (
        "默认 - 单条内容 Summary",
        """你是一个 AI 投资情报摘要助手。

请基于输入内容生成中文 summary，服务于快速浏览信息流。

要求：
1. 只写事实，不写投资建议、评分或主观推荐。
2. 控制在 180 字以内，尽量表达完整，不要在半句话处结束。
3. 优先保留公司/产品/模型/融资/研究结论/关键数据。
4. 如果信息不足，直接基于标题和已有文本做谨慎概括，不要编造。

输出 JSON：{"summary": "..."}""",
    ),
    "classify_ai_related": (
        "默认 - AI 相关性判断",
        """你是一个 AI 赛道内容筛选助手。

请判断输入内容是否与 AI 相关。AI 相关包括模型、算力、AI Infra、Agent、AI 应用、机器人、AI 编程、AI 搜索、AI 视频、半导体与 AI 产业公司动态。

要求：
1. 明确相关输出 true。
2. 明确无关输出 false。
3. 信息不足或标题党无法判断输出 null。
4. 不要因为来源是科技媒体就默认相关。

输出 JSON：{"ai_related": true|false|null, "reason": "不超过30字的判断依据"}""",
    ),
    "tag_content": (
        "默认 - 动态标签打标",
        """你是一个 AI 投资内容标签助手。

请为输入内容生成结构化标签，便于后续检索和统计。

要求：
1. content_type 只能从 financing, product_update, research, model_release, company_news, repo, product_launch, discussion, other 中选择。
2. sector_tags 可多选：AI Infra, AI Agent, AI Coding, AI Search, AI Video, Robotics, LLM, AI Application, Semiconductor, Other。
3. region_tags 可多选：China, US, Global, Other。
4. 只根据文本证据打标签，不确定时少打，不要补想象。

输出 JSON：{"content_type": "...", "sector_tags": [], "region_tags": []}""",
    ),
    "extract_entities": (
        "默认 - 实体识别",
        """你是一个 AI 投资情报实体抽取助手。

请从输入内容中识别实体，供内容库聚合使用。

实体类型只允许：
company, product, investor, person, org

要求：
1. 只抽取文本中明确出现的实体。
2. 公司、产品、模型、投资机构、研究组织、人物要尽量保留原文名称。
3. 不要抽取过于泛化的词，如 AI、模型、用户、公司。
4. 没有实体时返回空数组。

输出 JSON：{"entities": [{"type": "...", "name": "..."}]}""",
    ),
    "classify_content_type": (
        "默认 - 内容类型分类",
        """你是一个科技投资内容分类助手。

请判断输入内容的主要类型。只能选择一个最主要类型：
financing, product_update, research, model_release, company_news, repo, product_launch, discussion, other

判定参考：
1. 融资、并购、基金、投资事件：financing。
2. 产品发布、功能更新、商业化进展：product_update。
3. 论文、技术报告、研究成果：research。
4. 新模型、模型能力升级、benchmark：model_release。
5. 公司经营、人事、合作、政策回应：company_news。
6. GitHub 仓库：repo。
7. Product Hunt 产品榜单：product_launch。
8. HN/社区讨论：discussion。

输出 JSON：{"content_type": "..."}""",
    ),
    "normalize_title": (
        "默认 - 标题规范化",
        """你是一个内容标题清洗助手。

请清洗输入标题，使其适合在投资情报列表中展示。

要求：
1. 删除站点后缀、重复来源名、导航噪声、榜单序号和多余空白。
2. 保留公司名、产品名、模型名、核心事件。
3. 不改变事实含义，不增加原文没有的信息。
4. 中文标题保持中文，英文标题保持英文。
5. 控制在 60 字以内。

输出 JSON：{"title": "..."}""",
    ),
    "product_hunt_classifier": (
        "默认 - Product Hunt 条目处理",
        """你是一个 Product Hunt 今日榜单整理助手。

请基于产品名称、tagline 和可见描述，为单个 Product Hunt 条目生成适合 AI 投资情报库的结构化信息。

要求：
1. summary：中文，不超过 180 字，说明产品做什么、面向谁、核心功能，尽量表达完整，不要在半句话处结束。
2. ai_related：判断是否明显 AI 相关；不确定用 null。
3. sector_tags：可选 AI Agent, AI Coding, AI Search, AI Video, AI Application, AI Infra, Other。
4. entities：至少尝试抽取 product；公司名不明确时不要编造。
5. 不要判断投资价值。

输出 JSON：{"summary": "...", "ai_related": true|false|null, "sector_tags": [], "entities": []}""",
    ),
    "hn_classifier": (
        "默认 - Hacker News 条目处理",
        """你是一个 Hacker News Top 20 内容整理助手。

请基于 HN 标题和可见文本，为社区讨论条目生成轻量结构化信息。

要求：
1. summary：中文，不超过 180 字，概括讨论对象和可能的信息价值，尽量表达完整，不要在半句话处结束。
2. ai_related：判断是否与 AI/开发者工具/技术基础设施相关，不确定用 null。
3. content_type：通常为 discussion；如果明显是 repo、research、model_release，可按事实改写。
4. sector_tags：仅在有明确技术方向时填写。
5. entities：抽取明确出现的公司、产品、项目或组织。

输出 JSON：{"summary": "...", "ai_related": true|false|null, "content_type": "...", "sector_tags": [], "entities": []}""",
    ),
    "daily_summary_llm": (
        "默认 - 每日汇总 LLM 文本",
        """你是一个面向中国 VC/PE AI 赛道投资经理的每日情报汇总助手。

输入是当天结构化内容快照。请生成中文每日汇总文本。

要求：
1. 只基于输入事实，不编造、不补外部信息。
2. 按主题组织：投融资/公司动态、模型与研究、产品与应用、开发者与社区。
3. 每条尽量保留来源、公司/产品/模型名称和关键变化。
4. 不输出投资建议、评分或推荐。
5. 如果输入为空，明确说明今日暂无可汇总内容。

输出 Markdown，结构简洁，适合直接阅读。""",
    ),
}


SOURCES = [
    ("36氪", "venture_media", "https://36kr.com/", "http", False, "medium"),
    ("36氪创投", "venture_media", "https://36kr.com/information/contact/", "http", False, "medium"),
    ("36氪AI", "ai_media", "https://36kr.com/information/AI/", "http", False, "medium"),
    ("创业邦资讯", "venture_media", "https://www.cyzone.cn/channel/news", "http", False, "low"),
    ("猎云网融资汇", "venture_media", "https://www.lieyunpro.com/archives", "browser", True, "medium"),
    ("投中网", "venture_media", "https://www.chinaventure.com.cn/", "http", False, "low"),
    ("财联社创投通", "venture_media", "https://api3.cls.cn/share/subject/9050?os=ios&sv=736", "http", False, "low"),
    ("钛媒体创投", "venture_media", "https://www.tmtpost.com/column/5994956", "http", False, "low"),
    ("亿欧中文", "venture_media", "https://www.iyiou.com/news", "browser", True, "medium"),
    ("医药魔方 ByDrug", "venture_media", "https://bydrug.pharmcube.com/", "http", False, "low"),
    ("SVTR AI创投日报", "venture_media", "https://svtr.ai/", "http", False, "low"),
    ("甲子光年", "venture_media", "https://www.jazzyear.com/", "http", False, "low"),
    ("a16z AI", "venture_media", "https://a16z.com/ai/", "http", False, "low"),
    ("量子位", "ai_media", "https://www.qbitai.com/", "http", False, "low"),
    ("晚点 LatePost", "tech_business_media", "https://www.latepost.com/", "http", False, "medium"),
    ("投资界 / PEDaily", "venture_media", "https://www.pedaily.cn/", "http", False, "low"),
    ("OpenAI News", "official_news", "https://openai.com/news/", "http", False, "low"),
    ("OpenAI Research", "official_research", "https://openai.com/research/", "http", False, "low"),
    ("Anthropic News", "official_news", "https://www.anthropic.com/news", "http", False, "low"),
    ("Anthropic Research", "official_research", "https://www.anthropic.com/research", "http", False, "low"),
    ("TechCrunch AI", "ai_media", "https://techcrunch.com/category/artificial-intelligence/", "http", False, "low"),
    ("The Verge AI", "ai_media", "https://www.theverge.com/ai-artificial-intelligence", "http", False, "low"),
    ("VentureBeat AI", "ai_media", "https://venturebeat.com/category/ai/", "http", False, "low"),
    ("The Decoder", "ai_media", "https://the-decoder.com/", "http", False, "low"),
    ("Crunchbase News AI", "ai_media", "https://news.crunchbase.com/sections/ai/", "http", False, "low"),
    ("Hugging Face Daily Papers", "ai_research_signal", "https://huggingface.co/papers", "http", False, "low"),
    ("Hugging Face Spaces Trending", "ai_product_signal", "https://huggingface.co/spaces?sort=trending", "http", False, "low"),
    ("Google DeepMind Blog", "official_research", "https://deepmind.google/blog/", "http", False, "low"),
    ("Meta AI Blog", "official_research", "https://ai.meta.com/blog/", "http", False, "medium"),
    ("YC AI Companies", "startup_directory", "https://www.ycombinator.com/companies/industry/artificial-intelligence", "http", False, "low"),
    ("GitHub Trending Daily", "github", "https://github.com/trending?since=daily", "http", False, "low"),
    ("Product Hunt Today", "product_hunt", "https://www.producthunt.com/", "browser", True, "medium"),
    ("Hacker News Top 20", "hacker_news", "https://hacker-news.firebaseio.com/v0/topstories.json", "api", False, "low"),
]


def source_limits(name: str, category: str) -> tuple[int, int]:
    if name in {"36氪创投", "36氪AI"}:
        return 10, 30
    if name == "a16z AI":
        return 10, 20
    if name in {
        "TechCrunch AI",
        "The Verge AI",
        "VentureBeat AI",
        "The Decoder",
        "Crunchbase News AI",
    }:
        return 10, 30
    if category in {"venture_media", "tech_business_media"}:
        return 10, 120
    if category == "ai_media":
        return 10, 80
    if category in {"ai_research_signal", "ai_product_signal"}:
        return 10, 80
    if category == "startup_directory":
        return 20, 100
    if category in {"official_news", "official_research"}:
        return 5, 50
    if category == "github":
        return 10, 30
    if category == "hacker_news":
        return 20, 50
    return 3, 20


SOURCE_ENABLED_DEFAULTS = {
    "OpenAI News": False,
    "OpenAI Research": False,
    "Anthropic News": False,
    "Meta AI Blog": False,
    "Google DeepMind Blog": False,
    "财联社创投通": False,
    # SVTR 当前不参与每日自动抓取，保留为可编辑的信息源配置。
    "SVTR AI创投日报": False,
    # 猎云网公开页当前有 Cloudflare/浏览器校验，先接入为可编辑源但不参与每日自动抓取。
    "猎云网融资汇": False,
    # 亿欧中文站当前返回浏览器验证页，需要后续 Browser Worker 才适合参与自动抓取。
    "亿欧中文": False,
    # YC AI 公司库更适合按需/低频看项目池，默认只入配置，不参与每日自动抓取。
    "YC AI Companies": False,
}


SOURCE_LEGACY_NAMES = {
    "创业邦资讯": ["创业邦融资"],
    "亿欧中文": ["EqualOcean AI"],
}


TAGS = {
    "source_category": [
        "venture_media",
        "ai_media",
        "tech_business_media",
        "official_news",
        "official_research",
        "ai_research_signal",
        "ai_product_signal",
        "startup_directory",
        "github",
        "product_hunt",
        "hacker_news",
    ],
    "content_type": [
        "financing",
        "product_update",
        "research",
        "model_release",
        "company_news",
        "repo",
        "product_launch",
        "discussion",
        "other",
    ],
    "ai_financing_relevance": ["high", "none"],
    "sector": [
        "AI Infra",
        "AI Agent",
        "AI Coding",
        "AI Search",
        "AI Video",
        "Robotics",
        "LLM",
        "AI Application",
        "Semiconductor",
        "Other",
    ],
    "region": ["China", "US", "Global", "Other"],
    "priority": ["P0", "P1", "P2"],
}


SETTINGS = {
    "timezone": "Asia/Shanghai",
    "daily_crawl_time": "10:00",
    "content_cache_hours": "48",
    "daily_summary_use_llm": "false",
    "daily_summary_max_items": "50",
    "daily_summary_input_mode": "structured_items",
    "source_parallelism": "4",
    "llm_parallelism": "2",
    "weekly_financing_report_dir": "data/reports",
    "network_proxy_mode": "off",
    "network_proxy_url": "",
    "network_proxy_no_proxy": "localhost,127.0.0.1,::1",
    "last_auto_crawl_date": "",
}


LLM_TASKS = [
    (
        "generate_current_week_financing_report",
        "本周融资总结",
        "融资新闻页“本周总结”使用；按国内/海外整理本周一至当日的融资事件并生成 Markdown。",
    ),
    (
        "generate_previous_week_financing_report",
        "上周融资总结",
        "融资新闻页“上周总结”使用；按国内/海外整理上一自然周的融资事件并生成 Markdown。",
    ),
    (
        "process_content_metadata",
        "单条内容综合处理",
        "生成 180 字以内 summary，并同时返回 AI 相关性、内容类型、赛道、地区和实体。",
    ),
    (
        "classify_ai_financing_relevance",
        "AI融资高相关筛选",
        "基于新闻原文严格判断是否为高相关 AI 投融资；融资页只展示 high。",
    ),
    (
        "summarize_content",
        "单条内容 Summary",
        "仅生成单条内容的 180 字以内中文简介。当前 P0 可由综合处理任务覆盖。",
    ),
    (
        "classify_ai_related",
        "AI 相关性判断",
        "判断内容是否与 AI 相关；文章类综合媒体会将其作为入库筛选依据。",
    ),
    (
        "tag_content",
        "动态标签打标",
        "为内容生成 content_type、sector、region 等 key:value 标签。",
    ),
    (
        "extract_entities",
        "实体识别",
        "识别公司、产品、投资机构、人物、组织等轻量实体。",
    ),
    (
        "classify_content_type",
        "内容类型分类",
        "判断内容属于融资、产品更新、研究、模型发布、讨论等类型。",
    ),
    (
        "normalize_title",
        "标题规范化",
        "清洗标题中的站点后缀、日期噪声和列表页拼接文本。",
    ),
    (
        "product_hunt_classifier",
        "Product Hunt 条目处理",
        "处理 Product Hunt 今日榜单条目，可用于产品类别和简介优化。",
    ),
    (
        "hn_classifier",
        "Hacker News 条目处理",
        "处理 Hacker News Top 20，可用于简介、标签和实体补全。",
    ),
    (
        "daily_summary_llm",
        "每日汇总 LLM 文本",
        "可选任务。基于结构化快照生成每日汇总文本，默认关闭。",
    ),
]


def ensure_llm_defaults(db: Session) -> bool:
    """Add missing built-in prompts/tasks without overwriting user prompt edits."""
    changed = False

    legacy_prompt = db.scalar(select(Prompt).where(Prompt.prompt_name == "默认内容元数据处理 Prompt"))
    if not legacy_prompt:
        legacy_prompt = Prompt(
            prompt_name="默认内容元数据处理 Prompt",
            task_name="process_content_metadata",
            prompt_text=DEFAULT_PROMPT,
            enabled=True,
        )
        db.add(legacy_prompt)
        db.flush()
        changed = True

    default_prompt_by_task: dict[str, Prompt] = {}
    for task_name, (prompt_name, prompt_text) in DEFAULT_PROMPTS.items():
        prompt = db.scalar(select(Prompt).where(Prompt.prompt_name == prompt_name))
        if not prompt:
            prompt = Prompt(
                prompt_name=prompt_name,
                task_name=task_name,
                prompt_text=prompt_text,
                enabled=True,
            )
            db.add(prompt)
            db.flush()
            changed = True
        elif prompt.task_name != task_name:
            prompt.task_name = task_name
            changed = True
        default_prompt_by_task[task_name] = prompt

    default_llm_config_id = db.scalar(
        select(LLMTask.llm_config_id).where(LLMTask.task_name == "process_content_metadata")
    )
    if not default_llm_config_id:
        default_llm_config_id = db.scalar(
            select(LLMConfig.llm_config_id)
            .where(LLMConfig.enabled.is_(True))
            .order_by(LLMConfig.llm_config_id)
        )

    report_tasks = {
        "classify_ai_financing_relevance",
        "generate_current_week_financing_report",
        "generate_previous_week_financing_report",
    }
    for task_name, _, _ in LLM_TASKS:
        existing_task = db.scalar(select(LLMTask).where(LLMTask.task_name == task_name))
        default_prompt = default_prompt_by_task.get(task_name, legacy_prompt)
        if not existing_task:
            db.add(
                LLMTask(
                    task_name=task_name,
                    llm_config_id=default_llm_config_id if task_name in report_tasks else None,
                    prompt_id=default_prompt.prompt_id,
                    enabled=True,
                )
            )
            changed = True
            continue

        current_prompt = db.get(Prompt, existing_task.prompt_id) if existing_task.prompt_id else None
        if (
            not current_prompt
            or current_prompt.prompt_id == legacy_prompt.prompt_id
            or current_prompt.task_name != task_name
        ):
            existing_task.prompt_id = default_prompt.prompt_id
            changed = True
        if task_name in report_tasks and not existing_task.llm_config_id and default_llm_config_id:
            existing_task.llm_config_id = default_llm_config_id
            changed = True

    return changed



def seed_all(db: Session) -> None:
    for key, value in SETTINGS.items():
        existing = db.get(SystemSetting, key)
        if not existing:
            db.add(SystemSetting(setting_key=key, setting_value=value))

    for name, category, url, method, requires_js, risk in SOURCES:
        existing = db.scalar(select(Source).where(Source.source_name == name))
        migrated_from_legacy = False
        if not existing:
            for legacy_name in SOURCE_LEGACY_NAMES.get(name, []):
                existing = db.scalar(select(Source).where(Source.source_name == legacy_name))
                if existing:
                    existing.source_name = name
                    migrated_from_legacy = True
                    break
        item_limit, list_limit = source_limits(name, category)
        if not existing:
            db.add(
                Source(
                    source_name=name,
                    source_category=category,
                    source_url=url,
                    access_method=method,
                    requires_js=requires_js,
                    crawl_risk=risk,
                    priority="P0",
                    crawl_frequency="daily",
                    enabled=SOURCE_ENABLED_DEFAULTS.get(name, True),
                    list_page_limit=list_limit,
                    item_limit_per_run=item_limit,
                    timeout_seconds=25,
                )
            )
        else:
            if name == "36氪":
                existing.source_category = "venture_media"
            if name == "创业邦资讯" and migrated_from_legacy:
                existing.source_url = url
                existing.source_category = category
                existing.access_method = method
                existing.requires_js = requires_js
                existing.crawl_risk = risk
            if name == "财联社创投通" and "searchPage" in existing.source_url:
                existing.source_url = url
                existing.access_method = method
                existing.requires_js = requires_js
                existing.crawl_risk = risk
            if name == "猎云网融资汇" and existing.access_method == "http":
                existing.access_method = method
                existing.requires_js = requires_js
                existing.crawl_risk = risk
                existing.enabled = SOURCE_ENABLED_DEFAULTS.get(name, existing.enabled)
            if name == "亿欧中文" and (migrated_from_legacy or "equalocean.com" in existing.source_url):
                existing.source_url = url
                existing.access_method = method
                existing.requires_js = requires_js
                existing.crawl_risk = risk
                existing.enabled = SOURCE_ENABLED_DEFAULTS.get(name, existing.enabled)
            existing.item_limit_per_run = item_limit
            existing.list_page_limit = list_limit

    for tag_key, values in TAGS.items():
        for value in values:
            existing = db.scalar(
                select(TagDefinition).where(TagDefinition.tag_key == tag_key, TagDefinition.tag_value == value)
            )
            if not existing:
                db.add(
                    TagDefinition(
                        tag_key=tag_key,
                        tag_value=value,
                        display_name_cn=value,
                        display_name_en=value,
                        enabled=True,
                    )
                )

    ensure_llm_defaults(db)

    db.commit()
