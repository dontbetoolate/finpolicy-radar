from src.finpolicy.radar import canonical_url, classify, relevance_score


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
