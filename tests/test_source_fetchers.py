from __future__ import annotations

from datetime import datetime
import unittest

from ai_agent.models import Source
from ai_agent.services import CrawlService


class SourceFetcherTests(unittest.TestCase):
    def test_cyzone_uses_article_cards_and_list_date(self) -> None:
        source = Source(
            source_name="创业邦资讯",
            source_category="venture_media",
            source_url="https://www.cyzone.cn/channel/news",
            list_page_limit=20,
            item_limit_per_run=10,
            timeout_seconds=20,
        )
        home = """
            <html><body>
              <nav><a href="/article/111.html">导航推荐文章</a></nav>
              <div class="article-item">
                <a class="item-title" href="/article/836803.html">融资丨机器人公司完成新一轮融资</a>
                <a href="/article/836803.html">推进具身智能数据工业化</a>
                <span>2026-06-15 09:30</span>
              </div>
            </body></html>
        """
        detail = """
            <html><head>
              <title>融资丨机器人公司完成新一轮融资 - 创业邦</title>
              <meta name="description" content="推进具身智能数据工业化">
            </head><body><main>
              <h1>融资丨机器人公司完成新一轮融资</h1>
              <p>这是创业邦文章正文，包含足够长的内容以验证详情页解析和发布时间覆盖逻辑。</p>
              <div>推荐阅读 2026-06-14</div>
            </main></body></html>
        """
        service = CrawlService()
        service._get_html = lambda url, timeout: detail if "/article/" in url else home

        items = service.fetch_cyzone(source, run_timestamp=datetime(2026, 6, 15, 10, 0))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "融资丨机器人公司完成新一轮融资")
        self.assertEqual(items[0].publish_time, datetime(2026, 6, 15, 9, 30))
        self.assertNotIn("导航推荐文章", [item.title for item in items])

    def test_36kr_information_uses_flow_cards_only(self) -> None:
        source = Source(
            source_name="36氪AI",
            source_category="ai_media",
            source_url="https://36kr.com/information/AI/",
            list_page_limit=20,
            item_limit_per_run=10,
            timeout_seconds=20,
        )
        home = """
            <html><body>
              <nav><a href="/p/111111">导航区旧文章</a></nav>
              <div class="information-flow-list">
                <div class="information-flow-item">
                  <a class="article-item-title weight-bold" href="/p/3853016586359817">AI栏目文章</a>
                </div>
              </div>
            </body></html>
        """
        detail = """
            <html><head>
              <meta property="og:title" content="AI栏目文章-36氪">
              <meta property="article:published_time" content="2026-06-15T11:00:00+08:00">
              <meta name="description" content="AI栏目文章摘要">
            </head><body><article>
              <h1>AI栏目文章</h1>
              <p>这是三十六氪 AI 栏目的文章正文，内容长度足以用于详情提取测试。</p>
            </article>
            <script>window.initialState={"itemId":3853016586359817,"publishTime":1781488200127};</script>
            </body></html>
        """
        service = CrawlService()
        service._get_html = lambda url, timeout: detail if "/p/" in url else home

        items = service.fetch_36kr(source, run_timestamp=datetime(2026, 6, 15, 10, 0))

        self.assertEqual([item.title for item in items], ["AI栏目文章"])
        self.assertEqual(items[0].publish_time, datetime(2026, 6, 15, 9, 50, 0, 127000))
        self.assertNotIn("导航区旧文章", [item.title for item in items])

    def test_36kr_information_reports_captcha_page(self) -> None:
        source = Source(
            source_name="36氪创投",
            source_category="venture_media",
            source_url="https://36kr.com/information/contact/",
            list_page_limit=20,
            item_limit_per_run=10,
            timeout_seconds=20,
        )
        service = CrawlService()
        service._get_html = lambda url, timeout: '<script src="/sec_sdk_build/captcha/index.js"></script>'

        with self.assertRaisesRegex(RuntimeError, "验证码页"):
            service.fetch_36kr(source, run_timestamp=datetime(2026, 6, 15, 10, 0))

    def test_jazzyear_uses_page_title_and_list_date(self) -> None:
        source = Source(
            source_name="甲子光年",
            source_category="venture_media",
            source_url="https://www.jazzyear.com/",
            list_page_limit=20,
            item_limit_per_run=10,
            timeout_seconds=20,
        )
        home = """
            <html><main>
              <a href="/article_info.html?id=1775">
                原创 不叠衣服、不跳舞，这家AI公司凭什么签下上亿订单？
                人工智能 · 商业化 作者：张麟 2026-06-04
              </a>
            </main></html>
        """
        detail = """
            <html><head>
              <title>不叠衣服、不跳舞，这家AI公司凭什么签下上亿订单？</title>
            </head><body><main>
              <div>其他报告日期 2026-06-09</div>
              <h1>1. 从数据工厂到大模型：工匠行智能有何突破？</h1>
              <p>这是一段足够长的文章正文，用于验证甲子光年详情页能够正确提取正文，而不会把章节标题当成文章标题。</p>
            </main></body></html>
        """
        service = CrawlService()
        service._get_html = lambda url, timeout: detail if "article_info" in url else home

        items = service.fetch_jazzyear(source, run_timestamp=datetime(2026, 6, 4, 10, 0))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "不叠衣服、不跳舞，这家AI公司凭什么签下上亿订单？")
        self.assertEqual(items[0].publish_time, datetime(2026, 6, 4))

    def test_a16z_uses_main_ai_feed_and_excludes_header_recommendations(self) -> None:
        source = Source(
            source_name="a16z AI",
            source_category="venture_media",
            source_url="https://a16z.com/ai/",
            list_page_limit=20,
            item_limit_per_run=10,
            timeout_seconds=20,
        )
        home = """
            <html><body>
              <header><div data-feed-item><h4>
                <a href="https://a16z.com/unrelated/">Unrelated global recommendation</a>
              </h4></div></header>
              <main>
                <div data-feed-item><h4>
                  <a href="https://a16z.com/current-ai-post/">Current AI Post</a>
                </h4></div>
                <div class="hidden" data-feed-item><h4>
                  <a href="https://a16z.com/podcast/previous-ai-post/">Previous AI Post</a>
                </h4></div>
              </main>
            </body></html>
        """
        current = """
            <html><head>
              <meta property="og:title" content="Current AI Post | Andreessen Horowitz">
              <meta property="article:published_time" content="2026-06-12T00:00:00+00:00">
            </head><body><article><p>This is the current AI article body with enough detail for extraction.</p></article></body></html>
        """
        previous = """
            <html><head>
              <meta property="og:title" content="Previous AI Post | Andreessen Horowitz">
              <meta property="article:published_time" content="2026-06-11T00:00:00+00:00">
            </head><body><article><p>This is the previous AI article body with enough detail for extraction.</p></article></body></html>
        """
        pages = {
            source.source_url: home,
            "https://a16z.com/current-ai-post": current,
            "https://a16z.com/podcast/previous-ai-post": previous,
        }
        service = CrawlService()
        service._get_html = lambda url, timeout: pages[url]

        items = service.fetch_a16z_ai(source, run_timestamp=datetime(2026, 6, 12, 10, 0))

        self.assertEqual([item.title for item in items], ["Current AI Post", "Previous AI Post"])
        self.assertNotIn("Unrelated global recommendation", [item.title for item in items])


if __name__ == "__main__":
    unittest.main()
