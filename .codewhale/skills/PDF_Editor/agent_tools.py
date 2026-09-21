"""PDF_Editor 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

编辑会话按成员私有（data/<成员>/pdf_edits/），一律强制注入发送者 member。
pdf-edit = 渲染/OCR + 排版 LLM（至多两次 120s 调用）+ 重组 PDF：超时给足。
"""

from __future__ import annotations

import tool_runtime as rt
from tool_runtime import fn, s

ORDER = 95

COMMANDS = {"pdf-inspect", "pdf-edit", "pdf-list", "pdf-pages"}
CLI_TIMEOUTS = {"pdf-inspect": 120, "pdf-edit": 300}

TOOLS = {
    "inspect_pdf": "pdf-inspect",
    "edit_pdf": "pdf-edit",
    "pdf_edit_list": "pdf-list",
    "pdf_pages": "pdf-pages",
}

MEMBER_LOCKED = set(TOOLS)
DOC_TOOLS = {"edit_pdf"}
UNTRUSTED_TOOLS = {"inspect_pdf", "edit_pdf"}   # 来件 PDF 的字段/OCR 文本、排版模型转述

SCHEMAS = [
    fn("inspect_pdf", "看一份 PDF 是什么类型、几页、里面要填什么（表单字段或各页文字）。"
       "不知道这份表要哪些信息时先调它，再向用户要值。", {
        "file": s("PDF 路径（用户发来的保存路径，data 内）"),
    }, ["file"]),
    fn("pdf_pages", "只查一份 PDF 总共几页（快，不建编辑会话、不 OCR）。只要页数时用它，别用 inspect_pdf。", {
        "file": s("PDF 路径（用户发来的保存路径，data 内）"),
    }, ["file"]),
    fn("edit_pdf", "按一句指令编辑 PDF 并自动把结果发给用户：填表单、在任意位置写字、打勾、"
       "白底盖掉原内容再改写、贴签名/图片、画线、删页/旋转/重排/合并另一份 PDF。"
       "首次传 file；用户要更正结果时传上次返回的 session + 只说更正内容。", {
        "file": s("PDF 路径（首次编辑时传）"),
        "session": s("上次 edit_pdf 返回的 session（续改时传；不确定就 pdf_edit_list 查，别自己编）"),
        "instruction": s("要做的全部编辑，一句话写全，值必须是用户给的原话。"
                         "例：\"姓名填 张三；出生日期 1990-01-02；婚姻状况勾 已婚；"
                         "第 2 页签名处贴 Jim/inbox/2026-09/sig.png；删掉第 4 页\""),
        "fresh": rt.boolean("true = 丢开这份 PDF 之前的编辑，从原件重新开始"),
    }, ["instruction"]),
    fn("pdf_edit_list", "列出我的 PDF 编辑会话（找回 session 续改用）。", {}),
]

PROMPT_RULES = [
    "填写/修改 PDF：用户发来 PDF 要填或要改 → 不知道表里要什么就先 inspect_pdf → "
    "**一条消息里把缺的值一起问**（已知的不要再问，含糊的才问，绝不编造值；签名必须是用户发来的图片）"
    "→ 把全部信息写进一句 instruction 调 edit_pdf，PDF 会自动发给用户，不要再 send_file。"
    "用户说哪里不对 → 带同一 session 再调 edit_pdf，instruction 只写更正（\"名字往上挪一点\"、"
    "\"出生年改 1991\"）。结果里的 `提示:`/`警告:` 必须原意转述给用户",
]

IMAGE_ROUTES = [
    "用户此前或随文件说明想要**填写或修改**这份 PDF（如\"帮我填这个表\"\"把第 3 页删了\"）："
    "不要归档，改走 edit_pdf（file 传上面的保存路径；不知道要填什么先 inspect_pdf）。"
    "拿不准是归档还是编辑时问用户。",
]
