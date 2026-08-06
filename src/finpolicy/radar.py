from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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


@dataclass
class Candidate:
    title: str
    url: str
    source_id: str
    source_name: str
    source_weight: int
    list_date: str | None = None


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


def discover_candidates(session: requests.Session, source: dict[str, Any], rules: dict[str, Any]) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    max_items = int(rules.get("max_candidates_per_source", 40))
    timeout = int(rules.get("request_timeout_seconds", 25))

    for start_url in source["start_urls"]:
        page, final_url = fetch(session, start_url, timeout)
        soup = BeautifulSoup(page, "html.parser")
        for tag in soup.find_all("a", href=True):
            title = clean_text(tag.get_text(" ", strip=True) or tag.get("title", ""))
            if len(title) < 7 or title in {"更多", "详情", "查看详情", "下一页", "上一页"}:
                continue
            url = canonical_url(urljoin(final_url, tag["href"]))
            if not url.startswith(("http://", "https://")):
                continue
            if not domain_allowed(url, source.get("allowed_domains", [])):
                continue
            if not url_pattern_allowed(url, source.get("include_url_patterns", []), source.get("exclude_url_patterns", [])):
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


def largest_content_block(soup: BeautifulSoup) -> str:
    selectors = [
        "article",
        "main",
        ".article",
        ".article-content",
        ".content",
        ".TRS_Editor",
        "#UCAP-CONTENT",
        ".pages_content",
        ".detail",
    ]
    blocks = []
    for selector in selectors:
        for node in soup.select(selector):
            text = clean_text(node.get_text(" ", strip=True))
            if len(text) >= 80:
                blocks.append(text)
    if not blocks:
        for node in soup.find_all(["div", "section"]):
            text = clean_text(node.get_text(" ", strip=True))
            if 100 <= len(text) <= 50000:
                blocks.append(text)
    return max(blocks, key=len, default="")


def parse_article(session: requests.Session, candidate: Candidate, rules: dict[str, Any]) -> dict[str, Any]:
    timeout = int(rules.get("request_timeout_seconds", 25))
    page, final_url = fetch(session, candidate.url, timeout)
    soup = BeautifulSoup(page, "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "footer", "form", "iframe"]):
        node.decompose()

    h1 = soup.find("h1")
    title = clean_text(h1.get_text(" ", strip=True)) if h1 else candidate.title
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


def document_type(text: str, rules: dict[str, Any]) -> str:
    for kind, keywords in rules.get("policy_type_keywords", {}).items():
        if any(keyword in text for keyword in keywords):
            return kind
    return "政策信息"


def concise_summary(content: str, title: str) -> str:
    text = clean_text(content)
    text = re.sub(r"^(当前位置|首页)[^。]{0,100}", "", text)
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


def importance(source_weight: int, score: int, dtype: str, title: str) -> int:
    value = source_weight + min(score, 12)
    if dtype in {"法律法规", "规范性文件", "征求意见"}:
        value += 3
    if any(word in title for word in ["办法", "规定", "指导意见", "条例", "通知", "公告", "规划"]):
        value += 2
    if value >= 17:
        return 5
    if value >= 13:
        return 4
    if value >= 9:
        return 3
    if value >= 6:
        return 2
    return 1


def impact_text(categories: list[str], rules: dict[str, Any]) -> str:
    templates = rules.get("impact_templates", {})
    values = [templates[c] for c in categories if c in templates]
    return values[0] if values else "可能影响金融机构的产品、技术、数据或合规管理，具体要求应以官方原文为准。"


def process_candidate(session: requests.Session, candidate: Candidate, rules: dict[str, Any]) -> dict[str, Any] | None:
    article = parse_article(session, candidate, rules)
    combined = " ".join([article["title"], article.get("content", "")[:4000]])
    score, hits = relevance_score(combined, rules)
    if score < int(rules.get("minimum_relevance_score", 2)):
        return None
    categories = classify(combined, rules)
    dtype = document_type(article["title"] + " " + article.get("content", "")[:1200], rules)
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
        "categories": categories,
        "keywords": hits[:10],
        "relevance_score": score,
        "importance": importance(candidate.source_weight, score, dtype, article["title"]),
        "summary": concise_summary(article.get("content", ""), article["title"]),
        "impact": impact_text(categories, rules),
        "analysis_notice": "以上摘要与影响提示由规则自动生成，不代表发布机构观点；请以官方原文为准。",
    }


