import json
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
    total_seen = 0
    page = 1
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
    resp.encoding = resp.apparent_encoding or "gbk"

    parts = re.findall(r"url\s*\+=\s*'([^']+)'", resp.text)
    if parts:
        return "".join(parts)

    m = re.search(r'window\.location\.href\s*=\s*["\']([^"\']+)["\']', resp.text)
    if m:
        return m.group(1)

    return sogou_url


def _sanitize_filename(title: str) -> str:
    """Remove characters illegal in Windows filenames, trim to reasonable length."""
    for ch in r'<>:"/\|?*':
        title = title.replace(ch, "")
    title = title.replace("\n", " ").replace("\r", " ")
    return " ".join(title.split())[:80] or "untitled"


def _fetch_article_meta(sogou_url: str) -> dict:
    """Fetch article page via Sogou link, extract metadata and resolve working URL."""
    if "weixin.sogou.com/link" in sogou_url:
        resolved_url = _resolve_sogou_link(_SESSION, sogou_url)
    else:
        resolved_url = sogou_url

    headers = {"Referer": "https://mp.weixin.qq.com/"}
    resp = _SESSION.get(resolved_url, headers=headers, timeout=15, allow_redirects=True)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.select_one("#activity-name, .rich_media_title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    author_tag = soup.select_one("#js_name, .rich_media_meta_nickname")
    author = author_tag.get_text(strip=True) if author_tag else ""

    return {
        "title": title,
        "author": author,
        "final_url": resolved_url,
    }


def search_wechat(
    keyword: str,
    max_results: int = 5,
    storage_dir: Path | None = None,
    seen_titles: set[str] | None = None,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Search WeChat articles via Sogou, save metadata as JSON.
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

    for article in articles:
        _human_delay()
        try:
            fetched = _fetch_article_meta(article["url"])
            if fetched.get("title"):
                article["title"] = fetched["title"]
            if fetched.get("author"):
                article["author"] = fetched["author"]
            article["final_url"] = fetched.get("final_url", article["url"])
        except Exception as e:
            logger.error(f"Failed to fetch article {article['url']}: {e}")
            article["error"] = str(e)

        local_path = ""
        final_url = article.get("final_url", article["url"])

        if wechat_dir:
            safe_name = _sanitize_filename(article.get("title", "untitled"))
            meta = {
                "title": article.get("title", ""),
                "author": article.get("author", article.get("account", "")),
                "url": final_url,
                "description": article.get("description", "")[:200],
            }
            meta_file = wechat_dir / f"{safe_name}.json"
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            local_path = str(meta_file)

        results.append({
            "source": "wechat",
            "title": article.get("title", ""),
            "url": final_url,
            "author": article.get("author", article.get("account", "")),
            "description": article.get("description", "")[:200],
            "local_path": local_path,
            "file_type": "json",
        })

    new_offset = offset + len(results)
    return results, new_offset
