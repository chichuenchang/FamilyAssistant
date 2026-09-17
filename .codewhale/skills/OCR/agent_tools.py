"""OCR 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

进程内直调 ocr.py（无 cli.py）。路径限 data_root 内，防任意本地文件外泄到云端 OCR。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import paths as _paths
import tool_runtime as rt
from tool_runtime import fn, s

ORDER = 15


def tool_ocr_image(args):
    resolved = rt.data_path_guard(args.get("path", ""))
    if resolved is None:
        return f"[错误] 只允许识别数据目录内的图片: {_paths.data_root().resolve()}"
    try:
        from ocr import ocr_extract, is_available
        if is_available():
            info = ocr_extract(str(resolved))
            return json.dumps(info, ensure_ascii=False) if info else "[未识别到文字]"
        return "[OCR 未配置]"
    except Exception as e:
        return f"[OCR 错误] {e}"


TOOLS = {"ocr_image": tool_ocr_image}

UNTRUSTED_TOOLS = set(TOOLS)

SCHEMAS = [
    fn("ocr_image", "OCR 识别票据/账单图片，逐笔提取交易明细（返回 transactions 数组，"
       "非账单总额）。拿到后逐笔调 add_transaction 记账", {
        "path": s("图片路径"),
    }, ["path"]),
]