def policy_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (item.get("published_at") or item.get("discovered_at") or "", item.get("discovered_at") or "")


def merge_policies(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = {item["id"]: item for item in existing}
    url_index = {canonical_url(item["url"]): item["id"] for item in existing if item.get("url")}
    for item in incoming:
        existing_id = url_index.get(canonical_url(item["url"]))
        if existing_id and existing_id in index:
            old_discovered = index[existing_id].get("discovered_at")
            item["id"] = existing_id
            item["discovered_at"] = old_discovered or item["discovered_at"]
        index[item["id"]] = item
    return sorted(index.values(), key=policy_sort_key, reverse=True)


def build_feed(policies: list[dict[str, Any]], site_title: str = "金策雷达") -> str:
    updated = datetime.now(timezone.utc)
    entries = []
    for item in policies[:50]:
        date_str = item.get("published_at") or item.get("discovered_at")
        try:
            dt = date_parser.parse(date_str) if date_str else updated
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = updated
        description = html.escape(item.get("summary", "") + " " + item.get("analysis_notice", ""))
        entries.append(
            f"""<item><title>{html.escape(item['title'])}</title><link>{html.escape(item['url'])}</link>"
            f"<guid isPermaLink=\"false\">{item['id']}</guid><pubDate>{format_datetime(dt)}</pubDate>"
            f"<description>{description}</description></item>"""
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f'<title>{site_title}</title><link>./</link><description>金融科技政策自动监测</description>'
        f'<lastBuildDate>{format_datetime(updated)}</lastBuildDate>{"".join(entries)}</channel></rss>'
    )


def build_site(policies: list[dict[str, Any]], statuses: list[dict[str, Any]]) -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html", "xml"]))
    template = env.get_template("index.html.j2")
    categories = sorted({category for item in policies for category in item.get("categories", [])})
    source_names = sorted({item.get("source_name", "") for item in policies if item.get("source_name")})
    high_count = sum(1 for item in policies if int(item.get("importance", 0)) >= 4)
    today = datetime.now(timezone.utc).date().isoformat()
    today_count = sum(1 for item in policies if (item.get("published_at") or "") == today)
    html_output = template.render(
        policies=policies,
        statuses=statuses,
        categories=categories,
        source_names=source_names,
        total_count=len(policies),
        high_count=high_count,
        today_count=today_count,
        generated_at=now_iso(),
    )
    (PUBLIC_DIR / "index.html").write_text(html_output, encoding="utf-8")
    (PUBLIC_DIR / "feed.xml").write_text(build_feed(policies), encoding="utf-8")
    write_json(PUBLIC_DIR / "policies.json", policies)
    write_json(PUBLIC_DIR / "status.json", statuses)


def run(crawl: bool = True) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    source_config = load_yaml(ROOT / "config" / "sources.yaml")
    rules = load_yaml(ROOT / "config" / "rules.yaml")
    existing = load_json(DATA_DIR / "policies.json", [])
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
    build_site(policies, statuses)
    logging.info("Built %s policies (%s new/updated)", len(policies), len(incoming))


def main() -> None:
    parser = argparse.ArgumentParser(description="金策雷达 MVP")
    parser.add_argument("--build-only", action="store_true", help="不联网抓取，仅用已有数据生成网页")
    args = parser.parse_args()
    run(crawl=not args.build_only)


if __name__ == "__main__":
    main()
