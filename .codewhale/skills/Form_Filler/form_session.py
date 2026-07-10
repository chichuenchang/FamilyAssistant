"""
Family Assistant — 填表会话存储（Form_Filler skill）

会话 = 一次"帮我填 PDF 表"的全过程状态，JSON 落盘 data/<成员>/forms/<id>.json。
一字段一问的 ping-pong 跨消息 /clear/重启不丢：每答必写盘，恢复时读盘。

会话/字段 schema 见 docs/superpowers/specs/2026-07-09-pdf-form-fill-design.md。
成员隔离靠目录：路径永远经 paths.member_forms_dir(member)，调用方
（agent_core）注入已解析成员名。
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
import paths as _paths

FIELD_TYPES = ("text", "checkbox", "choice")
KINDS = ("acroform", "flat")
STATUSES = ("defining", "collecting", "done", "cancelled")
_ID_RE = re.compile(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{4}")

# checkbox 用户口语 → 规范值
_CHECK_ON = {"on", "yes", "true", "1", "是", "勾", "勾选", "选", "√", "✓", "对"}
_CHECK_OFF = {"off", "no", "false", "0", "否", "不勾", "不选", "不", "×"}


def _session_path(member: str, session_id: str) -> Path:
    if not _ID_RE.fullmatch(session_id or ""):
        raise ValueError(f"非法会话 id: {session_id}")
    return _paths.member_forms_dir(member) / f"{session_id}.json"


def _norm_field(f: dict) -> dict:
    name = str(f.get("name") or "").strip()
    if not name:
        raise ValueError("字段缺少 name")
    ftype = f.get("type") or "text"
    if ftype not in FIELD_TYPES:
        raise ValueError(f"字段 {name} 类型必须是 {FIELD_TYPES}，收到: {ftype}")
    options = [str(o) for o in (f.get("options") or [])]
    if ftype == "choice" and not options:
        raise ValueError(f"choice 字段 {name} 缺少 options")
    anchor = f.get("anchor")
    if anchor is not None:
        try:
            anchor = {k: float(anchor[k]) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"字段 {name} 的 anchor 需要数值 x/y/w/h")
        if any(v < 0 for v in anchor.values()) or anchor["w"] <= 0 or anchor["h"] <= 0:
            raise ValueError(f"字段 {name} 的 anchor 数值非法")
    return {"name": name, "label": str(f.get("label") or name),
            "type": ftype, "options": options,
            "page": int(f.get("page") or 0), "anchor": anchor,
            "value": None, "asked": False}


def new_session(member: str, source_pdf: str, kind: str,
                fields: list | None = None, status: str = "collecting",
                pages: list | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {KINDS}，收到: {kind}")
    if status not in STATUSES:
        raise ValueError(f"status 必须是 {STATUSES}，收到: {status}")
    now = datetime.now()
    session = {
        "id": now.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4],
        "member": member,
        "source_pdf": source_pdf,
        "kind": kind,
        "status": status,
        "created": now.isoformat(timespec="seconds"),
        "pages": pages or [],
        "fields": [_norm_field(f) for f in (fields or [])],
        "render_path": None,
    }
    save(session)
    return session


def load(member: str, session_id: str) -> dict:
    p = _session_path(member, session_id)
    if not p.exists():
        raise FileNotFoundError(f"会话不存在: {session_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(session: dict) -> None:
    p = _session_path(session["member"], session["id"])
    p.write_text(json.dumps(session, ensure_ascii=False, indent=1),
                 encoding="utf-8")


def list_sessions(member: str) -> list:
    out = []
    for p in _paths.member_forms_dir(member).glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(out, key=lambda s: s.get("id", ""), reverse=True)


def define_fields(session: dict, fields: list) -> None:
    """平面表：LLM 从 OCR 布局推断出的字段定义写入会话，锚点须落在页面内。"""
    if session.get("status") != "defining":
        raise ValueError("仅 defining 状态的平面表会话可定义字段")
    dims = {int(p["page"]): (float(p["width"]), float(p["height"]))
            for p in session.get("pages", [])}
    normed = []
    for f in fields:
        nf = _norm_field(f)
        if nf["anchor"] is None:
            raise ValueError(f"平面表字段 {nf['name']} 必须带 anchor")
        if nf["page"] not in dims:
            raise ValueError(f"字段 {nf['name']} 的 page {nf['page']} 不存在")
        w, h = dims[nf["page"]]
        a = nf["anchor"]
        if a["x"] + a["w"] > w or a["y"] + a["h"] > h:
            raise ValueError(f"字段 {nf['name']} 的 anchor 超出页面范围")
        normed.append(nf)
    session["fields"] = normed
    session["status"] = "collecting"
    save(session)


def next_unanswered(session: dict) -> dict | None:
    for f in session.get("fields", []):
        if f.get("value") is None:
            return f
    return None


def set_value(session: dict, name: str, value: str) -> dict:
    for f in session.get("fields", []):
        if f["name"] == name:
            f["value"] = _validate_value(f, value)
            save(session)
            return f
    raise ValueError(f"字段不存在: {name}")


def _validate_value(field: dict, value: str) -> str:
    value = "" if value is None else str(value).strip()
    if value == "":                     # 显式留空
        return ""
    if field["type"] == "checkbox":
        low = value.lower()
        if low in _CHECK_ON:
            return "on"
        if low in _CHECK_OFF:
            return "off"
        raise ValueError(f"勾选框 {field['name']} 只接受 是/否（on/off），收到: {value}")
    if field["type"] == "choice" and value not in field["options"]:
        raise ValueError(
            f"{field['name']} 必须从选项中选: {' / '.join(field['options'])}，收到: {value}")
    return value


def progress(session: dict) -> tuple:
    fields = session.get("fields", [])
    return (sum(1 for f in fields if f.get("value") is not None), len(fields))
