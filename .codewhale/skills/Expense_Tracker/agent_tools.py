"""Expense_Tracker 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。"""

from __future__ import annotations

import json
import re


import models
import tool_runtime as rt
from tool_runtime import fn, s, num, int_, boolean

ORDER = 10

_FALLBACK = {
    "add", "add-batch", "list", "summary", "monthly", "update", "delete",
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


def _table_columns(schema: str) -> str:
    """models.SCHEMA → "表(列, 列…)"（schema 单一事实来源，不手抄）。"""
    out = []
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, re.S):
        cols = [ln.split()[0] for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("--")]
        out.append(f"{name}({', '.join(cols)})")
    return "；".join(out)


def tool_query_ledger(args):
    return rt.ro_query(str(models.DB_PATH), args.get("sql") or "")


def tool_add_transactions(args):
    return rt.run_cli("add-batch", rt.json_arg(args, "rows"))


TOOLS = {
    "add_transaction": "add",
    "add_transactions": tool_add_transactions,
    "query_ledger": tool_query_ledger,
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
    "update_transaction": "update",
    "delete_transaction": "delete",
}

# 写工具：归属由代码注入（防 LLM 冒名记到别人头上）
MEMBER_LOCKED = {"add_transaction", "add_transactions", "add_deposit", "add_transfer", "add_tax"}

SCHEMAS = [
    fn("add_transaction", "记一笔账（支出/收入/投资/储蓄）", {
        "type": s("交易类型", enum=TX_TYPES),
        "amount": num("金额，正数"),
        "currency": s(f"币种，默认 {BASE_CUR}", enum=CURRENCIES),
        "date": s("日期 YYYY-MM-DD"),
        "category": s(f"分类，必须从合法分类中选: {CATS_DESC}"),
        "desc": s("描述，如 午餐"),
        "notes": s("备注"),
        "force": boolean("跳过重复检查强制写入（确认是另一笔独立交易、非重复时用）"),
    }, ["type", "amount", "date"]),
    fn("add_transactions", "一次记多笔账（账单/流水逐笔明细）。每行独立处理，返回逐行结果"
       "（已添加/疑似重复未写/错误），你据此决定哪些行带 force 重记或问用户", {
        "rows": {"type": "array", "description": "每项一笔，字段同 add_transaction",
                 "items": {"type": "object", "properties": {
                     "type": s("交易类型", enum=TX_TYPES),
                     "amount": num("金额，正数"),
                     "currency": s(f"币种，默认 {BASE_CUR}", enum=CURRENCIES),
                     "date": s("日期 YYYY-MM-DD"),
                     "category": s("分类，同 add_transaction"),
                     "desc": s("描述，带商家+时间以区分同日同额"),
                     "notes": s("备注"),
                     "force": boolean("确认是另一笔独立交易时跳过重复检查"),
                 }, "required": ["type", "amount", "date"]}},
    }, ["rows"]),
    fn("query_ledger", "对家庭账本跑一条只读 SQL（SQLite 方言，单条 SELECT/WITH，写操作会被拒）。"
       "任意统计、对比、趋势、跨表都可自己写；固定视图不够用时用它。最多返回 200 行。"
       "表: " + _table_columns(models.SCHEMA)
       + "。date 为 'YYYY-MM-DD' 文本，金额分币种不要混加", {
        "sql": s("SQL 语句"),
    }, ["sql"]),
    fn("list_transactions", "查询交易流水", {
        "type": s("交易类型", enum=TX_TYPES),
        "category": s("分类"),
        "currency": s("币种", enum=CURRENCIES),
        "start": s("开始日期 YYYY-MM-DD"),
        "end": s("结束日期 YYYY-MM-DD"),
        "limit": int_("最多返回条数"),
        "member": rt.member_param("按成员过滤，如只看某个家庭成员的账"),
    }),
    fn("get_summary", "按分类汇总金额（分币种）", {
        "type": s("交易类型，默认 expense", enum=TX_TYPES),
        "year": int_("年份"),
        "month": int_("月份 1-12"),
        "member": rt.member_param("按成员过滤，如只看某个家庭成员的账"),
        "by-member": boolean("按成员汇总（谁花了多少）"),
    }),
    fn("get_monthly", "按月汇总金额（分币种）", {
        "type": s("交易类型，默认 expense", enum=TX_TYPES),
        "year": int_("年份"),
        "member": rt.member_param("按成员过滤，如只看某个家庭成员的账"),
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
    fn("update_transaction", "修改一条交易：只传要改的字段，其余不动（记错金额/日期/分类/描述时用，别删了重记）", {
        "id": int_("交易 id"),
        "type": s("交易类型", enum=TX_TYPES),
        "amount": num("金额，正数"),
        "currency": s("币种", enum=CURRENCIES),
        "date": s("日期 YYYY-MM-DD"),
        "category": s("分类（须属于改后的交易类型）"),
        "desc": s("描述"),
        "notes": s("备注"),
    }, ["id"]),
    fn("delete_transaction", "删除一条交易", {
        "id": int_("交易 id"),
    }, ["id"]),
]

PROMPT_SECTIONS = [
    f"""## 记账（家庭共享账本）
- 交易类型: {"/".join(TX_TYPES)}；币种: {"/".join(CURRENCIES)}（默认基准 {BASE_CUR}）；分类见 add_transaction
- 开出去的发票/报价单/未付账单没有现金流，不是收支（要跟进收款可建待办）
- 账单/流水记的是每笔明细；总额、应还款额、最低还款额、已还款额是汇总数字，不记""",
]

IMAGE_ROUTES = [
    "消费票据、银行/信用卡/支付App流水或账单 → 记账（逐笔明细，desc 带商家+时间以免同日同额被误判重复）",
]
