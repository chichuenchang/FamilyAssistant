"""LLM 提供商适配：把统一的 OpenAI 风格消息翻译成各家 API，再把回复翻译回来。

契约（agent_core 只认这一种格式，历史也按它存）：
    messages: [{"role": system|user|assistant|tool, "content": str,
                "tool_calls": [{"id", "type": "function",
                                "function": {"name", "arguments": <JSON 字符串>}}],
                "tool_call_id": str}]
    tools:    OpenAI function schema 列表（[] / None = 纯文本调用）
    返回:     choices[0].message 形态的 dict，附私有键：
              "_usage"  {"prompt_tokens", "completion_tokens"}
              "_finish" stop | tool_calls | length | 其他原样
              "_blocks" （仅 anthropic）原始 content 块，同一轮回传时原样重放
              失败返回 None。
    以 "_" 开头的键是本进程私有，发请求前一律剥掉。
    开头的 system 消息可多条，静态在前、易变（时间戳/成员/状态）放最后一条：
    openai_compat 合并成一条发；anthropic 逐条成 block，倒数第二条打 cache_control
    （前缀缓存顺序 tools → system → messages，一个断点即覆盖 tools + 静态 system）。

新增提供商：写一个 chat(spec, messages, tools, effort, **opts) 函数，登记进 PROVIDERS。
spec 是 llm_client 模型表里的一条（provider / api_model / api_key_env / base_url /
base_url_env / effort）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request

_log = logging.getLogger("familyassist.agent")

DEFAULT_TEMPERATURE = 0.3
# 推理模型的 reasoning 占用 completion 预算，预算过低（曾 1500）会被推理耗尽 →
# content 空、无 tool_calls。账单 OCR 后逐笔记账尤其费 token，预算和超时都给足。
DEFAULT_MAX_TOKENS = 32000
DEFAULT_TIMEOUT = 120
# Claude 默认自适应 thinking，effort high/max 下工具密集轮（账单逐笔记账）整段响应
# 可达数分钟；非流式调用整包到齐才返回，120s 会在账单已产生后超时丢掉结果。
ANTHROPIC_TIMEOUT = 600


def _post(url: str, headers: dict, payload: dict, timeout: int) -> dict | None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        print(f"[agent] LLM 调用失败: {e} {detail}", file=sys.stderr)
        _log.error("LLM HTTP %s %s: %s", e.code, url, detail)
    except Exception as e:
        print(f"[agent] LLM 调用失败: {e}", file=sys.stderr)
        _log.exception("LLM 调用失败")
    return None


def _parse(fn, resp: dict | None) -> dict | None:
    """回复形态异常（代理 / Ollama 变体缺字段）→ 记日志返回 None，不让 KeyError 穿到传输层。"""
    if not resp:
        return None
    try:
        return fn(resp)
    except (KeyError, IndexError, TypeError, AttributeError):
        _log.error("LLM 回复形态异常: %s", str(resp)[:300])
        return None


def _public(msg: dict) -> dict:
    return {k: v for k, v in msg.items() if not k.startswith("_")}


def _split_system(messages) -> tuple[list[str], list[dict]]:
    """开头连续 system 消息的正文列表 + 其余消息。"""
    n = 0
    while n < len(messages) and messages[n].get("role") == "system":
        n += 1
    return [m.get("content") or "" for m in messages[:n]], list(messages[n:])


def _base_url(spec: dict) -> str:
    env = spec.get("base_url_env")
    return ((os.environ.get(env) if env else "") or spec["base_url"]).rstrip("/")


def _auth(spec: dict) -> str:
    return os.environ.get(spec["api_key_env"], "")


# ── OpenAI 兼容（DeepSeek / OpenAI / Moonshot / Qwen / Ollama …） ───────────

def openai_compat(spec: dict, messages, tools, effort: str, *,
                  temperature: float = DEFAULT_TEMPERATURE,
                  max_tokens: int = DEFAULT_MAX_TOKENS,
                  timeout: int = DEFAULT_TIMEOUT) -> dict | None:
    system, rest = _split_system(messages)
    msgs = [_public(m) for m in rest]
    if system:
        msgs.insert(0, {"role": "system", "content": "\n\n".join(s for s in system if s)})
    payload = {
        "model": spec["api_model"], "messages": msgs,
        "temperature": temperature, "max_tokens": max_tokens,
    }
    if spec.get("effort", True):
        payload["reasoning_effort"] = effort
    if tools:      # 纯文本调用（PDF_Editor 排版）不带 tools 键
        payload["tools"] = tools
    resp = _post(f"{_base_url(spec)}/v1/chat/completions",
                 {"Authorization": f"Bearer {_auth(spec)}"}, payload, timeout)
    return _parse(_parse_openai, resp)


def _parse_openai(resp: dict) -> dict:
    choice = resp["choices"][0]
    finish = choice.get("finish_reason")
    _log.debug("LLM finish=%s tokens=%s tool_calls=%d", finish,
               resp.get("usage", {}).get("completion_tokens"),
               len(choice["message"].get("tool_calls") or []))
    if finish == "length":
        _log.warning("LLM 输出被 max_tokens 截断（推理模型预算不足的信号）")
    return {**choice["message"], "_usage": resp.get("usage") or {}, "_finish": finish}


# ── Anthropic Messages API ──────────────────────────────────────────────

_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_FINISH = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


def _to_anthropic(messages) -> tuple[list[dict], list[dict]]:
    """OpenAI 消息 → (system blocks, anthropic messages)。连续 tool 结果合成一条 user。
    system ≥2 条时倒数第二条打 cache_control（见模块 docstring）。"""
    texts, rest = _split_system(messages)
    system = [{"type": "text", "text": t} for t in texts if t]
    if len(system) >= 2:
        system[-2]["cache_control"] = {"type": "ephemeral"}
    out = []
    for m in rest:
        role, content = m.get("role"), m.get("content") or ""
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                     "content": content}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant":
            blocks = m.get("_blocks")
            if not blocks:
                blocks = [{"type": "text", "text": content}] if content else []
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    blocks.append({"type": "tool_use", "id": tc.get("id", ""),
                                   "name": fn.get("name", ""), "input": args})
            if blocks:   # 空 assistant 消息 API 拒收，直接跳过
                out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": "user", "content": content})
    return system, out


def _to_anthropic_tool(t: dict) -> dict:
    fn = t.get("function") or t
    return {"name": fn["name"], "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}}


def anthropic(spec: dict, messages, tools, effort: str, *,
              temperature: float = DEFAULT_TEMPERATURE,   # 当前 Claude 拒收 temperature，忽略
              max_tokens: int = DEFAULT_MAX_TOKENS,
              timeout: int = ANTHROPIC_TIMEOUT) -> dict | None:
    system, msgs = _to_anthropic(messages)
    payload = {"model": spec["api_model"], "max_tokens": max_tokens, "messages": msgs}
    if system:
        payload["system"] = system
    if spec.get("effort", True):
        payload["output_config"] = {"effort": effort}
    if tools:
        payload["tools"] = [_to_anthropic_tool(t) for t in tools]
    resp = _post(f"{_base_url(spec)}/v1/messages",
                 {"x-api-key": _auth(spec), "anthropic-version": _ANTHROPIC_VERSION},
                 payload, timeout)
    return _parse(_parse_anthropic, resp)


def _parse_anthropic(resp: dict) -> dict:
    blocks = resp["content"]
    text = "".join(b["text"] for b in blocks if b.get("type") == "text")
    tool_calls = [{"id": b["id"], "type": "function",
                   "function": {"name": b["name"],
                                "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                  for b in blocks if b.get("type") == "tool_use"]
    stop = resp.get("stop_reason")
    finish = _ANTHROPIC_FINISH.get(stop, stop)
    usage = resp.get("usage") or {}
    _log.debug("LLM finish=%s tokens=%s tool_calls=%d", finish,
               usage.get("output_tokens"), len(tool_calls))
    if finish == "length":
        _log.warning("LLM 输出被 max_tokens 截断")
    elif finish == "refusal":
        _log.warning("LLM 拒答: %s", resp.get("stop_details"))
    msg = {"role": "assistant", "content": text, "_blocks": blocks, "_finish": finish,
           "_usage": {"prompt_tokens": usage.get("input_tokens") or 0,
                      "completion_tokens": usage.get("output_tokens") or 0}}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


PROVIDERS = {"openai_compat": openai_compat, "anthropic": anthropic}
