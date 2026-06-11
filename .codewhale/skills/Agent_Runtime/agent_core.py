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
import subprocess
import sys
from collections import defaultdict
from datetime import date
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
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "OCR"))  # OCR skill 的 ocr.py
# 注：CLI 经 subprocess 调用（见 _run_cli），无需加入 sys.path


# ── config.json（值的单一事实来源；不在代码里重复硬编码） ──────

def _load_config_dict() -> dict:
    """解析项目根 config.json；缺失/损坏返回 {}（用下方回退）。"""
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_CONFIG = _load_config_dict()

# 票据目录（config.json receipts_dir，缺失回退 receipts）
RECEIPTS_DIR = ROOT / (_CONFIG.get("receipts_dir") or "receipts")


def receipt_month_dir(dt: date | None = None) -> Path:
    """票据按月分子目录：receipts/YYYY-MM/，不存在则创建。"""
    d = RECEIPTS_DIR / (dt or date.today()).strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    return d

# CLI 命令白名单（config.json wechat.allowed_commands，缺失回退内置集）
_FALLBACK_ALLOWED = {
    "add", "list", "summary", "monthly", "delete",
    "deposit-add", "deposit-list", "tax-add", "tax-list",
    "fx-get", "fx-set", "categories",
    "transfer-add", "transfer-list",
}
ALLOWED_COMMANDS = set(_CONFIG.get("wechat", {}).get("allowed_commands") or _FALLBACK_ALLOWED)


# ── system prompt ───────────────────────────────────────────

def _build_system_prompt() -> str:
    """组装 system prompt：身份 + config 提取的事实 + 行为准则。

    工具定义走 API tools 参数。FamilyAssistant.md 是开发文档（文件路径、
    CLI 示例），对运行时对话无用，不进 prompt——省每条消息的 token。
    分类/币种从 config.json 提取为紧凑列表，不嵌原始 JSON。
    """
    today = date.today()
    tx_types = "/".join(_TX_TYPES)
    currencies = "/".join(_CURRENCIES)

    return f"""你是 Family Assistant，一个运行在微信/Telegram 等远程频道里的个人/家庭 AI 助手。

## 你是谁
- 你可以帮用户记账、查账、汇总开销、管理定期存款、查询汇率、OCR 票据等
- 你友好、简洁、直接——回复不用太长

## 记账合法值（来自配置，必须从中选）
- 交易类型: {tx_types}
- 币种: {currencies}（默认基准 {_BASE_CUR}）
- 各类型分类: {_CATS_DESC}

## 行为准则
- 用户说"记账""花了""买了"→ 提取金额/分类/日期 → 调 add_transaction
- 用户说"查账""这个月花了多少"→ 调 list_transactions 或 get_summary
- 用户说"存了定期""买了理财"→ add_deposit；"我有哪些定期"→ list_deposits
- 用户说"报税""今年报了多少税"→ add_tax / list_tax
- 用户说"换汇""把X块换成美元""转到X银行存定期""转钱"→ add_transfer（尽量问全：源账户/金额/币种→目标金额/币种/银行/账号/类型/日期）
- 用户问"这笔定期/活期哪来的""资金来源""查某笔存款来源"→ list_transfers（按 to-deposit-id 或 trace 关键词）
- 用户说"汇率"→ get_fx_rate；"美元汇率改成X"→ set_fx_rate
- 用户闲聊/问候 → 直接友好回复，不用调工具
- 需要精确信息时（金额、日期）才调工具，闲聊不调
- 工具执行后会返回结果，你基于结果用自然语言回复
- 如果用户没有指定日期，默认今天 {today}
- 回复中不要暴露技术细节（如 SQLite、CLI 等）
"""


# ── CLI 执行器 ──────────────────────────────────────────────

