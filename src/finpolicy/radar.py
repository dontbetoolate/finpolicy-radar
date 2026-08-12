from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from jinja2 import Environment, FileSystemLoader, select_autoescape
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public"
TEMPLATE_DIR = ROOT / "templates"

DATE_PATTERNS = [
    re.compile(r"(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),
]
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "spm", "from", "isapp"}
TEMPLATE_EXPRESSION_RE = re.compile(
    r"\{\{.*?\}\}|\{%.*?%\}|\b(?:x|data)\.[A-Za-z_$][\w$]*",
    re.IGNORECASE | re.DOTALL,
)
NAVIGATION_MARKERS = (
    "术语表",
    "网站地图",
    "无障碍浏览",
    "English Version",
    "新闻发布",
    "在线申报",
    "下载中心",
    "打印本页",
    "关闭窗口",
)
ANALYSIS_NOTICE = "以上摘要与关注级别由规则自动生成，不代表发布机构观点；请以官方原文为准。"


@dataclass
class Candidate:
    title: str
    url: str
    source_id: str
    source_name: str
    source_weight: int
    list_date: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalized_source_title(title: str, source: dict[str, Any]) -> str:
    normalized = clean_text(title)
    for original, replacement in source.get("title_normalization_map", {}).items():
        normalized = normalized.replace(str(original), str(replacement))
    return clean_text(normalized)


def contains_template_expression(value: str) -> bool:
    """Return whether text contains an unresolved front-end template expression."""
    normalized = unquote(html.unescape(value or ""))
    return "{{" in normalized or "}}" in normalized or bool(TEMPLATE_EXPRESSION_RE.search(normalized))


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": "FinPolicyRadar/1.0 (+public-policy-monitor; respectful low-frequency crawler)",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        }
    )
    return session


def fetch(session: requests.Session, url: str, timeout: int) -> tuple[str, str]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text, response.url


def fetch_json(session: requests.Session, url: str, timeout: int, params: dict[str, Any]) -> dict[str, Any]:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return payload


def configured_api_urls(source: dict[str, Any], plural_key: str, singular_key: str) -> list[str]:
    """Return ordered API endpoints while retaining singular-key compatibility."""
    urls = source.get(plural_key)
    if urls:
        return [str(url) for url in urls]
    return [str(source[singular_key])]


def fetch_nfra_json(
    session: requests.Session,
    urls: list[str],
    timeout: int,
    params: dict[str, Any],
    api_name: str,
) -> dict[str, Any]:
    """Try equivalent official NFRA endpoints in order, including its Big5 mirror."""
    failures = []
    for url in urls:
        try:
            payload = fetch_json(session, url, timeout, params)
            if payload.get("rptCode") == 200:
                return payload
            failures.append(f"{url}: rptCode={payload.get('rptCode')!r}")
        except Exception as exc:
            failures.append(f"{url}: {exc}")
        if url != urls[-1]:
            logging.warning("NFRA %s endpoint failed; trying official fallback: %s", api_name, failures[-1])
    raise RuntimeError(f"All NFRA {api_name} endpoints failed: {'; '.join(failures)}")


def domain_allowed(url: str, domains: Iterable[str]) -> bool:
    host = urlsplit(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in domains)


def url_pattern_allowed(url: str, include: list[str], exclude: list[str]) -> bool:
    if include and not any(pattern in url for pattern in include):
        return False
    return not any(pattern in url for pattern in exclude)


def extract_date(text: str, url: str = "") -> str | None:
    for target in (text, url):
        for pattern in DATE_PATTERNS:
            match = pattern.search(target)
            if not match:
                continue
            try:
                year, month, day = map(int, match.groups())
                return datetime(year, month, day, tzinfo=timezone.utc).date().isoformat()
            except ValueError:
                continue
    return None


def candidate_key(title: str, url: str) -> str:
    return hashlib.sha256((clean_text(title).lower() + "|" + canonical_url(url)).encode("utf-8")).hexdigest()[:18]


