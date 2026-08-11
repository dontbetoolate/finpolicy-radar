from src.finpolicy import radar
from src.finpolicy.radar import (
    Candidate,
    api_text,
    canonical_url,
    classify,
    contains_template_expression,
    discover_candidates,
    merge_policies,
    parse_article,
    process_candidate,
    relevance_score,
)


def rules():
    return {
        "relevance_keywords": {"人工智能": 5, "金融机构": 2},
        "categories": {"AI与大模型": ["人工智能", "大模型"]},
    }


def test_canonical_url_removes_tracking():
    assert canonical_url("https://example.com/a?utm_source=x&id=1#top") == "https://example.com/a?id=1"


def test_relevance_score():
    score, hits = relevance_score("金融机构人工智能应用", rules())
    assert score == 7
    assert "人工智能" in hits


def test_classify():
    assert classify("银行人工智能应用", rules()) == ["AI与大模型"]


def test_contains_template_expression_decodes_url_and_known_bindings():
    assert contains_template_expression("{{x.docSubtitle|trimHtml}}")
    assert contains_template_expression("发布时间：{{data.publishDate|dateFormat2}}")
    assert contains_template_expression("来源：data.docSource")
    assert contains_template_expression("?docId=%7B%7Bx.docId%7D%7D")
    assert not contains_template_expression("关于金融机构人工智能应用的通知")


class FakeResponse:
    def __init__(self, text, url="https://www.nfra.gov.cn/list.html"):
        self.text = text
        self.url = url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, text):
        self.text = text

    def get(self, url, timeout):
        return FakeResponse(self.text, url)


class FakeJsonResponse:
    def __init__(self, payload, url):
        self.payload = payload
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeApiSession:
    def __init__(self, response_for_url):
        self.response_for_url = response_for_url
        self.calls = []

    def get(self, url, timeout, params=None):
        self.calls.append((url, params))
        return FakeJsonResponse(self.response_for_url(url, params or {}), url)


def test_discover_candidates_skips_template_links_without_affecting_real_links():
    page = """
    <a href="/cn/view/pages/governmentDetail.html?docId={{x.docId}}"
       title="{{x.docSubtitle|trimHtml}}"></a>
    <a href="/cn/view/pages/governmentDetail.html?docId=123456">
       关于金融机构人工智能应用的通知
    </a>
    """
    source = {
        "id": "nfra_policy",
        "name": "国家金融监督管理总局",
        "start_urls": ["https://www.nfra.gov.cn/list.html"],
        "allowed_domains": ["www.nfra.gov.cn"],
        "include_url_patterns": ["/cn/view/pages/governmentDetail.html"],
        "exclude_url_patterns": [],
        "source_weight": 5,
    }

    candidates = discover_candidates(FakeSession(page), source, {})

    assert [(item.title, item.url) for item in candidates] == [
        (
            "关于金融机构人工智能应用的通知",
            "https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=123456",
        )
    ]


def test_nfra_api_collector_uses_real_document_id_and_public_detail_url():
    source = {
        "id": "nfra_policy",
        "name": "国家金融监督管理总局",
        "collector": "nfra_api",
        "api_list_url": "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild",
        "api_detail_url": "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectByDocId",
        "detail_page_url": "https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html",
        "api_item_id": 926,
        "allowed_domains": ["www.nfra.gov.cn"],
        "include_url_patterns": ["/cn/view/pages/governmentDetail.html"],
        "exclude_url_patterns": [],
        "source_weight": 5,
    }
    payload = {
        "rptCode": 200,
        "data": {
            "rows": [
                {
                    "docId": 1192308,
                    "docTitle": "国家金融监督管理总局关于数据安全管理办法的通知",
                    "publishDate": "2024-12-27 10:27:00",
                    "generaltype": "0",
                }
            ]
        },
    }
    session = FakeApiSession(lambda url, params: payload)

    candidates = discover_candidates(session, source, {"max_candidates_per_source": 40})

    assert len(candidates) == 1
    assert candidates[0].url == "https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=1192308&itemId=926&generaltype=0"
    assert candidates[0].metadata["doc_id"] == "1192308"
    assert candidates[0].list_date == "2024-12-27"


def test_nfra_api_collector_falls_back_to_official_big5_endpoint():
    source = {
        "id": "nfra_policy",
        "name": "国家金融监督管理总局",
        "collector": "nfra_api",
        "api_list_urls": [
            "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild",
            "https://big5.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild",
        ],
        "api_detail_urls": [
            "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectByDocId",
            "https://big5.nfra.gov.cn/cbircweb/DocInfo/SelectByDocId",
        ],
        "detail_page_url": "https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html",
        "api_item_id": 926,
        "allowed_domains": ["www.nfra.gov.cn"],
        "include_url_patterns": ["/cn/view/pages/governmentDetail.html"],
        "exclude_url_patterns": [],
        "source_weight": 5,
    }

    def response_for_url(url, params):
        if url.startswith("https://www.nfra.gov.cn"):
            raise RuntimeError("403 Client Error: Forbidden")
        if "SelectDocByItemIdAndChild" in url:
            return {
                "rptCode": 200,
                "data": {
                    "rows": [
                        {
                            "docId": 1192308,
                            "docTitle": "国家金融监督管理总局关于数据安全管理办法的通知",
                            "publishDate": "2024-12-27 10:27:00",
                        }
                    ]
                },
            }
        return {
            "rptCode": 200,
            "data": {
                "docTitle": "国家金融监督管理总局关于数据安全管理办法的通知",
                "publishDate": "2024-12-27 10:27:00",
                "docClob": "<p>银行保险机构应当加强数据安全管理。</p>",
            },
        }

    session = FakeApiSession(response_for_url)
    candidate = discover_candidates(session, source, {"max_candidates_per_source": 40})[0]
    article = parse_article(session, candidate, {"max_article_chars": 12000})

    assert [url for url, _ in session.calls] == [
        source["api_list_urls"][0],
        source["api_list_urls"][1],
        source["api_detail_urls"][0],
        source["api_detail_urls"][1],
    ]
    assert article["content"] == "银行保险机构应当加强数据安全管理。"
    assert article["url"].startswith("https://www.nfra.gov.cn/")


