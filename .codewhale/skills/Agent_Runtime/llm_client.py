"""模型无关的 LLM 入口 + 每用户 model/effort 覆盖（/model /effort）。

agent_core 只做编排；模型表、覆盖状态文件、提供商分发全在这里，
HTTP/格式翻译在 llm_providers。
模型表 = 内置 deepseek-flash + config.json "llm.models"（键名即 /model 用的名字）。
条目可带 "fallback": 另一模型键——主模型无响应/失败时同参重发一次（DeepSeek 曾整站宕机；
DeepSeek 条目 timeout 30 = 流式静默 30s 判死，见 llm_providers）。
状态存 data/.state/.llm_overrides.json：{user: {"model": ..., "effort": ...}}，
只在启动与切换命令时读写——消息路径零文件 IO。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

import llm_providers as _providers
import paths as _paths
import tool_runtime as _rt

_log = logging.getLogger("familyassist.agent")

EFFORTS = ("low", "medium", "high", "max")
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_EFFORT = "high"

_BUILTIN_MODELS = {
    DEFAULT_MODEL: {"provider": "openai_compat", "api_model": "deepseek-flash",
                    "api_key_env": "DEEPSEEK_API_KEY", "base_url_env": "DEEPSEEK_BASE_URL",
                    "base_url": "https://api.deepseek.com", "aliases": ["deepseek", "flash"]},
}
# 启动默认的环境变量（旧名保留兼容）
_ENV = {"model": ("LLM_MODEL", "DEEPSEEK_MODEL"),
        "effort": ("LLM_EFFORT", "DEEPSEEK_REASONING_EFFORT")}
_LABEL = {"model": "模型", "effort": "推理档"}
_DEFAULT = {"model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT}

MODELS: dict[str, dict] = {}
_NAMES: dict[str, str] = {}   # 小写键名/别名 → 模型表键（键名优先于别名）


# ── 模型表 ──────────────────────────────────────────────────

def load_models(cfg: dict | None = None) -> dict[str, dict]:
    """内置表 + config.json llm.models 合并（同名条目字段覆盖内置）。非法条目跳过并告警。"""
    global MODELS, _NAMES
    cfg = (_rt.CONFIG if cfg is None else cfg).get("llm") or {}
    models = {k: dict(v) for k, v in _BUILTIN_MODELS.items()}
    for name, spec in (cfg.get("models") or {}).items():
        if name.startswith("_"):
            continue
        merged = {**models.get(name, {}), **spec} if isinstance(spec, dict) else {}
        merged.setdefault("api_model", name)
        complete = (merged.get("provider") in _providers.PROVIDERS and merged.get("api_key_env")
                    and (merged.get("base_url") or merged.get("base_url_env")))
        if not complete:
            _log.warning("config.json llm.models[%r] 缺 provider/api_key_env/base_url，已忽略", name)
            continue
        models[name] = merged
    MODELS = models
    _NAMES = {a.lower(): n for n, s in models.items() for a in s.get("aliases") or []}
    _NAMES.update({n.lower(): n for n in models})
    return models


load_models()


def canon_model(name) -> str | None:
    """名字/别名（不分大小写）→ 模型表键；未登记返回 None。"""
    return _NAMES.get(name.lower()) if isinstance(name, str) else None


def spec(model: str) -> dict:
    """模型表条目；未登记的名字按 DeepSeek 原始模型 id 直发（LLM_MODEL=deepseek-v4-pro 之类）。"""
    return MODELS.get(model) or {**MODELS[DEFAULT_MODEL], "api_model": model}


def missing_key(model: str) -> str:
    """该模型缺的 API key 环境变量名；已配置返回 ""。"""
    env = spec(model)["api_key_env"]
    return "" if os.environ.get(env) else env


def ready_note() -> str:
    """启动横幅一行：当前默认模型是否可用。"""
    model = settings({}, "")[0]
    missing = missing_key(model)
    state = f"未配置 — 设置 {missing}" if missing else "已启用"
    return f"LLM: {model} {state}"


def _models_line() -> str:
    parts = []
    for name, s in MODELS.items():
        alias = "/".join(s.get("aliases") or [])
        missing = missing_key(name)
        parts.append(name + (f"（{alias}）" if alias else "")
                     + (f"，未配置 {missing}" if missing else ""))
    return "；".join(parts)


# ── 覆盖状态文件 ────────────────────────────────────────────

def overrides_path() -> Path:
    return _paths.state_file(".llm_overrides.json")


_CANON = {"model": canon_model,
          "effort": lambda a: a if a in EFFORTS else None}   # 可按用户覆盖的项


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
        clean = {k: v for k, canon in _CANON.items() if (v := canon(entry.get(k)))}
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

def _env(kind: str) -> str:
    """启动默认值：新旧环境变量名按 _ENV 顺序取第一个非空。"""
    for name in _ENV[kind]:
        if os.environ.get(name):
            return os.environ[name]
    return ""


def _resolve(overrides: dict, user: str, kind: str) -> tuple[str, str]:
    """该用户某项的 (生效值, 来源)：个人覆盖 > 环境变量 > 默认。环境变量值规范化后仍未登记则原样用。"""
    value = (overrides.get(user) or {}).get(kind)
    if value:
        return value, "你的个人覆盖"
    value = _env(kind)
    if value:
        return _CANON[kind](value) or value, "环境变量"
    return _DEFAULT[kind], "默认"


def settings(overrides: dict, user: str) -> tuple[str, str]:
    """该用户生效的 (model, effort)。"""
    return _resolve(overrides, user, "model")[0], _resolve(overrides, user, "effort")[0]


def status_note(overrides: dict, user: str) -> str:
    """注入 system 的当前 LLM 设置：被问"你用什么模型/推理档"时如实答。"""
    model, model_src = _resolve(overrides, user, "model")
    effort, effort_src = _resolve(overrides, user, "effort")
    return (f"\n\n## 当前 LLM 设置\n本轮你以 {model} 运行，推理档 {effort}"
            f"（模型来源：{model_src}；推理档来源：{effort_src}）。"
            f"被问用什么模型/推理档时如实告知；用户想改，让他自己发 /model 或 /effort。")


def _cmd_usage(kind: str) -> str:
    if kind == "model":
        return f"/model [{'|'.join(MODELS)}|reset]（不带参数查当前值；别名见 /model）"
    return "/effort [low|medium|high|max|reset]"


def parse_command(text: str):
    """/model /effort 解析。非切换命令 → None；否则 (kind, arg)：
    arg "" = 查询，"reset" = 清除，规范化后的值 = 切换，None = 用法错误。"""
    parts = text.split()
    head = parts[0].lower() if parts else ""
    if head not in ("/model", "/effort"):
        return None
    kind = head[1:]
    if len(parts) > 2:
        return kind, None
    arg = parts[1].lower() if len(parts) > 1 else ""
    if not arg or arg == "reset":
        return kind, arg
    return kind, _CANON[kind](arg)


def apply_command(overrides: dict, user: str, text: str, persist) -> str | None:
    """执行 /model /effort（就地改 overrides，persist(user)->bool 写回）。非命令返回 None。"""
    parsed = parse_command(text)
    if parsed is None:
        return None
    kind, arg = parsed
    label = _LABEL[kind]
    if arg is None:
        return f"用法: {_cmd_usage(kind)}"
    if not arg:  # 查询当前生效值与来源
        value, src = _resolve(overrides, user, kind)
        cur = f"当前{label}：{value}（{src}）。"
        return cur + (f"\n可切换：{_models_line()}" if kind == "model" else "")
    if arg == "reset":
        entry = overrides.get(user)
        if entry:
            entry.pop(kind, None)
            if not entry:
                overrides.pop(user)
        note = "" if persist(user) else "（状态文件写入失败，旧覆盖重启后可能恢复）"
        return f"✅ 已清除你的{label}覆盖，回到环境变量/默认。{note}"
    if kind == "model" and (missing := missing_key(arg)):
        return f"模型 {arg} 未配置 {missing}，无法切换。"
    overrides.setdefault(user, {})[kind] = arg
    note = "" if persist(user) else "（状态文件写入失败，重启后可能失效）"
    return f"✅ 你的{label}已切换为 {arg}（仅影响你）{note}。"


def fallback_of(model: str) -> str:
    """spec.fallback 指向的可用模型键；无、自指、未登记或缺 key 返回 ""。"""
    fb = spec(model).get("fallback") or ""
    return fb if fb in MODELS and fb != model and not missing_key(fb) else ""


def chat(messages, tools, model: str, effort: str, **opts) -> dict | None:
    """按模型表分发到提供商。返回 OpenAI 风格 message dict（可能含 tool_calls）；失败 None。
    附私有键 "_usage"/"_finish"（及 anthropic 的 "_blocks"，顶替时 "_fallback"=顶替模型键）：
    再次发给 API 前调用方须 pop 掉 _usage/_finish/_fallback；_blocks 留着同轮重放。
    opts 透传：temperature / max_tokens / timeout。主模型返回 None 且有 fallback 则同参重发一次。"""
    def call(name):
        s = spec(name)
        return _providers.PROVIDERS[s["provider"]](s, messages, tools, effort, **opts)

    out = call(model)
    if out is None and (fb := fallback_of(model)):
        _log.warning("模型 %s 无响应，改用 %s", model, fb)   # logger 已挂 stderr handler，不再 print
        out = call(fb)
        if out is not None:
            out["_fallback"] = fb
    return out