def api_text(value: Any, separator: str = "") -> str:
    """Convert an official API HTML fragment into plain text."""
    decoded = html.unescape(str(value or ""))
    if "<" not in decoded and ">" not in decoded:
        return clean_text(decoded)
    soup = BeautifulSoup(decoded, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return clean_text(soup.get_text(separator, strip=True))


def candidate_is_allowed(title: str, url: str, source: dict[str, Any]) -> bool:
    return bool(
        title
        and len(title) >= 7
        and url.startswith(("http://", "https://"))
        and not contains_template_expression(title)
        and not contains_template_expression(url)
        and domain_allowed(url, source.get("allowed_domains", []))
        and url_pattern_allowed(url, source.get("include_url_patterns", []), source.get("exclude_url_patterns", []))
    )


def source_focus_allowed(title: str, context: str, source: dict[str, Any]) -> bool:
    """Apply source-specific precision rules without treating broad homonyms as fintech."""
    title_keywords = source.get("candidate_title_keywords", [])
    if title_keywords and not any(keyword in title for keyword in title_keywords):
        return False
    combined = f"{title} {context}"
    return not any(re.search(pattern, combined) for pattern in source.get("excluded_context_patterns", []))


def discover_html_candidates(session: requests.Session, source: dict[str, Any], rules: dict[str, Any]) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    max_items = int(rules.get("max_candidates_per_source", 40))
    timeout = int(rules.get("request_timeout_seconds", 25))

    for start_url in source["start_urls"]:
        page, final_url = fetch(session, start_url, timeout)
        soup = BeautifulSoup(page, "html.parser")
        for tag in soup.find_all("a", href=True):
            title = clean_text(tag.get_text(" ", strip=True) or tag.get("title", ""))
            raw_href = tag.get("href", "")
            title_is_template = contains_template_expression(title)
            if (
                (len(title) < 7 and not title_is_template)
                or title in {"更多", "详情", "查看详情", "下一页", "上一页"}
                or contains_template_expression(raw_href)
            ):
                continue
            url = canonical_url(urljoin(final_url, raw_href))
            if not candidate_is_allowed(title, url, source):
                continue
            if not source_focus_allowed(title, "", source):
                continue
            parent_text = clean_text(tag.parent.get_text(" ", strip=True) if tag.parent else title)
            date = extract_date(parent_text, url)
            key = candidate_key(title, url)
            found[key] = Candidate(
                title=title,
                url=url,
                source_id=source["id"],
                source_name=source["name"],
                source_weight=int(source.get("source_weight", 3)),
                list_date=date,
            )
            if len(found) >= max_items:
                break
        if len(found) >= max_items:
            break
    return list(found.values())


def discover_nfra_api_candidates(session: requests.Session, source: dict[str, Any], rules: dict[str, Any]) -> list[Candidate]:
    """Discover real NFRA policies from its public document API, not Angular markup."""
    timeout = int(rules.get("request_timeout_seconds", 25))
    page_size = int(source.get("api_page_size", rules.get("max_candidates_per_source", 40)))
    max_pages = int(source.get("api_max_pages_per_item", 1))
    max_candidates = int(source.get("api_max_candidates", page_size * max_pages * 3))
    item_ids = source.get("api_item_ids") or [source["api_item_id"]]
    item_types = {str(key): value for key, value in source.get("api_item_types", {}).items()}
    found: dict[str, Candidate] = {}

    for configured_item_id in item_ids:
        item_id = str(configured_item_id)
        for page_index in range(1, max_pages + 1):
            payload = fetch_nfra_json(
                session,
                configured_api_urls(source, "api_list_urls", "api_list_url"),
                timeout,
                {"itemId": item_id, "pageSize": page_size, "pageIndex": page_index, "orderBy": "builddate"},
                "list",
            )
            data = payload.get("data", {})
            rows = data.get("rows", [])
            if not isinstance(rows, list):
                raise ValueError(f"NFRA API response has no document rows for item {item_id}")

            for row in rows:
                if not isinstance(row, dict) or not row.get("docId"):
                    continue
                doc_id = str(row["docId"])
                title = normalized_source_title(api_text(row.get("docTitle") or row.get("docSubtitle")), source)
                if not source_focus_allowed(title, "", source):
                    continue
                detail_params = {
                    "docId": doc_id,
                    "itemId": item_id,
                    "generaltype": str(row.get("generaltype") or "0"),
                }
                url = canonical_url(f"{source['detail_page_url']}?{urlencode(detail_params)}")
                if not candidate_is_allowed(title, url, source):
                    continue
                found[doc_id] = Candidate(
                    title=title,
                    url=url,
                    source_id=source["id"],
                    source_name=source["name"],
                    source_weight=int(source.get("source_weight", 3)),
                    list_date=extract_date(str(row.get("publishDate") or row.get("builddate") or "")),
                    metadata={
                        "collector": "nfra_api",
                        "doc_id": doc_id,
                        "official_document_type": item_types.get(item_id),
                        "title_normalization_map": source.get("title_normalization_map", {}),
                        "detail_api_urls": configured_api_urls(source, "api_detail_urls", "api_detail_url"),
                    },
                )

            total = int(data.get("total") or len(rows))
            if not rows or page_index * page_size >= total:
                break

    return sorted(found.values(), key=lambda item: (item.list_date or "", item.title), reverse=True)[:max_candidates]


def discover_gov_policy_api_candidates(session: requests.Session, source: dict[str, Any], rules: dict[str, Any]) -> list[Candidate]:
    """Discover China Government Network policies through its public policy-library search API."""
    timeout = int(rules.get("request_timeout_seconds", 25))
    max_items = int(rules.get("max_candidates_per_source", 40))
    found: dict[str, Candidate] = {}
    successful_queries = 0

    for query in source.get("api_queries", [""]):
        params = {
            "t": "zhengcelibrary_bm",
            "q": query,
            "timetype": "",
            "mintime": "",
            "maxtime": "",
            "sort": "pubtime",
            "sortType": 1,
            "searchfield": "title",
            "pcodeJiguan": "",
            "childtype": "",
            "subchildtype": "",
            "tsbq": "",
            "pubtimeyear": "",
            "puborg": "",
            "pcodeYear": "",
            "pcodeNum": "",
            "filetype": "",
            "p": 1,
            "n": max_items,
            "inpro": "",
            "bmfl": "",
            "dup": "",
            "orpro": "",
            "type": "gwyzcwjk",
        }
        try:
            payload = fetch_json(session, source["api_url"], timeout, params)
        except Exception as exc:
            logging.warning("China Government Network query failed for %r: %s", query, exc)
            continue
        if payload.get("code") != 200:
            logging.warning("China Government Network query returned code=%r for %r", payload.get("code"), query)
            continue
        successful_queries += 1
        rows = payload.get("searchVO", {}).get("listVO", [])
        if not isinstance(rows, list):
            logging.warning("China Government Network query returned no policy list for %r", query)
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = api_text(row.get("title"))
            context = " ".join(
                [title, api_text(row.get("summary")), api_text(row.get("puborg"))]
            )
            required_context = source.get("required_context_keywords", [])
            if required_context and not any(keyword in context for keyword in required_context):
                continue
            if not source_focus_allowed(title, context, source):
                continue
            url = canonical_url(str(row.get("url") or ""))
            if not candidate_is_allowed(title, url, source):
                continue
            found[url] = Candidate(
                title=title,
                url=url,
                source_id=source["id"],
                source_name=source["name"],
                source_weight=int(source.get("source_weight", 3)),
                list_date=extract_date(str(row.get("pubtimeStr") or "")),
            )

    if not successful_queries:
        raise ValueError("All China Government Network policy-library queries failed")
    max_candidates = int(source.get("api_max_candidates", max_items * max(1, len(source.get("api_queries", [])))))
    return sorted(found.values(), key=lambda item: (item.list_date or "", item.title), reverse=True)[:max_candidates]


def discover_candidates(session: requests.Session, source: dict[str, Any], rules: dict[str, Any]) -> list[Candidate]:
    collector = source.get("collector")
    if collector == "nfra_api":
        return discover_nfra_api_candidates(session, source, rules)
    if collector == "gov_policy_api":
        return discover_gov_policy_api_candidates(session, source, rules)
    return discover_html_candidates(session, source, rules)


def has_navigation_noise(value: str) -> bool:
    """Detect text blocks dominated by common government-site navigation controls."""
    lower = clean_text(value).lower()
    return sum(marker.lower() in lower for marker in NAVIGATION_MARKERS) >= 4


def clean_content_block(node: Any, minimum_chars: int) -> str:
    text = clean_text(node.get_text(" ", strip=True))
    if len(text) < minimum_chars or has_navigation_noise(text):
        return ""
    links = node.find_all("a")
    link_chars = sum(len(clean_text(link.get_text(" ", strip=True))) for link in links)
    if len(links) >= 3 and link_chars / max(len(text), 1) > 0.35:
        return ""
    return text


def largest_content_block(soup: BeautifulSoup) -> str:
    selectors = [
        "article",
        "main",
        "#UCAP-CONTENT",
        ".TRS_Editor",
        "#zoom",
        ".zoom1",
        "td.content",
        ".article-content",
        ".article",
        ".pages_content",
        ".detail",
        ".content",
    ]
    for selector in selectors:
        blocks = [clean_content_block(node, 40) for node in soup.select(selector)]
        blocks = [text for text in blocks if text]
        if blocks:
            return max(blocks, key=len)
    blocks = [clean_content_block(node, 60) for node in soup.find_all(["div", "section", "td"])]
    return max((text for text in blocks if text), key=len, default="")


def parse_article(session: requests.Session, candidate: Candidate, rules: dict[str, Any]) -> dict[str, Any]:
    if candidate.metadata.get("collector") == "nfra_api":
        return parse_nfra_api_article(session, candidate, rules)

    timeout = int(rules.get("request_timeout_seconds", 25))
    page, final_url = fetch(session, candidate.url, timeout)
    soup = BeautifulSoup(page, "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "footer", "form", "iframe"]):
        node.decompose()

    h1 = soup.find("h1")
    h1_title = clean_text(h1.get_text(" ", strip=True)) if h1 else ""
    title = h1_title if h1_title and not contains_template_expression(h1_title) else candidate.title
    page_text = clean_text(soup.get_text(" ", strip=True))
    published_at = extract_date(page_text[:2500], final_url) or candidate.list_date
    content = largest_content_block(soup)
    max_chars = int(rules.get("max_article_chars", 12000))
    content = content[:max_chars]
    return {
        "title": title or candidate.title,
        "url": canonical_url(final_url),
        "published_at": published_at,
        "content": content,
    }


def parse_nfra_api_article(session: requests.Session, candidate: Candidate, rules: dict[str, Any]) -> dict[str, Any]:
    """Use NFRA's public document API for the body while retaining its public detail-page URL."""
    timeout = int(rules.get("request_timeout_seconds", 25))
    detail_api_urls = candidate.metadata.get("detail_api_urls")
    if not detail_api_urls:
        detail_api_urls = [candidate.metadata["detail_api_url"]]
    payload = fetch_nfra_json(
        session,
        [str(url) for url in detail_api_urls],
        timeout,
        {"docId": candidate.metadata["doc_id"]},
        "detail",
    )
    if not isinstance(payload.get("data"), dict):
        raise ValueError("NFRA detail API returned no document data")
    document = payload["data"]
    detail_title = normalized_source_title(
        api_text(document.get("docTitle") or document.get("docSubtitle")),
        {"title_normalization_map": candidate.metadata.get("title_normalization_map", {})},
    )
    title = candidate.title if candidate.title and not contains_template_expression(candidate.title) else detail_title
    content = api_text(document.get("docClob"), separator=" ")[: int(rules.get("max_article_chars", 12000))]
    published_at = extract_date(str(document.get("publishDate") or document.get("builddate") or "")) or candidate.list_date
    return {"title": title, "url": candidate.url, "published_at": published_at, "content": content}


def relevance_score(text: str, rules: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    hits = []
    lower = text.lower()
    for keyword, weight in rules.get("relevance_keywords", {}).items():
        if keyword.lower() in lower:
            score += int(weight)
            hits.append(keyword)
    return score, hits


def classify(text: str, rules: dict[str, Any]) -> list[str]:
    result = []
    for category, keywords in rules.get("categories", {}).items():
        if any(keyword.lower() in text.lower() for keyword in keywords):
            result.append(category)
    return result or ["其他金融科技政策"]


def document_type(title: str, rules: dict[str, Any], official_type: str | None = None) -> str:
    """Classify by the official publication form and title, never incidental body terms."""
    title = clean_text(title)
    patterns = rules.get("policy_type_patterns")
    if not patterns:  # Retain compatibility with small external/test configurations.
        patterns = rules.get("policy_type_keywords", {})

    priority = ("政策解读与说明", "政策解读", "征求意见稿", "征求意见", "规划与实施方案")
    for kind in priority:
        if any(re.search(pattern, title) for pattern in patterns.get(kind, [])):
            return kind
    if official_type:
        return official_type
    for kind in ("法律法规与部门规章", "法律法规", "标准与技术规范", "规范性政策文件", "规范性文件"):
        if any(re.search(pattern, title) for pattern in patterns.get(kind, [])):
            return kind
    return "其他政策材料"


def applicable_entities(title: str, content: str, rules: dict[str, Any]) -> list[str]:
    """Infer explicit regulated audiences from the title and opening scope text."""
    patterns_by_entity = rules.get("applicable_entity_patterns", {})
    for scope_text in (clean_text(title), clean_text(content)[:1600]):
        result = [
            entity
            for entity, patterns in patterns_by_entity.items()
            if any(re.search(pattern, scope_text) for pattern in patterns)
        ]
        if result:
            return result
    scope_text = f"{clean_text(title)} {clean_text(content)[:1600]}"
    if any(re.search(pattern, scope_text) for pattern in rules.get("general_entity_patterns", [])):
        return ["多类主体或行业通用"]
    return ["未明确"]


def is_policy_material(
    title: str,
    content: str,
    rules: dict[str, Any],
    minimum_chars: int | None = None,
) -> bool:
    """Require policy-document signals and reject obvious news or one-off actions."""
    title = clean_text(title)
    content = clean_text(content)
    required_chars = int(rules.get("minimum_article_chars", 40)) if minimum_chars is None else minimum_chars
    if len(content) < required_chars or has_navigation_noise(content):
        return False
    if any(re.search(pattern, title) for pattern in rules.get("excluded_title_patterns", [])):
        return False
    combined = f"{title} {content[:1200]}"
    if any(re.search(pattern, combined) for pattern in rules.get("excluded_content_patterns", [])):
        return False
    return any(signal in title for signal in rules.get("policy_signal_keywords", []))


def concise_summary(content: str, title: str) -> str:
    text = clean_text(content)
    text = re.sub(rf"^{re.escape(clean_text(title))}\s*", "", text)
    text = re.sub(
        r"^20\d{2}年\d{1,2}月\d{1,2}日(?:\s+\d{1,2}:\d{2})?\s+来源：.*?【纠错】\s*",
        "",
        text,
    )
    text = re.sub(r"【(?:打印|纠错)】|打印本页|关闭窗口", "", text)
    text = re.sub(r"^(当前位置|首页)[^。]{0,100}", "", text)
    text = re.sub(r"\s+([：，。；])", r"\1", text)
    text = re.sub(r"\s+([《“（])", r"\1", text)
    text = re.sub(r"([《“（])\s+", r"\1", text)
    text = re.sub(r"\s+([》”）])", r"\1", text)
    text = re.sub(r"([，。；：])\s+", r"\1", text)
    text = re.sub(r"\s*\+\s*", "+", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    for _ in range(2):
        if text.startswith("各") and "：" in text[:800]:
            text = text.split("：", 1)[1].lstrip()
        else:
            break
    sentences = re.split(r"(?<=[。！？；])", text)
    selected = []
    total = 0
    for sentence in sentences:
        sentence = clean_text(sentence)
        if len(sentence) < 12 or title in sentence and len(sentence) < len(title) + 20:
            continue
        selected.append(sentence)
        total += len(sentence)
        if total >= 150 or len(selected) >= 2:
            break
    summary = "".join(selected)
    if not summary:
        summary = f"官方发布与“{title}”相关的政策或监管信息，请点击原文查看完整内容。"
    return summary[:220]


def clean_existing_summary(summary: str, title: str) -> str:
    """Clean an already generated summary without repeatedly summarizing or truncating it."""
    text = clean_text(summary)
    text = re.sub(rf"^{re.escape(clean_text(title))}\s*", "", text)
    text = re.sub(r"【(?:打印|纠错)】|打印本页|关闭窗口", "", text)
    text = re.sub(r"\s+([：，。；])", r"\1", text)
    text = re.sub(r"\s+([《“（])", r"\1", text)
    text = re.sub(r"([《“（])\s+", r"\1", text)
    text = re.sub(r"\s+([》”）])", r"\1", text)
    text = re.sub(r"([，。；：])\s+", r"\1", text)
    text = re.sub(r"\s*\+\s*", "+", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return clean_text(text)[:220]


def importance(
    source_weight: int,
    score: int,
    dtype: str,
    title: str,
    content: str = "",
    entities: list[str] | None = None,
    rules: dict[str, Any] | None = None,
) -> int:
    """Return 5/3/1 for core, tracked, and reference attention levels."""
    del source_weight  # All accepted records already come from official sources.
    rules = rules or {}
    entities = entities or []
    if dtype in {"政策解读与说明", "政策解读", "其他政策材料", "监管动态"}:
        return 1

    direct_text = f"{clean_text(title)} {clean_text(content)[:1200]}"
    core_keywords = rules.get(
        "attention_core_keywords",
        ["金融科技", "人工智能", "数据治理", "数据安全", "网络安全", "个人信息", "数字人民币", "支付清算"],
    )
    title_core = any(keyword in title for keyword in core_keywords)
    direct_core = any(keyword in direct_text for keyword in core_keywords)
    broad_scope = (
        len([entity for entity in entities if entity != "未明确"]) >= 2
        or "多类主体或行业通用" in entities
        or any(marker in direct_text for marker in ["银行业保险业", "金融机构", "金融行业", "全行业"])
    )
    formal = dtype in {
        "法律法规与部门规章",
        "法律法规",
        "规范性政策文件",
        "规范性文件",
        "规划与实施方案",
        "标准与技术规范",
        "征求意见稿",
        "征求意见",
    }
    if formal and title_core and broad_scope and not any(marker in title for marker in ["规章制定工作计划", "立法工作计划"]):
        return 5
    if formal and (direct_core or score >= 10):
        return 3
    return 1


def impact_text(categories: list[str], rules: dict[str, Any]) -> str:
    templates = rules.get("impact_templates", {})
    values = [templates[c] for c in categories if c in templates]
    return values[0] if values else "可能影响金融机构的产品、技术、数据或合规管理，具体要求应以官方原文为准。"


def process_candidate(session: requests.Session, candidate: Candidate, rules: dict[str, Any]) -> dict[str, Any] | None:
    if contains_template_expression(candidate.url):
        return None
    article = parse_article(session, candidate, rules)
    if any(
        contains_template_expression(article.get(field, ""))
        for field in ("title", "url", "content")
    ):
        return None
    source = next((item for item in rules.get("_sources", []) if item.get("id") == candidate.source_id), {})
    if not source_focus_allowed(article["title"], article.get("content", "")[:1600], source):
        return None
    if not is_policy_material(article["title"], article.get("content", ""), rules):
        return None
    combined = " ".join([article["title"], article.get("content", "")[:4000]])
    score, hits = relevance_score(combined, rules)
    if score < int(rules.get("minimum_relevance_score", 2)):
        return None
    categories = classify(combined, rules)
    dtype = document_type(article["title"], rules, candidate.metadata.get("official_document_type"))
    entities = applicable_entities(article["title"], article.get("content", ""), rules)
    discovered = now_iso()
    return {
        "id": candidate_key(article["title"], article["url"]),
        "title": article["title"],
        "source_id": candidate.source_id,
        "source_name": candidate.source_name,
        "published_at": article.get("published_at"),
        "discovered_at": discovered,
        "updated_at": discovered,
        "url": article["url"],
        "document_type": dtype,
        "applicable_entities": entities,
        "categories": categories,
        "keywords": hits[:10],
        "relevance_score": score,
        "importance": importance(
            candidate.source_weight,
            score,
            dtype,
            article["title"],
            article.get("content", ""),
            entities,
            rules,
        ),
        "summary": concise_summary(article.get("content", ""), article["title"]),
        "impact": impact_text(categories, rules),
        "analysis_notice": ANALYSIS_NOTICE,
    }


def policy_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (item.get("published_at") or item.get("discovered_at") or "", item.get("discovered_at") or "")


def merge_policies(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid_existing = [item for item in existing if policy_is_valid(item)]
    valid_incoming = [item for item in incoming if policy_is_valid(item)]
    index = {item["id"]: item for item in valid_existing}
    url_index = {canonical_url(item["url"]): item["id"] for item in valid_existing if item.get("url")}
    for item in valid_incoming:
        existing_id = url_index.get(canonical_url(item["url"]))
        if existing_id and existing_id in index:
            old_discovered = index[existing_id].get("discovered_at")
            item["id"] = existing_id
            item["discovered_at"] = old_discovered or item["discovered_at"]
        index[item["id"]] = item
    merged = sorted(index.values(), key=policy_sort_key, reverse=True)
    deduplicated = []
    seen_titles: set[tuple[str, str]] = set()
    for item in merged:
        title_key = (str(item.get("source_id", "")), normalize_title_for_deduplication(str(item.get("title", ""))))
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        deduplicated.append(item)
    return deduplicated


def normalize_title_for_deduplication(title: str) -> str:
    """Collapse harmless spacing/punctuation differences while retaining distinct policy titles."""
    return re.sub(r"[\s\u3000，,。；;：:（）()《》“”‘’\-—]+", "", clean_text(title)).casefold()


def policy_is_valid(item: dict[str, Any]) -> bool:
    """Reject persisted policies containing unresolved template data."""
    return bool(item.get("title") and item.get("url")) and not any(
        contains_template_expression(str(item.get(field, "")))
        for field in ("title", "summary", "url")
    )


def normalize_existing_policies(items: list[dict[str, Any]], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Remove persisted false positives and apply the current importance scale."""
    normalized = []
    for original in items:
        if not policy_is_valid(original):
            continue
        item = dict(original)
        if not is_policy_material(item.get("title", ""), item.get("summary", ""), rules, minimum_chars=12):
            continue
        item["summary"] = clean_existing_summary(item.get("summary", ""), item.get("title", ""))
        item["document_type"] = document_type(item.get("title", ""), rules)
        item["applicable_entities"] = applicable_entities(
            item.get("title", ""), item.get("summary", ""), rules
        )
        item["importance"] = importance(
            int(next((source.get("source_weight", 3) for source in rules.get("_sources", []) if source.get("id") == item.get("source_id")), 3)),
            int(item.get("relevance_score", 0)),
            item["document_type"],
            item.get("title", ""),
            item.get("summary", ""),
            item["applicable_entities"],
            rules,
        )
        item["analysis_notice"] = ANALYSIS_NOTICE
        normalized.append(item)
    return normalized


def build_feed(
    policies: list[dict[str, Any]],
    site_title: str = "金策雷达",
    site_url: str = "https://dontbetoolate.github.io/finpolicy-radar/",
) -> str:
    updated = datetime.now(timezone.utc)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = site_title
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = "金融科技政策自动监测"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(updated)
    for item in policies[:50]:
        date_str = item.get("published_at") or item.get("discovered_at")
        try:
            dt = date_parser.parse(date_str) if date_str else updated
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = updated
        entry = ET.SubElement(channel, "item")
        ET.SubElement(entry, "title").text = item["title"]
        ET.SubElement(entry, "link").text = item["url"]
        ET.SubElement(entry, "guid", {"isPermaLink": "false"}).text = item["id"]
        ET.SubElement(entry, "pubDate").text = format_datetime(dt)
        ET.SubElement(entry, "description").text = " ".join(
            part for part in [item.get("summary", ""), item.get("analysis_notice", "")] if part
        )
    return ET.tostring(rss, encoding="unicode", xml_declaration=True)


def attention_label(value: int) -> str:
    return "核心关注" if value >= 5 else "重点跟踪" if value >= 3 else "一般参考"


def build_site(policies: list[dict[str, Any]], statuses: list[dict[str, Any]], rules: dict[str, Any]) -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html", "xml"]))
    template = env.get_template("index.html.j2")
    source_names = sorted({item.get("source_name", "") for item in policies if item.get("source_name")})
    applicable_entities = list(rules.get("applicable_entity_patterns", {})) + ["多类主体或行业通用", "未明确"]
    document_types = list(rules.get("policy_type_patterns", {})) + ["其他政策材料"]
    core_count = sum(1 for item in policies if int(item.get("importance", 0)) >= 5)
    today = datetime.now(timezone.utc).date().isoformat()
    today_count = sum(1 for item in policies if (item.get("published_at") or "") == today)
    html_output = template.render(
        policies=policies,
        statuses=statuses,
        source_names=source_names,
        applicable_entities=applicable_entities,
        document_types=document_types,
        total_count=len(policies),
        core_count=core_count,
        today_count=today_count,
        generated_at=now_iso(),
        attention_label=attention_label,
    )
    (PUBLIC_DIR / "index.html").write_text(html_output, encoding="utf-8")
    (PUBLIC_DIR / "feed.xml").write_text(
        build_feed(policies, site_url=str(rules.get("site_url") or "https://dontbetoolate.github.io/finpolicy-radar/")),
        encoding="utf-8",
    )
    write_json(PUBLIC_DIR / "policies.json", policies)
    write_json(PUBLIC_DIR / "status.json", statuses)


def run(crawl: bool = True) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    source_config = load_yaml(ROOT / "config" / "sources.yaml")
    rules = load_yaml(ROOT / "config" / "rules.yaml")
    rules["_sources"] = source_config.get("sources", [])
    existing = normalize_existing_policies(load_json(DATA_DIR / "policies.json", []), rules)
    session = make_session()
    incoming: list[dict[str, Any]] = []
    existing_urls = {canonical_url(item.get("url", "")) for item in existing if item.get("url")}
    statuses = []

    if crawl:
        for source in source_config.get("sources", []):
            started = time.time()
            status = {"source_id": source["id"], "source_name": source["name"], "checked_at": now_iso()}
            try:
                candidates = discover_candidates(session, source, rules)
                accepted = 0
                errors = 0
                skipped_known = 0
                for candidate in candidates:
                    if canonical_url(candidate.url) in existing_urls:
                        skipped_known += 1
                        continue
                    try:
                        item = process_candidate(session, candidate, rules)
                        if item:
                            incoming.append(item)
                            existing_urls.add(canonical_url(item["url"]))
                            accepted += 1
                    except Exception as exc:  # keep one bad article from breaking the whole source
                        errors += 1
                        logging.warning("Article failed %s: %s", candidate.url, exc)
                    time.sleep(float(rules.get("request_delay_seconds", 0.5)))
                status.update({"ok": True, "candidates": len(candidates), "accepted": accepted, "skipped_known": skipped_known, "article_errors": errors})
            except Exception as exc:
                logging.exception("Source failed: %s", source["name"])
                status.update({"ok": False, "error": str(exc)[:300], "candidates": 0, "accepted": 0})
            status["duration_seconds"] = round(time.time() - started, 2)
            statuses.append(status)
    else:
        statuses = load_json(DATA_DIR / "status.json", [])

    policies = merge_policies(existing, incoming)
    write_json(DATA_DIR / "policies.json", policies)
    write_json(DATA_DIR / "status.json", statuses)
    build_site(policies, statuses, rules)
    logging.info("Built %s policies (%s new/updated)", len(policies), len(incoming))


def main() -> None:
    parser = argparse.ArgumentParser(description="金策雷达 MVP")
    parser.add_argument("--build-only", action="store_true", help="不联网抓取，仅用已有数据生成网页")
    args = parser.parse_args()
    run(crawl=not args.build_only)


if __name__ == "__main__":
    main()