def test_nfra_api_article_uses_official_api_body_without_template_markup():
    candidate = Candidate(
        "国家金融监督管理总局关于数据安全管理办法的通知",
        "https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=1192308&itemId=926&generaltype=0",
        "nfra_policy",
        "国家金融监督管理总局",
        5,
        metadata={
            "collector": "nfra_api",
            "doc_id": "1192308",
            "detail_api_url": "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectByDocId",
        },
    )
    session = FakeApiSession(
        lambda url, params: {
            "rptCode": 200,
            "data": {
                "docTitle": "国家金融监督管理总局关于数据安全管理办法的通知",
                "publishDate": "2024-12-27 10:27:00",
                "docClob": "<p>银行保险机构应当加强数据安全管理，保护个人信息。</p>",
            },
        }
    )

    article = parse_article(session, candidate, {"max_article_chars": 12000})

    assert article["title"] == candidate.title
    assert article["published_at"] == "2024-12-27"
    assert article["content"] == "银行保险机构应当加强数据安全管理，保护个人信息。"
    assert article["url"] == candidate.url


def test_gov_policy_api_collector_strips_highlight_markup_and_keeps_official_url():
    source = {
        "id": "gov_policy",
        "name": "中国政府网",
        "collector": "gov_policy_api",
        "api_url": "https://sousuo.www.gov.cn/search-gov/data",
        "api_queries": ["金融"],
        "required_context_keywords": ["金融", "银行", "保险", "支付"],
        "allowed_domains": ["www.gov.cn", "sousuo.www.gov.cn"],
        "include_url_patterns": ["/zhengce/", "/zcwjk/"],
        "exclude_url_patterns": ["/hudong/"],
        "source_weight": 5,
    }
    payload = {
        "code": 200,
        "searchVO": {
            "listVO": [
                {
                    "title": "<em>金融</em>产品网络营销管理办法",
                    "url": "https://www.gov.cn/zhengce/zhengceku/202604/content_7066927.htm",
                    "pubtimeStr": "2026.04.24",
                },
                {
                    "title": "关于加快推进人工智能在人力资源领域应用的意见",
                    "summary": "推动人工智能在人力资源领域应用。",
                    "url": "https://www.gov.cn/zhengce/zhengceku/202607/content_7074732.htm",
                    "pubtimeStr": "2026.06.22",
                }
            ]
        },
    }
    session = FakeApiSession(lambda url, params: payload)

    candidates = discover_candidates(session, source, {"max_candidates_per_source": 40})

    assert api_text("<em>金融</em>产品") == "金融产品"
    assert len(candidates) == 1
    assert candidates[0].title == "金融产品网络营销管理办法"
    assert candidates[0].url == "https://www.gov.cn/zhengce/zhengceku/202604/content_7066927.htm"
    assert candidates[0].list_date == "2026-04-24"


def test_process_candidate_skips_template_content(monkeypatch):
    candidate = Candidate("真实政策标题", "https://www.nfra.gov.cn/policy/1", "nfra", "金融监管总局", 5)
    monkeypatch.setattr(
        radar,
        "parse_article",
        lambda session, candidate, rules: {
            "title": "真实政策标题",
            "url": candidate.url,
            "published_at": None,
            "content": "发布时间：{{data.publishDate|dateFormat2}} 金融机构",
        },
    )

    assert process_candidate(None, candidate, rules()) is None


def test_process_candidate_uses_real_detail_title_over_template_list_title():
    page = """
    <html><body>
      <h1>关于金融机构人工智能应用的通知</h1>
      <article>这是从官方详情页提取的真实政策正文，涉及金融机构人工智能应用管理要求。为了确保内容块达到解析长度，这里补充真实政策正文的测试文字。</article>
    </body></html>
    """
    candidate = Candidate(
        "{{x.docSubtitle|trimHtml}}",
        "https://www.nfra.gov.cn/cn/view/pages/governmentDetail.html?docId=123456",
        "nfra_policy",
        "国家金融监督管理总局",
        5,
    )

    item = process_candidate(FakeSession(page), candidate, rules())

    assert item is not None
    assert item["title"] == "关于金融机构人工智能应用的通知"
    assert item["url"] == candidate.url


def test_merge_policies_removes_persisted_template_record_and_keeps_official_url():
    bad = {
        "id": "bad",
        "title": "{{x.docSubtitle|trimHtml}}",
        "url": "https://www.nfra.gov.cn/detail?docId=%7B%7Bx.docId%7D%7D",
        "summary": "来源：{{data.docSource}}",
    }
    good = {
        "id": "good",
        "title": "真实政策标题",
        "url": "https://www.nfra.gov.cn/detail?docId=123",
        "summary": "真实政策正文摘要",
        "published_at": "2026-08-06",
        "discovered_at": "2026-08-06T00:00:00+00:00",
    }

    assert merge_policies([bad, good], []) == [good]
    assert merge_policies([bad], [good])[0]["url"] == good["url"]
