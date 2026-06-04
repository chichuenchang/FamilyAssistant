"""
Agent Core — 频道无关的全量上下文智能助手。

所有远程频道（微信、Telegram、未来其他）共用这一个 Agent，行为一致。
与 CodeWhale 工作方式一致：读取整个项目文档，理解意图，自主决策。
告别关键词路由，每条消息都带完整项目上下文调 DeepSeek API。

模式:
    启动时加载项目文档 → 构建 system prompt
    每条消息 → system + 对话历史 + 用户消息 → DeepSeek（function calling）
    LLM 自主选择工具 → 执行 → LLM 生成自然语言回复

频道接入契约（详见 .codewhale/skills/Agent_Runtime/SKILL.md）:
    agent.handle(text, user)        # 文字消息
    agent.handle_image(path, user)  # 图片消息
    user = 频道内唯一 id（隔离各用户对话历史）

依赖:
    DEEPSEEK_API_KEY

用法:
    from agent_core import Agent  # 同目录传输层直接 import
    agent = Agent()
    reply = agent.handle("这个月花了多少", user="wx_xxx")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 本文件位于 .codewhale/skills/Agent_Runtime/ ，向上 3 级到项目根
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))  # 让 scripts.ocr / scripts/cli.py 可解析


# ── 项目文档加载 ────────────────────────────────────────────

def _load_file(path: str) -> str:
    f = ROOT / path
    return f.read_text(encoding="utf-8") if f.exists() else ""


def _build_system_prompt() -> str:
    """组装 system prompt：项目文档 + 工具定义 + 行为准则。"""
    overview = _load_file("FamilyAssistant.md")
    config = _load_file("config.json")

    today = date.today()

    return f"""你是 Family Assistant，一个运行在微信/Telegram 等远程频道里的个人/家庭 AI 助手。
你有整个项目的全局视角，能自主决定如何响应用户。

## 你是谁
- 你可以帮用户记账、查账、汇总开销、管理定期存款、查询汇率、OCR 票据等
- 你友好、简洁、直接——回复不用太长

## 项目结构
{overview}

## 配置
{config}

## 可用工具
你可以调用以下 Python 函数来完成任务。每个回复中可以包含一个或多个工具调用。
工具调用格式：在回复中单独一行以 <TOOL> 开头，JSON 格式。

1. 记账
   <TOOL>{{"tool":"add_transaction","args":{{"type":"expense","amount":45.5,"currency":"CNY","date":"{today}","category":"餐饮","desc":"午餐"}}}}
   type: expense/income/investment/savings
   currency: CNY/USD/CAD

2. 查询
   <TOOL>{{"tool":"list_transactions","args":{{"type":"expense","start":"2026-05-01","end":"2026-06-01","limit":20}}}}

3. 汇总
   <TOOL>{{"tool":"get_summary","args":{{"type":"expense","year":2026,"month":6}}}}

4. 月度总览
   <TOOL>{{"tool":"get_monthly","args":{{"type":"expense","year":2026}}}}

5. 定期存款
   <TOOL>{{"tool":"list_deposits","args":{{}}}}

6. 汇率
   <TOOL>{{"tool":"get_fx_rate","args":{{"from":"USD","to":"CNY"}}}}

7. OCR 票据（用户发了图片时）
   <TOOL>{{"tool":"ocr_image","args":{{"path":"图片路径"}}}}

8. 删除
   <TOOL>{{"tool":"delete_transaction","args":{{"id":12}}}}

