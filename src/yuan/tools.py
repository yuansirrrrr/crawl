import asyncio
import logging
from pathlib import Path

from langchain_core.tools import tool

from yuan.config import settings

logger = logging.getLogger(__name__)


@tool
async def search_douyin_tool(keyword: str, max_results: int = 5) -> str:
    """搜索抖音视频并下载到本地。返回视频标题、作者和本地路径列表。

    Args:
        keyword: 搜索关键词
        max_results: 最多返回几条结果，默认 5
    """
    from yuan.scraper.douyin import search_douyin

    storage = settings.storage_dir(keyword)
    try:
        results = await search_douyin(keyword=keyword, max_results=max_results, storage_dir=storage)
    except Exception as e:
        logger.error(f"Douyin search failed: {e}")
        return f"抖音搜索失败: {e}"

    if not results:
        return "抖音未找到相关视频。"

    lines = [f"抖音搜索「{keyword}」共找到 {len(results)} 条视频，已保存至: {storage}"]
    for i, r in enumerate(results, 1):
        path = r.get("local_path", "未下载")
        lines.append(f"{i}. 《{r.get('title', '无标题')}》 作者: {r.get('author', '未知')} | {path}")
    return "\n".join(lines)


@tool
def search_wechat_tool(keyword: str, max_results: int = 5) -> str:
    """搜索微信公众号文章并下载 HTML 到本地。返回文章标题、作者和本地路径列表。

    Args:
        keyword: 搜索关键词
        max_results: 最多返回几条结果，默认 5
    """
    from yuan.scraper.wechat import search_wechat

    storage = settings.storage_dir(keyword)
    try:
        results = search_wechat(keyword=keyword, max_results=max_results, storage_dir=storage)
    except Exception as e:
        logger.error(f"WeChat search failed: {e}")
        return f"微信搜索失败: {e}"

    if not results:
        return "微信公众号未找到相关文章。"

    lines = [f"微信搜索「{keyword}」共找到 {len(results)} 篇文章，已保存至: {storage}"]
    for i, r in enumerate(results, 1):
        path = r.get("local_path", "未下载")
        text = r.get("text_content", r.get("description", ""))[:150]
        lines.append(f"{i}. 《{r.get('title', '无标题')}》 作者: {r.get('author', '未知')}")
        if text:
            lines.append(f"   摘要: {text}")
        lines.append(f"   路径: {path}")
    return "\n".join(lines)
