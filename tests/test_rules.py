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
        "policy_signal_keywords": ["通知", "规定", "办法", "公告"],
        "excluded_title_patterns": ["会见", "出席", "召开.*会议"],
        "excluded_content_patterns": ["决定授权.{0,80}担任.{0,40}清算行"],
        "minimum_article_chars": 40,
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


def test_pbc_short_content_block_beats_navigation_text():
    page = """
    <html><body>
      <div>术语表 网站地图 无障碍浏览 English Version 信息公开 新闻发布 法律法规
      货币政策 支付体系 金融科技 征信管理 反洗钱 在线申报 下载中心</div>
      <h2>中国人民银行公告〔2026〕第20号</h2>
      <td class="content"><div id="zoom">根据《中国人民银行与德意志联邦银行备忘录》，中国人民银行决定授权德意志银行股份有限公司担任法兰克福人民币清算行。 中国人民银行 2026年8月7日</div></td>
    </body></html>
    """
    candidate = Candidate(
        "中国人民银行公告〔2026〕第20号",
        "https://www.pbc.gov.cn/goutongjiaoliu/example/index.html",
        "pbc_news",
        "中国人民银行",
        5,
    )

    article = parse_article(FakeSession(page), candidate, {})

    assert article["content"].startswith("根据《中国人民银行与德意志联邦银行备忘录》")
    assert "网站地图" not in article["content"]


def test_process_candidate_rejects_one_off_clearing_bank_authorization():
    candidate = Candidate(
        "中国人民银行公告〔2026〕第20号",
        "https://www.pbc.gov.cn/goutongjiaoliu/example/index.html",
        "pbc_news",
        "中国人民银行",
        5,
    )
    page = """
    <html><body><td class="content"><div id="zoom">
      根据《中国人民银行与德意志联邦银行备忘录》，中国人民银行决定授权德意志银行股份有限公司担任法兰克福人民币清算行。
      中国人民银行 2026年8月7日
    </div></td></body></html>
    """

    assert process_candidate(FakeSession(page), candidate, rules()) is None


def test_process_candidate_rejects_meeting_news_even_with_relevant_terms():
    candidate = Candidate(
        "中国人民银行召开金融科技工作会议",
        "https://www.pbc.gov.cn/news/example.html",
        "pbc_news",
        "中国人民银行",
        5,
    )
    page = """
    <html><body><article>会议研究部署金融机构人工智能应用管理工作，并介绍后续通知和规划。这里补足正文长度以模拟真实新闻稿。</article></body></html>
    """

    assert process_candidate(FakeSession(page), candidate, rules()) is None


def test_process_candidate_rejects_commentary_when_policy_signal_only_appears_in_body():
    candidate = Candidate(
        "某负责人：推动行业高质量发展",
        "https://www.cac.gov.cn/news/example.html",
        "cac_policy",
        "国家互联网信息办公室",
        4,
    )
    page = """
    <html><body><article>文章介绍相关管理规定和工作办法，并讨论金融机构人工智能治理方向。这是署名文章而不是政策文件。</article></body></html>
    """

    assert process_candidate(FakeSession(page), candidate, rules()) is None


def test_summary_removes_repeated_title_date_source_and_print_controls():
    title = "个人信息保护规定"
    content = (
        "个人信息保护规定 2026年08月07日 16:00 来源： 中国网信网 【打印】 【纠错】 "
        "为规范个人信息处理活动，保护个人信息权益，有关部门制定本规定并明确适用范围。"
    )

    summary = radar.concise_summary(content, title)

    assert summary.startswith("为规范个人信息处理活动")
    assert "来源" not in summary
    assert "打印" not in summary


def test_importance_reserves_five_stars_for_high_relevance_formal_policy():
    assert radar.importance(5, 3, "规范性文件", "中国人民银行公告〔2026〕第20号") == 3
    assert radar.importance(5, 16, "法律法规", "金融机构数据安全管理办法") == 5
    assert radar.importance(5, 20, "政策解读", "金融机构数据安全管理办法答记者问") == 3
    assert radar.importance(5, 20, "监管动态", "金融科技工作会议") == 2


def test_document_type_prioritizes_policy_interpretation_over_body_legal_terms():
    configured_rules = {
        "policy_type_keywords": {
            "法律法规": ["法律", "规定"],
            "政策解读": ["政策解读", "专家解读"],
        }
    }

    assert radar.document_type("专家解读｜构建个人信息保护法律制度", configured_rules) == "政策解读"


def test_normalize_existing_policies_removes_navigation_pollution_and_recalculates_importance():
    polluted = {
        "id": "polluted",
        "title": "中国人民银行公告〔2026〕第20号",
        "url": "https://www.pbc.gov.cn/example.html",
        "summary": "术语表 网站地图 无障碍浏览 English Version 新闻发布 在线申报 下载中心",
        "source_id": "pbc_news",
        "document_type": "规范性文件",
        "relevance_score": 14,
        "importance": 5,
    }
    valid = {
        "id": "valid",
        "title": "金融机构数据安全管理办法",
        "url": "https://www.nfra.gov.cn/example.html",
        "summary": "本办法规定金融机构应当建立数据安全管理制度并落实个人信息保护责任。",
        "source_id": "nfra_policy",
        "document_type": "法律法规",
        "relevance_score": 16,
        "importance": 2,
    }
    configured_rules = rules() | {
        "_sources": [{"id": "nfra_policy", "source_weight": 5}],
        "policy_type_keywords": {"法律法规": ["办法", "规定"]},
    }

    normalized = radar.normalize_existing_policies([polluted, valid], configured_rules)

    assert [item["id"] for item in normalized] == ["valid"]
    assert normalized[0]["importance"] == 5


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


def test_merge_policies_removes_exact_title_duplicate_from_same_source():
    older = {
        "id": "older",
        "title": "金融基础设施监督管理办法",
        "source_id": "pbc_news",
        "url": "https://www.pbc.gov.cn/policy/older.html",
        "summary": "正式政策摘要",
        "published_at": "2025-08-01",
        "discovered_at": "2026-08-06T09:36:36+00:00",
    }
    newer = {
        **older,
        "id": "newer",
        "url": "https://www.pbc.gov.cn/policy/newer.html",
        "discovered_at": "2026-08-06T09:36:37+00:00",
    }
    another_source = {
        **older,
        "id": "gov-copy",
        "source_id": "gov_policy",
        "url": "https://www.gov.cn/policy/copy.html",
    }

    merged = merge_policies([older, newer, another_source], [])

    assert [item["id"] for item in merged] == ["newer", "gov-copy"]
