"""Any_Search 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。"""

from __future__ import annotations


from tool_runtime import fn, s, int_

ORDER = 80

COMMANDS = {"any-search", "any-extract", "any-subdomains", "any-images"}

CLI_TIMEOUTS = {"any-images": 120}  # 搜索 25s + Bing 兜底 10s + 每张下载 ≤10s

TOOLS = {
    "anysearch_search": "any-search",
    "anysearch_extract": "any-extract",
    "anysearch_subdomains": "any-subdomains",
    "fetch_images": "any-images",
}

UNTRUSTED_TOOLS = {"anysearch_search", "anysearch_extract"}
MEMBER_LOCKED = {"fetch_images"}   # 下载落到发送者自己的目录
IMAGE_TOOLS = {"fetch_images"}     # 返回每行一个图片路径，传输层逐张发

SCHEMAS = [
    fn("anysearch_search", "高质量实时联网搜索（AnySearch）。比 web_search 更准，"
       "查最新资讯/事实/股价/学术/健康等首选。需要垂直领域结构化结果时（finance/health/"
       "academic/travel/code 等），先用 anysearch_subdomains 拿到 sub_domain 再传 domain/sub_domain。"
       "返回结果原文，你据此用中文总结报告", {
        "query": s("搜索关键词/问题"),
        "domain": s("垂直领域（可选）", enum=[
            "general", "resource", "social_media", "finance", "academic", "legal",
            "health", "business", "security", "ip", "code", "energy",
            "environment", "agriculture", "travel", "film", "gaming"]),
        "sub_domain": s("子域路由键（如 finance.quote），垂直搜索时配 domain；先用 anysearch_subdomains 发现"),
        "sub_domain_params": s("子域参数，key=value,key2=value2 或 JSON（schema 见 anysearch_subdomains 输出）"),
        "max_results": int_("返回结果数 1-10（默认 10）"),
    }, ["query"]),
    fn("anysearch_extract", "抓取并提取一个网页链接的全文（AnySearch，markdown）。"
       "用户发链接让看/总结、或搜索摘要不够需读全文时用。返回正文，你据此总结", {
        "url": s("网页 URL"),
    }, ["url"]),
    fn("anysearch_subdomains", "查某垂直领域的可用子域及参数 schema（垂直 anysearch_search 前的发现步骤）。"
       "返回 domain/sub_domain/query_format/params_schema 表", {
        "domains": s("单个或逗号分隔的多个领域，如 finance 或 finance,health"),
    }, ["domains"]),
    fn("fetch_images", "从网上找图片并直接发给用户（用户要\"看X的图片/照片\"\"X长什么样\"）。"
       "图片由系统自动发出，你只需一句话说明找的是什么；返回 [错误] 就如实说没找到", {
        "query": s("要找的图片内容；图库以英文为主，中文名词先译成英文（螳螂 → praying mantis）"),
        "count": int_("张数 1-5（默认 3）"),
    }, ["query"]),
]

PROMPT_RULES = [
    '用户问"最新新闻/外面在发生什么/帮我查一下X" → 优先 anysearch_search（更准，可选 domain 垂直搜索：finance/health/academic/travel/code 等，先 anysearch_subdomains 发现子域）；web_search 为备选。发链接让看/总结文章 → anysearch_extract（备选 web_read）；发 YouTube 链接让总结 → youtube_summarize。工具返回抓取到的原文，你据此用中文总结报告；抓取失败就如实说没查到，别编造',
    '用户要看某样东西的图片/照片/长什么样 → fetch_images（图片自动发出，不要在回复里贴链接或路径）',
]
