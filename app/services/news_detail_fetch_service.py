"""新闻详情原文抓取服务。

当前版本只做新闻详情原文补全，不做新闻级 LLM 分析。
点击 /api/stocks/news/{news_id} 时，如果 content_text 为空且 url 存在，则尝试抓取网页正文。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import requests
from sqlalchemy.orm import Session


class _VisibleTextParser(HTMLParser):
    """用标准库提取网页可见文本，避免新增 BeautifulSoup 依赖。"""

    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: list[str] = []
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "canvas", "iframe"}:
            self._skip_stack.append(tag)
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}:
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
        raw = unescape(" ".join(self._chunks))
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*", "\n", raw)
        raw = re.sub(r"\s+\n", "\n", raw)
        raw = re.sub(r"\n\s+", "\n", raw)
        return raw.strip()


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


def _download_html(url: str) -> str:
    timeout = _env_int("NEWS_DETAIL_FETCH_TIMEOUT_SECONDS", 10)
    max_html_chars = _env_int("NEWS_DETAIL_MAX_HTML_CHARS", 200000)

    headers = {
        "User-Agent": os.getenv(
            "NEWS_DETAIL_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }

    response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        raise RuntimeError(f"unsupported content-type: {content_type}")

    return response.text[:max_html_chars]


def _extract_text_from_html(html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(html)
    text = parser.text()

    min_chars = _env_int("NEWS_DETAIL_MIN_TEXT_CHARS", 200)
    if len(text) < min_chars:
        return ""

    max_chars = _env_int("NEWS_DETAIL_MAX_TEXT_CHARS", 12000)
    return text[:max_chars]


def enrich_news_detail_if_needed(
    db: Session,
    news: Any,
    *,
    include_html: bool = False,
    force_fetch: bool = False,
) -> Any:
    """按需补全单条新闻详情。"""
    if not news:
        return news

    if not _env_bool("ENABLE_NEWS_DETAIL_FETCH", True):
        return news

    content_text = getattr(news, "content_text", None)
    content_status = getattr(news, "content_status", None)

    if content_text and not force_fetch:
        if not content_status and hasattr(news, "content_status"):
            news.content_status = "fetched"
            db.commit()
            db.refresh(news)
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
        text = _extract_text_from_html(html)

        if not text:
            raise RuntimeError("downloaded html but extracted visible text is too short")

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
