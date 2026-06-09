"""新闻详情原文读取与补全服务。

设计目标：
1. /api/stocks/news/{news_id} 详情接口优先返回数据库中的新闻原文 content_text；
2. 不再把 summary 当成原文返回；
3. 如果数据库没有原文，且新闻有 url，则按需抓取网页正文并写回数据库。

说明：Alpha Vantage 一类新闻源通常只提供 summary，不提供完整正文。
因此 content_text 只有在明显不是 summary 时才会被认为是“新闻原文”。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import requests
from sqlalchemy.orm import Session


_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe"}
_BLOCK_TAGS = {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}
_ARTICLE_JSON_TYPES = {"article", "newsarticle", "blogposting", "reportagenewsarticle"}


class _VisibleTextParser(HTMLParser):
    """用标准库提取网页可见文本，避免新增 BeautifulSoup 依赖。"""

    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: list[str] = []
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_stack.append(tag)
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()
        if tag in {"p", "div", "section", "article", "li"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return _clean_text(" ".join(self._chunks))


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _clean_text(text: str | None) -> str:
    if not text:
        return ""

    raw = unescape(str(text))
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\s*\n\s*", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _normalize_for_compare(text: str | None) -> str:
    text = _clean_text(text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def is_real_original_content(content_text: str | None, summary: str | None = None) -> bool:
    """判断 content_text 是否像真正新闻正文，而不是 summary 副本。"""
    content = _clean_text(content_text)
    if not content:
        return False

    min_chars = _env_int("NEWS_DETAIL_MIN_TEXT_CHARS", 200)
    if len(content) < min_chars:
        return False

    summary_norm = _normalize_for_compare(summary)
    content_norm = _normalize_for_compare(content)
    if not summary_norm:
        return True

    if content_norm == summary_norm:
        return False

    # 兼容导入脚本把 summary 写进 content_text 的情况：只要正文没有明显比摘要长，视为 summary_only。
    if content_norm in summary_norm or summary_norm in content_norm:
        if len(content_norm) <= int(len(summary_norm) * 1.35) + 80:
            return False

    # 简单 token 重合度兜底，避免标点/换行差异造成 summary 被误判成原文。
    summary_tokens = set(re.findall(r"[a-z0-9]+", summary_norm))
    content_tokens = set(re.findall(r"[a-z0-9]+", content_norm))
    if summary_tokens and content_tokens:
        overlap = len(summary_tokens & content_tokens) / max(len(summary_tokens), 1)
        if overlap >= 0.92 and len(content_norm) <= int(len(summary_norm) * 1.5) + 120:
            return False

    return True


def get_original_content_text(news: Any) -> str | None:
    """只返回数据库中的真实原文；summary 不会从这里返回。"""
    content_text = getattr(news, "content_text", None)
    summary = getattr(news, "summary", None)
    if is_real_original_content(content_text, summary):
        return _clean_text(content_text)
    return None


def news_detail_source(news: Any) -> str:
    if get_original_content_text(news):
        return "database_content_text"
    if getattr(news, "content_text", None):
        return "summary_only_or_too_short"
    if getattr(news, "content_html", None):
        return "database_content_html_only"
    return "not_available"


def _download_html(url: str) -> str:
    timeout = _env_int("NEWS_DETAIL_FETCH_TIMEOUT_SECONDS", 12)
    max_html_chars = _env_int("NEWS_DETAIL_MAX_HTML_CHARS", 300000)

    headers = {
        "User-Agent": os.getenv(
            "NEWS_DETAIL_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type and "text/plain" not in content_type and content_type:
        raise RuntimeError(f"unsupported content-type: {content_type}")

    return response.text[:max_html_chars]


def _iter_json_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_json_objects(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_json_objects(item)


def _extract_json_ld_article_body(html: str) -> str:
    # 很多新闻网页会在 JSON-LD 中放 articleBody，优先取这个，通常比可见文本更干净。
    pattern = re.compile(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    candidates: list[str] = []

    for match in pattern.finditer(html):
        raw = unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        for obj in _iter_json_objects(data):
            raw_type = obj.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            normalized_types = {str(t).lower() for t in types if t}
            if normalized_types and not (normalized_types & _ARTICLE_JSON_TYPES):
                continue

            body = obj.get("articleBody") or obj.get("text")
            if isinstance(body, str):
                candidates.append(_clean_text(body))

    return max(candidates, key=len, default="")


def _extract_article_tag_text(html: str) -> str:
    candidates: list[str] = []
    for match in re.finditer(r"<article\b[^>]*>(.*?)</article>", html, re.IGNORECASE | re.DOTALL):
        parser = _VisibleTextParser()
        parser.feed(match.group(1))
        text = parser.text()
        if text:
            candidates.append(text)
    return max(candidates, key=len, default="")


def _extract_visible_text(html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(html)
    return parser.text()


def _extract_text_from_html(html: str, summary: str | None = None) -> str:
    candidates = [
        _extract_json_ld_article_body(html),
        _extract_article_tag_text(html),
        _extract_visible_text(html),
    ]

    max_chars = _env_int("NEWS_DETAIL_MAX_TEXT_CHARS", 12000)
    clean_candidates = []
    for candidate in candidates:
        text = _clean_text(candidate)
        if is_real_original_content(text, summary):
            clean_candidates.append(text[:max_chars])

    return max(clean_candidates, key=len, default="")


def enrich_news_detail_if_needed(
    db: Session,
    news: Any,
    *,
    include_html: bool = False,
    force_fetch: bool = False,
) -> Any:
    """按需补全单条新闻详情。

    若 content_text 已经是真实原文，则直接返回。
    若 content_text 为空或只是 summary，则尝试根据 url 抓取正文并写回 content_text。
    """
    if not news:
        return news

    if get_original_content_text(news) and not force_fetch:
        if not getattr(news, "content_status", None) and hasattr(news, "content_status"):
            news.content_status = "fetched"
            db.commit()
            db.refresh(news)
        return news

    if not _env_bool("ENABLE_NEWS_DETAIL_FETCH", True):
        return news

    url = getattr(news, "url", None)
    if not url:
        if hasattr(news, "content_status"):
            news.content_status = "no_url"
        if hasattr(news, "content_fetched_at"):
            news.content_fetched_at = datetime.now()
        db.commit()
        db.refresh(news)
        return news

    try:
        html = _download_html(url)
        text = _extract_text_from_html(html, getattr(news, "summary", None))

        if not text:
            raise RuntimeError("downloaded html but original article text was not found")

        if hasattr(news, "content_text"):
            news.content_text = text
        if hasattr(news, "content_html") and (include_html or _env_bool("NEWS_DETAIL_STORE_HTML", False)):
            news.content_html = html
        if hasattr(news, "content_status"):
            news.content_status = "fetched"
        if hasattr(news, "content_fetched_at"):
            news.content_fetched_at = datetime.now()

        db.commit()
        db.refresh(news)

    except Exception as exc:
        # 不把 summary 写入 content_text，避免前端把 summary 当原文展示。
        if hasattr(news, "content_status"):
            news.content_status = "fetch_failed"
        if hasattr(news, "content_fetched_at"):
            news.content_fetched_at = datetime.now()

        for attr in ("content_error", "fetch_error", "detail_error"):
            if hasattr(news, attr):
                setattr(news, attr, str(exc)[:1000])
                break

        db.commit()
        db.refresh(news)

    return news
