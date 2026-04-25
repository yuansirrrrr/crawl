import json
import logging
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from playwright.async_api import async_playwright

from yuan.config import settings

logger = logging.getLogger(__name__)

DOUYIN_PROFILE_DIR = Path(__file__).parent.parent.parent.parent / "browser_data" / "douyin_profile"


def _parse_cookie_string(cookie_str: str) -> list[dict]:
    cookies = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".douyin.com",
                "path": "/",
                "httpOnly": False,
                "secure": False,
            })
    return cookies


def _extract_video_url(video_info: dict) -> str:
    """Select the best video download URL."""
    play_addr = video_info.get("play_addr", {})
    url_list = play_addr.get("url_list", [])
    if not url_list:
        return ""
    candidates = [u for u in url_list if "/audio/" not in u] or url_list
    for u in candidates:
        if "video/tos" in u and ".mp4" in u:
            return u
    for u in candidates:
        if ".mp4" in u:
            return u
    return candidates[0]


async def search_douyin(
    keyword: str,
    max_results: int = 5,
    storage_dir: Path | None = None,
    seen_ids: set[str] | None = None,
) -> tuple[list[dict], int]:
    """Search Douyin via Playwright browser API call, download videos.
    Returns (results, skipped_count).
    """
    results = []
    skipped = 0
    _external_seen = seen_ids is not None
    if seen_ids is None:
        seen_ids = set()

    DOUYIN_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(DOUYIN_PROFILE_DIR),
            headless=True,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            accept_downloads=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Inject cookies from config before navigation
        if settings.DOUYIN_COOKIES:
            await context.add_cookies(_parse_cookie_string(settings.DOUYIN_COOKIES))
            logger.debug("Cookies injected from config")

        # Visit douyin.com to establish session
        await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # 抖音 API 单次最多返回约 20 条有效视频（offset>0 始终返回空）
        # count 设为 min(max_results*2, 30) 尽量多拿，30 是 API 上限
        page_size = min(max(max_results * 2, 20), 30)

        api_url = (
            f"https://www.douyin.com/aweme/v1/web/general/search/single/?"
            f"keyword={quote(keyword)}&search_channel=aweme_video_web&"
            f"search_source=normal_search&query_correct_type=1&is_filter_search=0&"
            f"offset=0&count={page_size}&aid=6383&device_platform=webapp"
        )

        response_text = await page.evaluate(f"""
            async () => {{
                try {{
                    const resp = await fetch("{api_url}", {{
                        credentials: "include",
                        headers: {{ "Accept": "application/json" }}
                    }});
                    return await resp.text();
                }} catch (e) {{
                    return "ERROR:" + e.message;
                }}
            }}
        """)

        if response_text.startswith("ERROR:"):
            logger.error(f"API call failed: {response_text}")
        else:
            cleaned = re.sub(r'\d+\s*([{{\[])', r'\1', response_text)
            cleaned = re.sub(r'([}}\]])\s*\d+', r'\1', cleaned)

            body = None
            for ch in ['{', '[']:
                idx = cleaned.find(ch)
                if idx >= 0:
                    try:
                        body = json.loads(cleaned[idx:])
                        break
                    except json.JSONDecodeError:
                        continue

            if not body:
                logger.warning("Failed to parse API response")
            else:
                status_code = body.get("status_code", -1)
                data_list = body.get("data", [])
                logger.debug(f"status={status_code}, data={len(data_list)}")

                if status_code != 0 or not data_list:
                    logger.warning(f"No results (status={status_code})")
                else:
                    for item in data_list:
                        if len(results) >= max_results:
                            break

                        aweme = item.get("aweme_info", {})
                        if not aweme:
                            continue

                        aweme_id = str(aweme.get("aweme_id", ""))
                        if not aweme_id:
                            continue
                        if aweme_id in seen_ids:
                            if _external_seen:
                                skipped += 1
                            continue
                        seen_ids.add(aweme_id)

                        if aweme.get("images"):
                            continue

                        video_info = aweme.get("video", {})
                        if not video_info:
                            continue

                        video_url = _extract_video_url(video_info)
                        if not video_url:
                            logger.debug(f"No valid video URL for aweme_id={aweme_id}, skipping")
                            continue

                        author_info = aweme.get("author", {})
                        cover_info = video_info.get("cover", {})
                        cover_url_list = cover_info.get("url_list", [])

                        results.append({
                            "source": "douyin",
                            "title": aweme.get("desc", ""),
                            "url": f"https://www.douyin.com/video/{aweme_id}",
                            "author": author_info.get("nickname", ""),
                            "description": aweme.get("desc", "")[:200],
                            "video_id": aweme_id,
                            "video_url": video_url,
                            "thumbnail": cover_url_list[0] if cover_url_list else "",
                        })

        await context.close()

    if not results:
        logger.warning("Douyin: no qualifying results found")

    # Download videos
    if storage_dir and results:
        douyin_dir = storage_dir / "douyin"
        douyin_dir.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(results):
            try:
                if r.get("video_url"):
                    local_path = await _download_douyin_video(
                        r["video_url"], r.get("title", ""), r.get("video_id", ""), douyin_dir, i
                    )
                    r["local_path"] = local_path
                    r["file_type"] = "mp4"
            except Exception as e:
                logger.error(f"Failed to download Douyin video: {e}")
                r["error"] = str(e)

    return results, skipped


def _sanitize_filename(title: str) -> str:
    """Remove characters illegal in Windows filenames, trim to reasonable length."""
    for ch in r'<>:"/\|?*':
        title = title.replace(ch, "")
    title = title.replace("\n", " ").replace("\r", " ")
    return " ".join(title.split())[:80] or "untitled"


async def _download_douyin_video(video_url: str, title: str, video_id: str, dest_dir: Path, index: int) -> str:
    """Download a Douyin video, using title as filename."""
    safe_name = _sanitize_filename(title) if title else None
    if not safe_name:
        safe_id = video_id.split("/")[-1] if "/" in video_id else video_id
        safe_name = safe_id or f"video_{index}"
    filename = f"{safe_name}.mp4"
    filepath = dest_dir / filename

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
        "Accept": "video/mp4,*/*;q=0.5",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=settings.DOWNLOAD_TIMEOUT) as client:
        resp = await client.get(video_url, headers=headers)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)

    return str(filepath)
