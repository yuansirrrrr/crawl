import asyncio
import json
import os
import re
import logging
from pathlib import Path
from datetime import datetime
from urllib.parse import quote as urlquote

import requests
from bs4 import BeautifulSoup

from yuan.llm import get_llm

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = Path(__file__).parent.parent.parent / "downloads"

_HTML_SESSION = requests.Session()
_HTML_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
})

HTML_GENERATOR_PROMPT = """你是一个前端网页设计师和内容编辑。请根据以下素材，为一个中文用户生成一个精美的单页 HTML 网页。

## 要求
1. 输出完整的 HTML 文档（DOCTYPE、head、body），CSS 写在 <style> 标签内，JS 写在 <script> 标签内
2. 使用纯原生 JavaScript（无外部库和框架），JS 放在页面底部或 DOMContentLoaded 事件中
3. 网页主题：{topic}
4. 页面结构：
   - 顶部 Header：显示主题名称和生成日期
   - 内容概览区：用自然语言综合概括该主题下的核心观点（基于文章摘要和视频信息）
   - 导航栏：提供快速跳转到视频列表和文章区的锚点链接
   - 视频列表区：用 <video controls> 标签展示每个视频，显示标题
   - 文章区：每篇文章显示标题、摘要和"阅读原文"链接（链接到原始微信文章），点击后在新的浏览器标签页打开
   - Footer：素材来源标注（抖音 · 微信公众号）
5. 交互功能要求（用原生 JS 实现）：
   - 顶部导航栏锚点跳转，页面滚动时高亮当前所在区块
   - 视频列表支持切换网格视图和列表视图（网格 2 列并排，列表单列）
   - 返回顶部按钮（页面滚动一定距离后出现）
   - 页面加载时有简单的渐入动画效果
6. 样式要求：现代简洁、中文友好、适配移动端、有良好的间距和层级感
7. 视频路径使用下面素材中提供的 src_path 值，直接作为 <video> 的 src（已经是相对于 HTML 文件的正确路径）
8. 只输出 HTML 代码，不要输出其他任何内容。不要用 Markdown 代码块包裹。

## 素材

### 视频（共 {video_count} 个）
{video_list}

### 文章（共 {article_count} 篇）
{article_summaries}"""


def _fetch_article_text_from_url(url: str) -> str:
    """从微信文章 URL 实时获取全文纯文本。"""
    if not url or not url.startswith("http"):
        return ""
    try:
        resp = _HTML_SESSION.get(url, headers={"Referer": "https://mp.weixin.qq.com/"}, timeout=15, allow_redirects=True)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        content = soup.select_one("div.rich_media_content")
        if content:
            return content.get_text(separator="\n", strip=True)
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        logger.error(f"Failed to fetch article from {url}: {e}")
        return ""


def _collect_topics() -> dict[str, list[dict]]:
    """扫描 downloads/ 目录，按话题名分组素材。

    返回 {topic_name: [{"type": "douyin"/"wechat", "url": str, "title": str, ...}]}
    """
    if not DOWNLOADS_DIR.exists():
        return {}

    topics: dict[str, list[dict]] = {}

    for dirpath in sorted(DOWNLOADS_DIR.iterdir()):
        if not dirpath.is_dir():
            continue
        match = re.match(r"^(.+?)_\d{8}_\d{6}$", dirpath.name)
        if not match:
            continue
        topic_name = match.group(1)

        if topic_name not in topics:
            topics[topic_name] = []

        # 收集视频
        douyin_dir = dirpath / "douyin"
        if douyin_dir.exists():
            for f in sorted(douyin_dir.glob("*.mp4")):
                # HTML 保存在 downloads/，所以路径要包含话题目录名
                rel_path = os.path.relpath(f, DOWNLOADS_DIR).replace("\\", "/")
                topics[topic_name].append({
                    "type": "douyin",
                    "path": f,
                    "title": f.stem,
                    "src_path": rel_path,
                })

        # 收集文章（从 JSON 元数据读取 URL）
        wechat_dir = dirpath / "wechat"
        if wechat_dir.exists():
            for f in sorted(wechat_dir.glob("*.json")):
                try:
                    meta = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                topics[topic_name].append({
                    "type": "wechat",
                    "path": f,
                    "title": meta.get("title", f.stem),
                    "url": meta.get("url", ""),
                })

    return topics


