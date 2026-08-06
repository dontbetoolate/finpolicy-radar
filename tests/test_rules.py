from src.finpolicy import radar
from src.finpolicy.radar import (
    Candidate,
    canonical_url,
    classify,
    contains_template_expression,
    discover_candidates,
    merge_policies,
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
