"""Expense_Tracker 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import tool_runtime as rt
from tool_runtime import fn, s, num, int_, boolean

ORDER = 10

_FALLBACK = {
    "add", "list", "summary", "monthly", "delete",
    "deposit-add", "deposit-list", "tax-add", "tax-list",
    "fx-get", "fx-set",
    "transfer-add", "transfer-list",
}
COMMANDS = set(_FALLBACK)
# 记账族走 config.json wechat.allowed_commands 白名单（增删仅影响记账族；缺失/损坏用回退）
AGENT_COMMANDS = set((rt.CONFIG.get("wechat") or {}).get("allowed_commands") or _FALLBACK)

TX_TYPES = list(rt.CONFIG.get("categories", {}).keys()) or [
    "expense", "income", "investment", "savings"]
CURRENCIES = rt.CONFIG.get("supported_currencies") or ["USD", "CNY", "CAD"]
BASE_CUR = rt.CONFIG.get("base_currency") or "USD"
CATS_DESC = json.dumps(rt.CONFIG.get("categories", {}), ensure_ascii=False)

TOOLS = {
    "add_transaction": "add",
    "list_transactions": "list",
    "get_summary": "summary",
    "get_monthly": "monthly",
    "list_deposits": "deposit-list",
    "add_deposit": "deposit-add",
    "get_fx_rate": "fx-get",
    "set_fx_rate": "fx-set",
    "add_tax": "tax-add",
    "list_tax": "tax-list",
    "add_transfer": "transfer-add",
    "list_transfers": "transfer-list",
    "delete_transaction": "delete",
}

# 写工具：归属由代码注入（防 LLM 冒名记到别人头上）
MEMBER_LOCKED = {"add_transaction", "add_deposit", "add_transfer", "add_tax"}

SCHEMAS = [
    fn("add_transaction", "记一笔账（支出/收入/投资/储蓄）", {
        "type": s("交易类型", enum=TX_TYPES),
        "amount": num("金额，正数"),
        "currency": s(f"币种，默认 {BASE_CUR}", enum=CURRENCIES),
        "date": s("日期 YYYY-MM-DD"),
        "category": s(f"分类，必须从合法分类中选: {CATS_DESC}"),
        "desc": s("描述，如 午餐"),
        "notes": s("备注"),
        "force": boolean("跳过重复检查强制写入（仅在用户确认非重复后用）"),
    }, ["type", "amount", "date"]),
    fn("list_transactions", "查询交易流水", {
        "type": s("交易类型", enum=TX_TYPES),
        "category": s("分类"),
        "currency": s("币种", enum=CURRENCIES),
        "start": s("开始日期 YYYY-MM-DD"),
        "end": s("结束日期 YYYY-MM-DD"),
        "limit": int_("最多返回条数"),
        "member": s("按成员过滤，如只看某个家庭成员的账"),
    }),
    fn("get_summary", "按分类汇总金额（分币种）", {
        "type": s("交易类型，默认 expense", enum=TX_TYPES),
        "year": int_("年份"),
        "month": int_("月份 1-12"),
        "member": s("按成员过滤，如只看某个家庭成员的账"),
        "by-member": boolean("按成员汇总（谁花了多少）"),
    }),
    fn("get_monthly", "按月汇总金额（分币种）", {
        "type": s("交易类型，默认 expense", enum=TX_TYPES),
        "year": int_("年份"),
        "member": s("按成员过滤，如只看某个家庭成员的账"),
    }),
    fn("list_deposits", "查询定期存款", {
        "currency": s("币种", enum=CURRENCIES),
        "active": boolean("只看未到期的"),
    }),
    fn("add_deposit", "新增定期存款记录", {
        "amount": num("本金"),
        "currency": s("币种", enum=CURRENCIES),
        "bank": s("银行名"),
        "account": s("账号"),
        "term": int_("期限（月）"),
        "rate": num("年利率(%)"),
        "start-date": s("起存日 YYYY-MM-DD"),
        "maturity": s("到期日 YYYY-MM-DD"),
        "notes": s("备注"),
    }, ["amount", "start-date"]),
    fn("get_fx_rate", "查询汇率", {
        "from": s("源币种", enum=CURRENCIES),
        "to": s("目标币种", enum=CURRENCIES),
    }, ["from", "to"]),
    fn("set_fx_rate", "设置汇率", {
        "from": s("源币种", enum=CURRENCIES),
        "to": s("目标币种", enum=CURRENCIES),
        "rate": num("汇率：1 源币种 = rate 目标币种"),
    }, ["from", "to", "rate"]),
    fn("add_tax", "新增报税记录", {
        "year": int_("税务年度"),
        "country": s("国家", enum=["US", "CA"]),
        "data": s('报税数据，JSON 字符串，如 {"total_income": 100000, "tax_paid": 20000}'),
        "filing-date": s("申报日期 YYYY-MM-DD"),
        "notes": s("备注"),
    }, ["year", "country"]),
    fn("list_tax", "查询报税记录", {
        "year": int_("税务年度"),
        "country": s("国家", enum=["US", "CA"]),
    }),
    fn("add_transfer", "记录资金划转/换汇（溯源；目标为定期时自动建定期存款）", {
        "from-amount": num("源金额"),
        "from-currency": s("源币种", enum=CURRENCIES),
        "to-amount": num("目标金额"),
        "to-currency": s("目标币种", enum=CURRENCIES),
        "to-type": s("目标账户类型：活期/定期"),
        "from-desc": s("源账户描述，如 活期/工行"),
        "from-type": s("源账户类型：活期/定期"),
        "from-deposit-id": int_("源若为已记录定期存款，其 id"),
        "rate": num("换汇汇率；不填按 to/from 计算"),
        "exchange-date": s("换汇日期 YYYY-MM-DD"),
        "to-bank": s("目标银行"),
        "to-account": s("目标账号"),
        "transfer-date": s("到账/转账日期 YYYY-MM-DD"),
        "to-term": int_("目标定期期限（月）"),
        "to-rate": num("目标定期年利率(%)"),
        "to-maturity": s("目标定期到期日 YYYY-MM-DD"),
        "notes": s("备注"),
    }, ["from-amount", "from-currency", "to-amount", "to-currency", "to-type"]),
    fn("list_transfers", "查询划转记录/溯源资金来源", {
        "currency": s("匹配源或目标币种", enum=CURRENCIES),
        "to-bank": s("目标银行"),
        "type": s("匹配源或目标类型 活期/定期"),
        "start": s("开始日期 YYYY-MM-DD"),
        "end": s("结束日期 YYYY-MM-DD"),
        "to-deposit-id": int_("查某定期存款的资金来源"),
        "from-deposit-id": int_("查某定期存款的去向"),
        "trace": s("模糊匹配 描述/银行/账号/备注"),
        "limit": int_("最多返回条数"),
    }),
    fn("delete_transaction", "删除一条交易", {
        "id": int_("交易 id"),
    }, ["id"]),
]

PROMPT_SECTIONS = [
    f"""## 记账合法值（来自配置，必须从中选）
- 交易类型: {"/".join(TX_TYPES)}
- 币种: {"/".join(CURRENCIES)}（默认基准 {BASE_CUR}）
- 各类型分类: {CATS_DESC}""",
]

PROMPT_RULES = [
    '用户说"记账""花了""买了"→ 提取金额/分类/日期 → 调 add_transaction',
    '用户说"查账""这个月花了多少"→ list_transactions 或 get_summary（可给 year+month 看某一个月）；要全年逐月对比→get_monthly（只按年，无月参数）；记错要删→delete_transaction',
    '用户说"存了定期""买了理财"→ add_deposit；"我有哪些定期"→ list_deposits',
    '用户说"报税""今年报了多少税"→ add_tax / list_tax',
    '用户说"换汇""把X块换成美元""转到X银行存定期""转钱"→ add_transfer（尽量问全：源账户/金额/币种→目标金额/币种/银行/账号/类型/日期）',
    '用户问"这笔定期/活期哪来的""资金来源""查某笔存款来源"→ list_transfers（按 to-deposit-id 或 trace 关键词）',
    '用户说"汇率"→ get_fx_rate；"美元汇率改成X"→ set_fx_rate',
]
