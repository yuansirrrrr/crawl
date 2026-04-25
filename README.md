# Yuan

对话式内容搜索工具：输入话题后，AI 自动搜索抖音和微信公众号相关内容，
下载视频/文章到本地，并合成摘要。

## 架构

```
用户 ──> CLI/对话 ──> LangGraph ReAct Agent
                          │
                    ┌───────┴───────┐
                    │               │
            search_douyin    search_wechat
            (Playwright)     (requests)
                    │               │
              抖音 API 调用     搜狗微信搜索
                    │               │
              httpx 下载 mp4   下载 HTML 文章
                    │               │
                    └───────┬───────┘
                            │
                    DeepSeek 合成摘要
```

### 核心模块

- **`src/yuan/scraper/douyin.py`** — 抖音搜索
  - 通过 Playwright 启动浏览器，访问抖音首页建立 session
  - 用 `page.evaluate()` 在浏览器上下文中调用 `fetch()` 请求抖音搜索 API
    （`/aweme/v1/web/general/search/single/`），绕过签名校验
  - 解析返回的 JSON（含干扰字符），提取视频元数据和 CDN 直链
  - 用 httpx 下载视频，以标题命名保存
- **`src/yuan/scraper/wechat.py`** — 微信文章搜索
  - 通过 requests 请求搜狗微信搜索页面
  - BeautifulSoup 解析提取标题、链接、摘要
  - 访问文章页（`mp.weixin.qq.com`），保留原始 HTML
  - 处理懒加载图片（`data-src` → `src`）
- **`src/yuan/tools.py`** — LangChain 工具
  - 将抖音/微信搜索函数封装为 `@tool`，供 LLM 调用
  - 返回格式化文本（标题、作者、本地路径）
- **`src/yuan/agent.py`** — LangGraph ReAct Agent
  - 使用 `create_react_agent` 构建 function calling agent
  - 系统提示词引导 LLM 按需调用搜索工具，并综合结果生成摘要
- **`src/yuan/cli.py`** — 终端入口
  - `uv run yuan chat` — 启动对话
  - `uv run yuan serve` — 启动 FastAPI 服务

## 安装

```bash
# 克隆仓库后，安装依赖
uv sync

# 安装 Playwright 浏览器
uv run playwright install chromium
```

## 配置

复制 `.env.example` 为 `.env`，填入 DeepSeek API Key：

```env
DEEPSEEK_API_KEY=sk-xxx
```

## 首次登录抖音

抖音搜索需要登录状态。首次使用需要登录一次，session 保存在本地：

```bash
uv run python fetch_cookies.py
```

浏览器窗口会打开，进入抖音页面。如果已登录直接按 Enter；
如果没有登录，先扫码或手机号登录，完成后按 Enter。
Session 保存后，后续搜索会自动使用保存的 session。

## 使用

### 对话模式

```bash
uv run yuan chat
```

启动后输入话题，AI 会自动搜索并返回摘要：

```
你: 搜索人工智能，要10条

助手: 抖音搜索「人工智能」共找到 10 条视频，已保存至: downloads/...
      1. 《xxx》 作者: xxx | downloads/...
      ...
```

支持自然语言指定数量：
- "搜索量子计算，每个平台5条"
- "只看抖音，搜索机器学习"
- "搜一下新能源车，要15条"

### API 模式

```bash
uv run yuan serve --port 8000

# 调用接口
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "搜索人工智能，要10条"}'
```

## 存储结构

```
downloads/
└── 话题_20260425_140000/
    ├── douyin/
    │   ├── 视频标题1.mp4
    │   └── 视频标题2.mp4
    └── wechat/
        ├── 文章标题1.html
        └── 文章标题2.html
```

## 常见问题

### 抖音搜索返回空结果或报错 `verify_check`

这是抖音的临时风控，不是账号被封。可能原因：

1. **搜索频率过高** — 短时间内请求太多，触发风控
   - 等待 10~30 分钟自动解除，或换网络（手机热点）
2. **删除了 `browser_data/douyin_profile` 目录** — 新 profile 缺少信任指纹
   - 运行 `uv run python fetch_cookies.py` 重新登录
   - 登录后等待页面加载稳定（至少 10 秒）再按 Enter
   - 建议不要频繁删除 profile，一次登录后可长期使用

### 微信搜索无结果

搜狗搜索偶尔触发验证码，稍后重试即可。
