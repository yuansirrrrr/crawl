"""
首次登录抖音，保存完整浏览器 session 到 profile 目录。
运行方式: uv run python fetch_cookies.py

浏览器会弹出，完成登录后在终端按 Enter，session 自动保存。
"""
import asyncio
import sys
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "browser_data" / "douyin_profile"


async def main():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright 未安装，请先运行: uv run playwright install chromium")
        sys.exit(1)

    print(f"Profile 目录: {PROFILE_DIR}")
    print("浏览器即将打开，请完成登录（扫码或手机号登录）。")
    print("登录完成后，回到终端按 Enter 保存 session。\n")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.douyin.com/", wait_until="domcontentloaded")

        print("浏览器已打开，请在浏览器中完成登录。")
        print("登录成功后按 Enter...")
        input()

        cookies = await context.cookies("https://www.douyin.com/")
        important = ["msToken", "ttwid", "odin_tt", "passport_csrf_token", "sessionid"]
        found = [c["name"] for c in cookies if c["name"] in important]
        missing = [k for k in important if k not in found]

        print(f"\n已获取 {len(cookies)} 个 cookies")
        print(f"关键 cookies: {found}")
        if missing:
            print(f"缺少（可能未登录完成）: {missing}")

        await context.close()

    print(f"\nSession 已保存到: {PROFILE_DIR}")
    print("现在可以运行: uv run yuan chat")


if __name__ == "__main__":
    asyncio.run(main())
