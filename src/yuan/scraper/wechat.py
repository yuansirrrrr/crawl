import re
import time
import random
import logging
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from yuan.config import settings

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
})


def _human_delay(min_s: float = 1.0, max_s: float = 2.5):
    time.sleep(random.uniform(min_s, max_s))


def _sogou_search(keyword: str, max_results: int, offset: int = 0) -> list[dict]:
    """Search WeChat articles via Sogou with pagination, skipping the first `offset` results."""
    articles = []
    total_seen = 0  # total items iterated across all pages
    page = 1
    # need to scan enough pages to skip `offset` items and collect `max_results` new ones
    max_pages = max(3, ((offset + max_results) // 10) + 2)

    while len(articles) < max_results and page <= max_pages:
        url = f"https://weixin.sogou.com/weixin?type=2&query={quote(keyword)}&ie=utf8&page={page}"
        headers = {
            "Referer": "https://weixin.sogou.com/",
            "Cookie": _SESSION.cookies.get_dict().__str__(),
        }

        resp = _SESSION.get(url, headers=headers, timeout=settings.SEARCH_TIMEOUT)
        resp.encoding = "utf-8"

        if "antispider" in resp.url or "antispider" in resp.text[:500]:
            logger.error("Sogou anti-spider triggered. Try again later.")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("ul.news-list > li") or soup.select(".news-list li")

        if not items:
            break

        for li in items:
            if len(articles) >= max_results:
                break

            title_tag = li.select_one("h3 a")
            desc_tag = li.select_one("p.txt-info")
            account_tag = li.select_one("span.all-time-y2")

            if not title_tag:
                continue

            # skip the first `offset` valid items
            if total_seen < offset:
                total_seen += 1
                continue

            total_seen += 1
            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            if href.startswith("/link?"):
                href = f"https://weixin.sogou.com{href}"

            articles.append({
                "title": title,
                "url": href,
                "description": desc_tag.get_text(strip=True) if desc_tag else "",
                "account": account_tag.get_text(strip=True) if account_tag else "",
            })

        page += 1
        if page <= max_pages and len(articles) < max_results:
            _human_delay()

    return articles


def _resolve_sogou_link(session: requests.Session, sogou_url: str) -> str:
    """Resolve Sogou /link?url=... redirect to actual mp.weixin.qq.com URL."""
    resp = session.get(sogou_url, timeout=15, allow_redirects=True)
    # Sogou redirect page is usually GBK, but URL extraction doesn't care about encoding
    resp.encoding = resp.apparent_encoding or "gbk"

    # Sogou uses JS to build the real URL — extract it via regex
    # Pattern: url += 'https://mp.'; url += 'weixin.qq.c'; ...
    parts = re.findall(r"url\s*\+=\s*'([^']+)'", resp.text)
    if parts:
        return "".join(parts)

    # Fallback: look for a direct meta refresh or window.location
    m = re.search(r'window\.location\.href\s*=\s*["\']([^"\']+)["\']', resp.text)
    if m:
        return m.group(1)

    return sogou_url  # give up, return original


def _sanitize_filename(title: str) -> str:
    """Remove characters illegal in Windows filenames, trim to reasonable length."""
    for ch in r'<>:"/\|?*':
        title = title.replace(ch, "")
    title = title.replace("\n", " ").replace("\r", " ")
    return " ".join(title.split())[:80] or "untitled"


def _fetch_article(url: str) -> dict:
    """Fetch a WeChat article page, preserve original HTML with styles."""
    # Resolve Sogou redirect first
    if "weixin.sogou.com/link" in url:
        url = _resolve_sogou_link(_SESSION, url)

    headers = {"Referer": "https://mp.weixin.qq.com/"}
    resp = _SESSION.get(url, headers=headers, timeout=15, allow_redirects=True)
    # WeChat articles are always UTF-8
    resp.encoding = "utf-8"
    raw_html = resp.text

    # Parse only for text extraction, keep raw_html intact for saving
    soup = BeautifulSoup(raw_html, "html.parser")

    # Resolve lazy-loaded images in both raw HTML and soup
    # Replace data-src with src in the raw HTML string
    raw_html = re.sub(r'data-src=', 'src=', raw_html)
    # Remove data-src attributes (they're now src)
    raw_html = re.sub(r'\s*data-src="[^"]*"', '', raw_html)

    # Remove visibility:hidden from js_content div so content displays without JS.
    # We target the style attribute on the js_content div and remove it entirely.
    raw_html = re.sub(
        r'(<div[^>]*id="js_content"[^>]*)\s+style="[^"]*"[^>]*>',
        lambda m: m.group(0).replace(' style="' + re.search(r'style="([^"]*)"', m.group(0)).group(1) + '"', ''),
        raw_html,
    )
    # Simpler: just remove style attr from js_content div
    raw_html = re.sub(
        r'(<div[^>]*id="js_content"[^>]*)\s+style="[^"]*"',
        r'\1',
        raw_html,
    )

    content_div = soup.select_one("div.rich_media_content")
    text_content = content_div.get_text(separator="\n", strip=True)[:1000] if content_div else ""

    title_tag = soup.select_one("#activity-name, .rich_media_title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    author_tag = soup.select_one("#js_name, .rich_media_meta_nickname")
    author = author_tag.get_text(strip=True) if author_tag else ""

    return {
        "title": title,
        "author": author,
        "content_html": str(content_div) if content_div else "",
        "full_html": raw_html,
        "text_content": text_content,
        "final_url": resp.url,
    }


def search_wechat(
    keyword: str,
    max_results: int = 5,
    storage_dir: Path | None = None,
    seen_titles: set[str] | None = None,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Search WeChat articles via Sogou using requests, download full HTML.
    `offset` skips the first N Sogou results so successive calls get new articles.
    Returns (results, new_offset).
    """
    results = []

    logger.info(f"WeChat search: {keyword} offset={offset}")
    articles = _sogou_search(keyword, max_results, offset=offset)

    if not articles:
        logger.warning("WeChat: no articles found from Sogou search")
        return results, offset

    wechat_dir = None
    if storage_dir:
        wechat_dir = storage_dir / "wechat"
        wechat_dir.mkdir(parents=True, exist_ok=True)

    for i, article in enumerate(articles):
        _human_delay()
        try:
            fetched = _fetch_article(article["url"])
            # Only overwrite title/author if fetched values are non-empty
            if fetched.get("title"):
                article["title"] = fetched["title"]
            if fetched.get("author"):
                article["author"] = fetched["author"]
            article["full_html"] = fetched.get("full_html", "")
            article["text_content"] = fetched.get("text_content", "")
            article["final_url"] = fetched.get("final_url", article["url"])
        except Exception as e:
            logger.error(f"Failed to fetch article {article['url']}: {e}")
            article["full_html"] = ""
            article["text_content"] = article.get("description", "")
            article["error"] = str(e)

        # Save HTML to disk (use article title as filename)
        local_path = ""
        if wechat_dir and article.get("full_html"):
            safe_name = _sanitize_filename(article.get("title", "untitled"))
            filepath = wechat_dir / f"{safe_name}.html"
            filepath.write_text(article["full_html"], encoding="utf-8")
            local_path = str(filepath)

        final_url = article.get("final_url", article["url"])

        results.append({
            "source": "wechat",
            "title": article.get("title", ""),
            "url": final_url,
            "author": article.get("author", article.get("account", "")),
            "description": article.get("description", "")[:200],
            "text_content": article.get("text_content", ""),
            "local_path": local_path,
            "file_type": "html",
        })

    new_offset = offset + len(results)
    return results, new_offset
