"""DeepSeek 调用 + 每用户 model/effort 覆盖（/model /effort）。

agent_core 只做编排；模型表、覆盖状态文件、HTTP 调用全在这里。
状态存 data/.state/.llm_overrides.json：{user: {"model": ..., "effort": ...}}，
只在启动与切换命令时读写——消息路径零文件 IO。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path


import paths as _paths

_log = logging.getLogger("familyassist.agent")

MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
MODEL_ALIASES = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}
EFFORTS = ("low", "medium", "high", "max")
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_EFFORT = "max"

_ENV = {"model": "DEEPSEEK_MODEL", "effort": "DEEPSEEK_REASONING_EFFORT"}
_LABEL = {"model": "模型", "effort": "推理档"}
_USAGE = {"model": "/model [flash|pro|reset]", "effort": "/effort [low|medium|high|max|reset]"}
_VALID = {"model": MODELS, "effort": EFFORTS}
_DEFAULT = {"model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT}


# ── 覆盖状态文件 ────────────────────────────────────────────

def overrides_path() -> Path:
    return _paths.state_file(".llm_overrides.json")


def load_overrides() -> dict:
    """读每用户 LLM 覆盖。文件缺失 → {}；损坏/值非法 → 跳过并告警（手工改过也不炸）。"""
    try:
        raw = json.loads(overrides_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        _log.warning("LLM 覆盖状态文件损坏，按无覆盖启动", exc_info=True)
        return {}
    out = {}
    for user, entry in (raw.items() if isinstance(raw, dict) else []):
        if not isinstance(entry, dict):
            continue
        clean = {k: entry[k] for k in ("model", "effort") if entry.get(k) in _VALID[k]}
        if clean:
            out[user] = clean
    return out


def save_overrides(overrides: dict) -> None:
    """原子写（mkstemp 唯一临时文件 + os.replace，同 members._save_members 套路）。"""
    p = overrides_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── 解析 / 命令 / 调用 ──────────────────────────────────────

def settings(overrides: dict, user: str) -> tuple[str, str]:
    """该用户生效的 (model, effort)：个人覆盖 > 环境变量 > 默认。"""
    ov = overrides.get(user) or {}
    return tuple(ov.get(k) or os.environ.get(_ENV[k]) or _DEFAULT[k]
                 for k in ("model", "effort"))


def status_note(overrides: dict, user: str) -> str:
    """注入 system 的当前 LLM 设置：被问"你用什么模型/推理档"时如实答。"""
    model, effort = settings(overrides, user)
    ov = overrides.get(user) or {}

    def _src(kind: str) -> str:
        if ov.get(kind):
            return "你的个人覆盖"
        return "环境变量" if os.environ.get(_ENV[kind]) else "默认"

    return (f"\n\n## 当前 LLM 设置\n本轮你以 {model} 运行，推理档 {effort}"
            f"（模型来源：{_src('model')}；推理档来源：{_src('effort')}）。"
            f"被问用什么模型/推理档时如实告知；用户想改，让他自己发 /model 或 /effort。")


def parse_command(text: str):
    """/model /effort 解析。非切换命令 → None；否则 (kind, arg | None 表示用法错误)。"""
    parts = text.lower().split()
    if not parts or parts[0] not in ("/model", "/effort"):
        return None
    kind = parts[0][1:]
    if len(parts) > 2:
        return kind, None
    arg = parts[1] if len(parts) > 1 else ""
    if kind == "model":
        arg = MODEL_ALIASES.get(arg, arg)
    if arg and arg != "reset" and arg not in _VALID[kind]:
        return kind, None
    return kind, arg


def apply_command(overrides: dict, user: str, text: str, persist) -> str | None:
    """执行 /model /effort（就地改 overrides，persist(user)->bool 写回）。非命令返回 None。"""
    parsed = parse_command(text)
    if parsed is None:
        return None
    kind, arg = parsed
    label, usage = _LABEL[kind], _USAGE[kind]
    if arg is None:
        return f"用法: {usage}"
    if not arg:  # 查询当前生效值与来源
        ov = (overrides.get(user) or {}).get(kind)
        env = os.environ.get(_ENV[kind])
        if ov:
            return f"当前{label}：{ov}（你的个人覆盖）。"
        if env:
            return f"当前{label}：{env}（环境变量）。"
        return f"当前{label}：{_DEFAULT[kind]}（默认）。"
    if arg == "reset":
        entry = overrides.get(user)
        if entry:
            entry.pop(kind, None)
            if not entry:
                overrides.pop(user)
        note = "" if persist(user) else "（状态文件写入失败，旧覆盖重启后可能恢复）"
        return f"✅ 已清除你的{label}覆盖，回到环境变量/默认。{note}"
    overrides.setdefault(user, {})[kind] = arg
    note = "" if persist(user) else "（状态文件写入失败，重启后可能失效）"
    return f"✅ 你的{label}已切换为 {arg}（仅影响你）{note}。"


def chat(messages, tools, model: str, effort: str) -> dict | None:
    """DeepSeek chat completions（native function calling）。
    返回 choices[0].message 整个 dict（可能含 tool_calls）；失败返回 None。
    message 附私有键 "_usage"（API usage 原样）、"_finish"（finish_reason）：
    再次发给 API 前调用方须 pop 掉。"""
    import urllib.request
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    payload = {
        "model": model,
        "messages": messages,
        # DeepSeek V4 是推理模型，reasoning 占用 completion 预算，
        # 预算过低（曾 1500）会被推理耗尽 → content 空、无 tool_calls。
        # 账单图片 OCR 后逐笔记账尤其费 token，预算和超时都给足；
        # 高档位推理更长，max_tokens 相应调高避免被截断成空 content。
        "reasoning_effort": effort,
        "temperature": 0.3, "max_tokens": 32000,
    }
    if tools:      # 纯文本调用（PDF_Editor 排版）不带 tools 键
        payload["tools"] = tools
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        choice = resp["choices"][0]
        _log.debug("LLM finish=%s tokens=%s tool_calls=%d",
                   choice.get("finish_reason"),
                   resp.get("usage", {}).get("completion_tokens"),
                   len(choice["message"].get("tool_calls") or []))
        if choice.get("finish_reason") == "length":
            _log.warning("LLM 输出被 max_tokens 截断（推理模型预算不足的信号）")
        return {**choice["message"], "_usage": resp.get("usage") or {},
                "_finish": choice.get("finish_reason")}
    except Exception as e:
        print(f"[agent] LLM 调用失败: {e}", file=sys.stderr)
        _log.exception("LLM 调用失败")
        return None
