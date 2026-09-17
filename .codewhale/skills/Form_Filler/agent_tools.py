"""Form_Filler 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

填表会话按成员私有（data/<成员>/forms/），一律强制注入发送者 member。
form-scan 平面表逐页渲染+OCR、form-render 重组 PDF：超时加长。
"""

from __future__ import annotations


import tool_runtime as rt
from tool_runtime import fn, s

ORDER = 95

COMMANDS = {"form-scan", "form-define", "form-next", "form-set",
            "form-render", "form-list", "form-cancel"}
CLI_TIMEOUTS = {"form-scan": 120, "form-render": 120}

_SESSION = s("会话 id（fill_form_scan 返回的，形如 20260713_222813_2b73；不确定就先 fill_form_list 查，别自己编）")


def tool_fill_form_define(args):
    return rt.run_cli("form-define", rt.json_arg(args, "fields"))


TOOLS = {
    "fill_form_scan": "form-scan",
    "fill_form_define_fields": tool_fill_form_define,
    "fill_form_next": "form-next",
    "fill_form_set_answer": "form-set",
    "fill_form_render": "form-render",
    "fill_form_list": "form-list",
    "fill_form_cancel": "form-cancel",
}

MEMBER_LOCKED = set(TOOLS)
DOC_TOOLS = {"fill_form_render"}
UNTRUSTED_TOOLS = {"fill_form_scan"}   # 来件 PDF 的 OCR/字段文本

SCHEMAS = [
    fn("fill_form_scan", "识别 PDF 表格的可填字段并创建填表会话（用户要求填表时用）。"
       "可填写 PDF 直接列出字段；平面/扫描 PDF 返回逐页 OCR 文本+坐标，"
       "需再调 fill_form_define_fields 提交你推断的字段。"
       "同一 PDF 已有进行中会话时直接续用该会话（不会重建）。", {
        "file": s("PDF 路径（用户发来的保存路径，data 内）"),
    }, ["file"]),
    fn("fill_form_define_fields", "平面表专用：把你从 OCR 布局推断出的待填字段提交给会话。"
       "anchor 是填写区域（标签右侧或下方的空白处），页面像素坐标。", {
        "session": _SESSION,
        "fields": s('JSON 数组: [{"name","label","type":"text|checkbox|choice",'
                    '"options":[],"page":0,"anchor":{"x","y","w","h"}}]'),
    }, ["session", "fields"]),
    fn("fill_form_next", "取会话中下一个未回答字段（问用户前调它）。", {
        "session": _SESSION,
    }, ["session"]),
    fn("fill_form_set_answer", "记录用户对某字段的回答。留空传空字符串。", {
        "session": _SESSION,
        "field": s("字段 name"),
        "value": s("用户给的值；checkbox 用 on/off；留空传 \"\""),
    }, ["session", "field", "value"]),
    fn("fill_form_render", "所有字段回答完后生成填好的 PDF 并自动发给用户。", {
        "session": _SESSION,
    }, ["session"]),
    fn("fill_form_list", "列出我的填表会话（恢复中断的填表用）。", {}),
    fn("fill_form_cancel", "取消一个填表会话。", {
        "session": _SESSION,
    }, ["session"]),
]

PROMPT_RULES = [
    '填 PDF 表格：用户发来表格并要求填写 → fill_form_scan（file 传保存路径）。平面/扫描表先按 OCR 布局推断字段（fill_form_define_fields）。之后进入逐字段问答：每次 fill_form_next 取一个字段，一条消息只问一个字段——即使你从成员注册表/备忘/文档里知道答案，也必须问，把已知值作为建议给出（"回复\'对\'或给出正确值"）。绝不擅自替用户填任何值，绝不编造。用户答一个记一个（fill_form_set_answer；用户说"跳过/留空"传空字符串）。全部答完（或用户说"剩下都留空"时把余下字段逐个置空）再 fill_form_render，PDF 会自动发给用户。中断的填表用 fill_form_list 恢复',
]

IMAGE_ROUTES = [
    "用户此前或随图说明想要**填写**这份表格（如\"帮我填这个表\"）："
    "不要归档，改调 fill_form_scan（file 传上面的保存路径）进入填表流程。拿不准是归档还是填表时问用户。",
]
