"""Web_Reach 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。"""

from __future__ import annotations


from tool_runtime import fn, s

ORDER = 70

COMMANDS = {"web-search", "web-read", "yt-summary"}

CLI_TIMEOUTS = {"web-search": 45}  # RapidAPI 20s + DuckDuckGo fallback 20s

TOOLS = {
    "web_search": "web-search",
    "web_read": "web-read",
    "youtube_summarize": "yt-summary",
}

SCHEMAS = [
    fn("web_search", "联网搜索最新资讯/新闻/动态（用户问\"最新新闻\"\"外面在发生什么\"\"帮我查一下X\"）。"
       "返回抓取到的网页结果原文，你据此用中文总结报告", {
        "query": s("搜索关键词/问题"),
    }, ["query"]),
    fn("web_read", "抓取并阅读一个网页链接（用户发链接让看/总结文章时）。返回网页正文，你据此总结", {
        "url": s("网页 URL"),
    }, ["url"]),
    fn("youtube_summarize", "获取 YouTube 视频字幕转写（用户发 YouTube 链接让总结时）。"
       "返回字幕全文（无字幕则返回标题+简介），你据此用中文总结视频内容", {
        "url": s("YouTube 视频 URL"),
    }, ["url"]),
]