## 行为准则
- 用户说"记账""花了""买了"→ 提取金额/分类/日期 → 调 add_transaction
- 用户说"查账""这个月花了多少"→ 调 list_transactions 或 get_summary
- 用户说"汇率"→ 调 get_fx_rate
- 用户闲聊/问候 → 直接友好回复，不用调工具
- 需要精确信息时（金额、日期）才调工具，闲聊不调
- 工具执行后会返回结果，你基于结果用自然语言回复
- 如果用户没有指定日期，默认今天 {today}
- 回复中不要暴露技术细节（如 SQLite、CLI 等）
"""


# ── CLI 执行器 ──────────────────────────────────────────────

def _run_cli(cmd: str, args: dict[str, Any] = None) -> str:
    """执行 CLI 命令并返回 stdout。"""
    allowed = {
        "add", "list", "summary", "monthly", "delete",
        "deposit-add", "deposit-list", "tax-add", "tax-list",
        "fx-get", "fx-set",
    }
    if cmd not in allowed:
        return f"[错误] 命令不允许: {cmd}"

    cli_args = [cmd]
    if args:
        for k, v in args.items():
            cli_args.append(k)
            cli_args.append(str(v))

    cli_path = ROOT / "scripts" / "cli.py"
    try:
        result = subprocess.run(
            [sys.executable, str(cli_path)] + cli_args,
            capture_output=True, text=True, cwd=str(ROOT),
            timeout=30, encoding="utf-8", errors="replace",
        )
        return result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "[错误] 超时"
    except Exception as e:
        return f"[错误] {e}"


# ── 工具实现 ────────────────────────────────────────────────

def _tool_add_transaction(args): return _run_cli("add", args)
def _tool_list_transactions(args): return _run_cli("list", args)
def _tool_get_summary(args): return _run_cli("summary", args)
def _tool_get_monthly(args): return _run_cli("monthly", args)
def _tool_list_deposits(args): return _run_cli("deposit-list", args)
def _tool_get_fx_rate(args): return _run_cli("fx-get", args)
def _tool_delete_transaction(args): return _run_cli("delete", args)

def _tool_ocr_image(args):
    path = args.get("path", "")
    try:
        from scripts.ocr import ocr_extract, is_available
        if is_available():
            info = ocr_extract(path)
            return json.dumps(info, ensure_ascii=False) if info else "[未识别到文字]"
        return "[OCR 未配置]"
    except Exception as e:
        return f"[OCR 错误] {e}"


_TOOL_MAP = {
    "add_transaction": _tool_add_transaction,
    "list_transactions": _tool_list_transactions,
    "get_summary": _tool_get_summary,
    "get_monthly": _tool_get_monthly,
    "list_deposits": _tool_list_deposits,
    "get_fx_rate": _tool_get_fx_rate,
    "delete_transaction": _tool_delete_transaction,
    "ocr_image": _tool_ocr_image,
}


# ── Agent ───────────────────────────────────────────────────

class Agent:
    """频道无关的全量上下文智能助手。每条消息带完整项目文档 + 对话历史调 DeepSeek。"""

    def __init__(self, history_size: int = 20):
        self.system_prompt = _build_system_prompt()
        self.history_size = history_size
        self.history: dict[str, list[dict]] = defaultdict(list)

    def handle(self, text: str, user: str = "default") -> str:
        text = text.strip()
        if not text:
            return "收到空消息。"

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return "未配置 DEEPSEEK_API_KEY。"

        msgs = [{"role": "system", "content": self.system_prompt}]
        user_history = self.history[user]
        msgs.extend(user_history[-self.history_size * 2:])
        msgs.append({"role": "user", "content": text})

        llm_output = ""
        tool_log = ""  # 收集工具执行日志
        for _ in range(3):
            llm_output = self._call_llm(msgs)
            if not llm_output:
                return "抱歉，暂时出错了。"

            tools = self._parse_tools(llm_output)
            if not tools:
                clean = self._clean_response(llm_output)
                final = f"{tool_log}\n{clean}".strip() if tool_log else clean
                self._save_history(user, text, clean)
                return final

            results = []
            for name, targs in tools:
                r = _TOOL_MAP[name](targs)
                results.append(r)
                # 只展示工具调用，代码输出交给 LLM 转述
                brief = ", ".join(f"{k}={v}" for k, v in targs.items())
                tool_log += f"  ⚙️ {name}({brief})\n"

            msgs.append({"role": "assistant", "content": llm_output})
            msgs.append({"role": "user", "content":
                f"工具执行结果:\n" + "\n".join(results) +
                "\n请基于以上结果用自然语言回复用户。"})

        clean = self._clean_response(llm_output)
        final = f"{tool_log}\n{clean}".strip() if tool_log else clean
        self._save_history(user, text, clean)
        return final

    def handle_image(self, image_path: str, user: str = "default") -> str:
        from scripts.ocr import ocr_image, is_available
        if is_available():
            ocr_text = ocr_image(image_path)
            if ocr_text:
                prompt = (
                    f"用户发了一张票据图片，OCR结果:\n{ocr_text}\n"
                    f"提取金额/日期/类别帮用户记账。如果不完整，告知需要什么。"
                )
                return self.handle(prompt, user=user)
        return "📷 图片已收到。请用文字描述（如\"午餐45块\"），或配置腾讯云 OCR。"

    def _call_llm(self, messages):
        import urllib.request
        api_key = os.environ["DEEPSEEK_API_KEY"]
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        body = json.dumps({
            "model": "deepseek-v4-flash",
            "messages": messages,
            "temperature": 0.3, "max_tokens": 800,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[agent] LLM 调用失败: {e}", file=sys.stderr)
            return ""

    def _clean_response(self, text: str) -> str:
        """移除 <TOOL> 标签，返回纯净回复文本。"""
        return re.sub(r"<TOOL>\s*\{.*?\}\s*(?:</TOOL>)?", "", text, flags=re.DOTALL).strip()

    def _parse_tools(self, text):
        tools = []
        for m in re.finditer(r"<TOOL>\s*(\{.*?\})\s*(?:</TOOL>)?", text, re.DOTALL):
            try:
                t = json.loads(m.group(1))
                if t.get("tool") in _TOOL_MAP:
                    tools.append((t["tool"], t.get("args", {})))
            except json.JSONDecodeError:
                pass
        return tools

    def _save_history(self, user, user_msg, assistant_msg):
        h = self.history[user]
        h.append({"role": "user", "content": user_msg})
        h.append({"role": "assistant", "content": assistant_msg})
        if len(h) > self.history_size * 2:
            self.history[user] = h[-self.history_size * 2:]


# ── 兼容旧接口 ──────────────────────────────────────────────

def process_message(text: str) -> str:
    return Agent().handle(text)

def process_image(image_path: str) -> str:
    return Agent().handle_image(image_path)


# ── 测试入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    print("Family Assistant — Agent Core 测试模式")
    print("频道无关，全量上下文，跟 CodeWhale 一样的工作方式。")
    ok = bool(os.environ.get("DEEPSEEK_API_KEY"))
    print(f"LLM: {'已启用' if ok else '未配置 — 设置 DEEPSEEK_API_KEY'}")
    print("-" * 40)
    agent = Agent()
    while True:
        try:
            msg = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in ("quit", "exit", "q"):
            break
        print(agent.handle(msg))
        print()
