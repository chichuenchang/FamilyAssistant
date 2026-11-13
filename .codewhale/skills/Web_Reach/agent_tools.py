"""Web_Reach 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。"""

from __future__ import annotations


from tool_runtime import fn, s

ORDER = 70

COMMANDS = {"web-search", "web-read", "yt-summary"}

CLI_TIMEOUTS = {"web-search": 45}  # 4 searches concurrent; worst Serper 20s + Parallel 20s

TOOLS = {
    "web_search": "web-search",
    "web_read": "web-read",
    "youtube_summarize": "yt-summary",
}

UNTRUSTED_TOOLS = set(TOOLS)

SCHEMAS = [
    fn("web_search", "联网搜索：任何要上网查的问题都用它（最新新闻/价格/事实/\"帮我查一下X\"）。"
       "系统同时用 Serper 与 AnySearch 各搜英文、中文两组关键词（中文网常更有用），"
       "无论用户用什么语言提问。返回四组结果原文，你综合后用用户的语言总结", {
        "query_en": s("从用户请求提炼的英文搜索关键词（不是整句直译）"),
        "query_zh": s("从用户请求提炼的中文搜索关键词（不是整句直译）"),
    }, ["query_en", "query_zh"]),
    fn("web_read", "抓取并阅读一个网页链接（用户发链接让看/总结文章时）。返回网页正文，你据此总结", {
        "url": s("网页 URL"),
    }, ["url"]),
    fn("youtube_summarize", "获取 YouTube 视频字幕转写（用户发 YouTube 链接让总结时）。"
       "返回字幕全文（无字幕则返回标题+简介），你据此用中文总结视频内容", {
        "url": s("YouTube 视频 URL"),
    }, ["url"]),
]