async def _summarize_article(title: str, url: str) -> str:
    """从 URL 实时获取文章全文，然后用 LLM 概括。"""
    text = _fetch_article_text_from_url(url)
    if not text:
        logger.warning(f"No text extracted from article: {title}")
        return "暂无可获取的全文内容。"

    llm = get_llm()
    prompt = f"""你是一位资深的内容分析师，擅长深度阅读和提炼文章核心信息。请对以下文章进行全面、详细的摘要。

## 摘要要求
1. **主题概括**：开篇明确点出文章的核心主题和写作意图
2. **内容脉络**：按文章逻辑顺序，详细梳理每个重要部分的关键信息，不要遗漏重要细节
3. **事实与数据**：提取文章中提到的具体案例、统计数据、时间节点等支撑性信息
4. **观点与结论**：准确提炼作者的核心论点、分析视角和最终结论
5. **背景信息**：如果文章涉及特定背景（如案件、事件、人物），补充说明相关上下文
6. **语言风格**：使用准确、流畅的书面中文，段落之间过渡自然
7. **篇幅要求**：不少于 500 字，确保内容充实、信息完整

请直接输出摘要内容，不要加"以下是摘要"等前缀，也不要用列表格式，用连贯的段落文字。

文章标题：{title}
文章内容：
{text}"""
    resp = await llm.ainvoke(prompt)
    return resp.content


async def _generate_html_with_llm(
    topic: str,
    videos: list[dict],
    articles: list[dict],
    summaries: dict[str, str],
) -> str:
    """调用 LLM 生成完整的 HTML 页面。"""
    video_lines = []
    for v in videos:
        encoded_path = urlquote(v["src_path"], safe="/")
        video_lines.append(f"- 标题: {v['title']} | src_path: {encoded_path}")
    video_list_text = "\n".join(video_lines) if video_lines else "无视频素材"

    article_lines = []
    for a in articles:
        summary = summaries.get(a["title"], "暂无摘要")
        url = a.get("url", "")
        article_lines.append(f"### {a['title']}\n摘要: {summary}\n原文链接: {url}")
    article_summaries_text = "\n\n".join(article_lines) if article_lines else "无文章素材"

    prompt = HTML_GENERATOR_PROMPT.format(
        topic=topic,
        video_count=len(videos),
        article_count=len(articles),
        video_list=video_list_text,
        article_summaries=article_summaries_text,
    )

    llm = get_llm()
    resp = await llm.ainvoke(prompt)
    html = resp.content

    html = re.sub(r"^```html?\s*\n", "", html)
    html = re.sub(r"\n```\s*$", "", html)
    html = html.strip()

    return html


async def generate_all() -> list[str]:
    """主入口：扫描所有话题，逐个生成 HTML。返回生成的文件路径列表。"""
    topics = _collect_topics()
    if not topics:
        logger.info("No topics found in downloads directory.")
        return []

    output_paths = []

    for topic_name, items in topics.items():
        videos = [i for i in items if i["type"] == "douyin"]
        articles = [i for i in items if i["type"] == "wechat"]

        if not videos and not articles:
            continue

        # 并发获取文章全文并生成摘要
        if articles:
            logger.info(f"Summarizing {len(articles)} articles...")
            tasks = [
                _summarize_article(a["title"], a.get("url", ""))
                for a in articles
            ]
            summary_results = await asyncio.gather(*tasks)
            summaries = {a["title"]: s for a, s in zip(articles, summary_results)}
        else:
            summaries = {}

        logger.info(f"Generating HTML for topic: {topic_name}")
        try:
            html_content = await _generate_html_with_llm(topic_name, videos, articles, summaries)
        except Exception as e:
            logger.error(f"LLM HTML generation failed for {topic_name}: {e}")
            continue

        safe_name = topic_name.replace("/", "_").replace("\\", "_")
        output_path = DOWNLOADS_DIR / f"{safe_name}_汇总_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        output_path.write_text(html_content, encoding="utf-8")
        output_paths.append(str(output_path))
        logger.info(f"Generated: {output_path}")

    return output_paths