def _run_cli(cmd: str, args: dict[str, Any] = None) -> str:
    """执行 CLI 命令并返回 stdout。"""
    if cmd not in ALLOWED_COMMANDS:
        return f"[错误] 命令不允许: {cmd}"

    cli_args = [cmd]
    if args:
        for k, v in args.items():
            flag = k if k.startswith("-") else f"--{k}"  # 容忍裸键（type→--type）
            if v is True:
                cli_args.append(flag)                     # 布尔开关，无值（如 --force）
            elif v is False or v is None or v == "":
                continue                                  # 未设置则跳过
            else:
                cli_args.append(flag)
                cli_args.append(str(v))

    cli_path = ROOT / ".codewhale" / "skills" / "Expense_Tracker" / "cli.py"
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
def _tool_add_deposit(args): return _run_cli("deposit-add", args)
def _tool_get_fx_rate(args): return _run_cli("fx-get", args)
def _tool_set_fx_rate(args): return _run_cli("fx-set", args)
def _tool_add_tax(args): return _run_cli("tax-add", args)
def _tool_list_tax(args): return _run_cli("tax-list", args)
def _tool_add_transfer(args): return _run_cli("transfer-add", args)
def _tool_list_transfers(args): return _run_cli("transfer-list", args)
def _tool_delete_transaction(args): return _run_cli("delete", args)

def _tool_ocr_image(args):
    path = args.get("path", "")
    # 安全：path 来自 LLM（间接来自用户消息），只允许票据目录内的文件，
    # 防止把任意本地文件 base64 后发给腾讯云/DeepSeek（数据外泄）。
    try:
        p = Path(path)
        resolved = (p if p.is_absolute() else ROOT / p).resolve()
        if not resolved.is_relative_to(RECEIPTS_DIR.resolve()):
            return f"[错误] 只允许识别票据目录内的图片: {RECEIPTS_DIR}"
    except (OSError, ValueError):
        return "[错误] 无效的图片路径"
    try:
        from ocr import ocr_extract, is_available
        if is_available():
            info = ocr_extract(str(resolved))
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
    "add_deposit": _tool_add_deposit,
    "get_fx_rate": _tool_get_fx_rate,
    "set_fx_rate": _tool_set_fx_rate,
    "add_tax": _tool_add_tax,
    "list_tax": _tool_list_tax,
    "add_transfer": _tool_add_transfer,
    "list_transfers": _tool_list_transfers,
    "delete_transaction": _tool_delete_transaction,
    "ocr_image": _tool_ocr_image,
}


# ── 工具 JSON Schema（DeepSeek function calling，OpenAI 兼容格式） ──
# 参数名与 CLI 标志一致（含连字符），_run_cli 直接转 --flag。
# 枚举值来自 config.json（单一事实来源）。

_TX_TYPES = list(_CONFIG.get("categories", {}).keys()) or [
    "expense", "income", "investment", "savings"]
_CURRENCIES = _CONFIG.get("supported_currencies") or ["USD", "CNY", "CAD"]
_BASE_CUR = _CONFIG.get("base_currency") or "USD"
_CATS_DESC = json.dumps(_CONFIG.get("categories", {}), ensure_ascii=False)


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props,
                       "required": required or []},
    }}


def _s(desc: str, **kw) -> dict:
    return {"type": "string", "description": desc, **kw}


def _num(desc: str) -> dict:
    return {"type": "number", "description": desc}


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


TOOL_SCHEMAS = [
    _fn("add_transaction", "记一笔账（支出/收入/投资/储蓄）", {
        "type": _s("交易类型", enum=_TX_TYPES),
        "amount": _num("金额，正数"),
        "currency": _s(f"币种，默认 {_BASE_CUR}", enum=_CURRENCIES),
        "date": _s("日期 YYYY-MM-DD"),
        "category": _s(f"分类，必须从合法分类中选: {_CATS_DESC}"),
        "desc": _s("描述，如 午餐"),
        "notes": _s("备注"),
        "force": {"type": "boolean", "description": "跳过重复检查强制写入（仅在用户确认非重复后用）"},
    }, ["type", "amount", "date"]),
    _fn("list_transactions", "查询交易流水", {
        "type": _s("交易类型", enum=_TX_TYPES),
        "category": _s("分类"),
        "currency": _s("币种", enum=_CURRENCIES),
        "start": _s("开始日期 YYYY-MM-DD"),
        "end": _s("结束日期 YYYY-MM-DD"),
        "limit": _int("最多返回条数"),
    }),
    _fn("get_summary", "按分类汇总金额（分币种）", {
        "type": _s("交易类型，默认 expense", enum=_TX_TYPES),
        "year": _int("年份"),
        "month": _int("月份 1-12"),
    }),
    _fn("get_monthly", "按月汇总金额（分币种）", {
        "type": _s("交易类型，默认 expense", enum=_TX_TYPES),
        "year": _int("年份"),
    }),
    _fn("list_deposits", "查询定期存款", {
        "currency": _s("币种", enum=_CURRENCIES),
        "active": {"type": "boolean", "description": "只看未到期的"},
    }),
    _fn("add_deposit", "新增定期存款记录", {
        "amount": _num("本金"),
        "currency": _s("币种", enum=_CURRENCIES),
        "bank": _s("银行名"),
        "account": _s("账号"),
        "term": _int("期限（月）"),
        "rate": _num("年利率(%)"),
        "start-date": _s("起存日 YYYY-MM-DD"),
        "maturity": _s("到期日 YYYY-MM-DD"),
        "notes": _s("备注"),
    }, ["amount", "start-date"]),
    _fn("get_fx_rate", "查询汇率", {
        "from": _s("源币种", enum=_CURRENCIES),
        "to": _s("目标币种", enum=_CURRENCIES),
    }, ["from", "to"]),
    _fn("set_fx_rate", "设置汇率", {
        "from": _s("源币种", enum=_CURRENCIES),
        "to": _s("目标币种", enum=_CURRENCIES),
        "rate": _num("汇率：1 源币种 = rate 目标币种"),
    }, ["from", "to", "rate"]),
    _fn("add_tax", "新增报税记录", {
        "year": _int("税务年度"),
        "country": _s("国家", enum=["US", "CA"]),
        "data": _s('报税数据，JSON 字符串，如 {"total_income": 100000, "tax_paid": 20000}'),
        "filing-date": _s("申报日期 YYYY-MM-DD"),
        "notes": _s("备注"),
    }, ["year", "country"]),
    _fn("list_tax", "查询报税记录", {
        "year": _int("税务年度"),
        "country": _s("国家", enum=["US", "CA"]),
    }),
    _fn("add_transfer", "记录资金划转/换汇（溯源；目标为定期时自动建定期存款）", {
        "from-amount": _num("源金额"),
        "from-currency": _s("源币种", enum=_CURRENCIES),
        "to-amount": _num("目标金额"),
        "to-currency": _s("目标币种", enum=_CURRENCIES),
        "to-type": _s("目标账户类型：活期/定期"),
        "from-desc": _s("源账户描述，如 活期/工行"),
        "from-type": _s("源账户类型：活期/定期"),
        "from-deposit-id": _int("源若为已记录定期存款，其 id"),
        "rate": _num("换汇汇率；不填按 to/from 计算"),
        "exchange-date": _s("换汇日期 YYYY-MM-DD"),
        "to-bank": _s("目标银行"),
        "to-account": _s("目标账号"),
        "transfer-date": _s("到账/转账日期 YYYY-MM-DD"),
        "to-term": _int("目标定期期限（月）"),
        "to-rate": _num("目标定期年利率(%)"),
        "to-maturity": _s("目标定期到期日 YYYY-MM-DD"),
        "notes": _s("备注"),
    }, ["from-amount", "from-currency", "to-amount", "to-currency", "to-type"]),
    _fn("list_transfers", "查询划转记录/溯源资金来源", {
        "currency": _s("匹配源或目标币种", enum=_CURRENCIES),
        "to-bank": _s("目标银行"),
        "type": _s("匹配源或目标类型 活期/定期"),
        "start": _s("开始日期 YYYY-MM-DD"),
        "end": _s("结束日期 YYYY-MM-DD"),
        "to-deposit-id": _int("查某定期存款的资金来源"),
        "from-deposit-id": _int("查某定期存款的去向"),
        "trace": _s("模糊匹配 描述/银行/账号/备注"),
        "limit": _int("最多返回条数"),
    }),
    _fn("ocr_image", "OCR 识别票据图片并提取结构化信息", {
        "path": _s("图片路径"),
    }, ["path"]),
    _fn("delete_transaction", "删除一条交易", {
        "id": _int("交易 id"),
    }, ["id"]),
]


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

        # 频道无关命令：清除本用户对话上下文（不经 LLM，零 token）
        if text.lower() in ("/clear", "清除上下文", "清空上下文", "清空记忆"):
            self.history.pop(user, None)
            return "✅ 对话上下文已清除。"

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return "未配置 DEEPSEEK_API_KEY。"

        msgs = [{"role": "system", "content": self.system_prompt}]
        user_history = self.history[user]
        msgs.extend(user_history[-self.history_size * 2:])
        msgs.append({"role": "user", "content": text})

        reply = ""
        tool_log = ""  # 收集工具执行日志
        for _ in range(4):
            message = self._call_llm(msgs)
            if message is None:
                return "抱歉，暂时出错了。"

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                reply = (message.get("content") or "").strip()
                break

            msgs.append(message)
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                try:
                    targs = json.loads(tc["function"].get("arguments") or "{}")
                except (json.JSONDecodeError, KeyError):
                    targs = {}
                fn = _TOOL_MAP.get(name)
                result = fn(targs) if fn else f"[错误] 未知工具: {name}"
                # 只展示工具调用，代码输出交给 LLM 转述
                brief = ", ".join(f"{k}={v}" for k, v in targs.items())
                tool_log += f"  ⚙️ {name}({brief})\n"
                msgs.append({"role": "tool",
                             "tool_call_id": tc.get("id", ""),
                             "content": result})

        if not reply:
            reply = "（工具已执行，但生成回复失败）" if tool_log else "抱歉，暂时出错了。"
        final = f"{tool_log}\n{reply}".strip() if tool_log else reply
        self._save_history(user, text, reply)
        return final

    def handle_image(self, image_path: str, user: str = "default") -> str:
        from ocr import ocr_image, is_available
        if is_available():
            ocr_text = ocr_image(image_path)
            if ocr_text:
                prompt = (
                    f"用户发了一张票据图片，OCR结果:\n{ocr_text}\n"
                    f"提取金额/日期/类别帮用户记账。如果不完整，告知需要什么。"
                )
                return self.handle(prompt, user=user)
        return "📷 图片已收到。请用文字描述（如\"午餐45块\"），或配置腾讯云 OCR。"

    def _call_llm(self, messages) -> dict | None:
        """调 DeepSeek chat completions（native function calling）。

        返回 choices[0].message 整个 dict（可能含 tool_calls）；失败返回 None。
        """
        import urllib.request
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        body = json.dumps({
            "model": "deepseek-v4-flash",
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "temperature": 0.3, "max_tokens": 800,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            return resp["choices"][0]["message"]
        except Exception as e:
            print(f"[agent] LLM 调用失败: {e}", file=sys.stderr)
            return None

    def _save_history(self, user, user_msg, assistant_msg):
        h = self.history[user]
        h.append({"role": "user", "content": user_msg})
        h.append({"role": "assistant", "content": assistant_msg})
        if len(h) > self.history_size * 2:
            self.history[user] = h[-self.history_size * 2:]


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
