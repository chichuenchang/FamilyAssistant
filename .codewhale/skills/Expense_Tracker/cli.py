"""
Family Assistant — Expense Tracker CLI

Agent 通过 CLI 子命令操作数据库，输出纯文本或 JSON。
用法: python .codewhale/skills/Expense_Tracker/cli.py <command> [args]
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 把本 skill 目录加入 sys.path（同目录 db / models）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 成员注册表（Agent_Runtime skill；跨 skill 经 sys.path，与 agent_core 引 OCR 同模式）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
import members as members_registry

from db import (
    init_db,
    add_transaction,
    get_transactions,
    delete_transaction,
    summarize_by_category,
    monthly_summary,
    summarize_by_member,
    add_deposit,
    get_deposits,
    add_transfer,
    get_transfers,
    add_tax_filing,
    get_tax_filings,
    set_exchange_rate,
    get_latest_rate,
    get_categories,
    get_base_currency,
    TRANSACTION_TYPES,
)


def _validate_member(name: str) -> str:
    """非空成员名必须已登记；返回原值或抛 ValueError。空值放行（家庭级）。"""
    if not name:
        return ""
    known = members_registry.member_names()
    if name not in known:
        raise ValueError(
            f"未知成员 '{name}'。已登记: {', '.join(known) or '（无）'}。用 member-add 添加。")
    return name


def cmd_init(_args):
    init_db()
    print("数据库初始化完成。")


def cmd_add(args):
    tid, dupes = add_transaction(
        type_=args.type,
        amount=args.amount,
        currency=args.currency,
        date_=args.date,
        category=args.category or "",
        description=args.desc or "",
        receipt_path=args.receipt or "",
        notes=args.notes or "",
        skip_dup_check=args.force,
        member=_validate_member(args.member or ""),
    )
    if dupes:
        print(f"⚠ 疑似重复！已存在 {len(dupes)} 笔同日同金额同币种的记录：")
        for d in dupes:
            print(f"  #{d['id']} [{d['date']}] {d['type']} {d['amount']} {d['currency']} "
                  f"| {d['category'] or '-'} | {d['description']}")
        if args.force:
            print(f"已强制写入 #{tid}。")
        else:
            print("未写入。如确认不是重复，请加 --force 强制写入。")
    else:
        print(f"已添加交易 #{tid}: {args.type} {args.amount} {args.currency} — {args.category or '未分类'}")


def cmd_list(args):
    rows = get_transactions(
        type_=args.type,
        category=args.category,
        currency=args.currency,
        start_date=args.start,
        end_date=args.end,
        limit=args.limit or 200,
        member=args.member,
    )
    if not rows:
        print("没有找到交易记录。")
        return
    for r in rows:
        print(f"#{r['id']} [{r['date']}] {r['type']} {r['amount']} {r['currency']} "
              f"| {r['category'] or '-'} | {r['description']}")


def cmd_delete(args):
    ok = delete_transaction(args.id)
    print(f"{'已删除' if ok else '未找到'} 交易 #{args.id}")


def cmd_categories(args):
    types = [args.type] if args.type else list(TRANSACTION_TYPES)
    for t in types:
        print(f"{t}: {', '.join(get_categories(t))}")


def cmd_summary(args):
    if args.by_member:
        result = summarize_by_member(type_=args.type or "expense",
                                     year=args.year, month=args.month)
        if not result:
            print("没有数据。")
            return
        for i, (cur, members) in enumerate(result.items()):
            if i:
                print()
            print(f"【{cur}】")
            for name, total in members.items():
                print(f"{name}: {total:.2f} {cur}")
        return
    result = summarize_by_category(
        type_=args.type or "expense",
        year=args.year,
        month=args.month,
        member=args.member,
    )
    if not result:
        print("没有数据。")
        return
    # 按币种分块，不跨币种相加
    for i, (cur, cats) in enumerate(result.items()):
        if i:
            print()
        total = sum(cats.values())
        print(f"【{cur}】")
        print(f"{'类别':<10} {'金额':>12}  {'占比':>6}")
        print("-" * 32)
        for cat, amt in cats.items():
            pct = (amt / total * 100) if total > 0 else 0
            print(f"{cat:<10} {amt:>12.2f}  {pct:>5.1f}%")
        print("-" * 32)
        print(f"{'合计':<10} {total:>12.2f} {cur}")


def cmd_monthly(args):
    result = monthly_summary(type_=args.type or "expense", year=args.year, member=args.member)
    if not result:
        print("没有数据。")
        return
    for i, (cur, months) in enumerate(result.items()):
        if i:
            print()
        print(f"【{cur}】")
        for mon, total in months.items():
            print(f"{mon}: {total:.2f} {cur}")


def cmd_deposit_add(args):
    did = add_deposit(
        amount=args.amount,
        currency=args.currency,
        bank=args.bank or "",
        account=args.account or "",
        term_months=args.term or 0,
        rate=args.rate or 0.0,
        start_date=args.start_date,
        maturity_date=args.maturity or "",
        receipt_path=args.receipt or "",
        notes=args.notes or "",
        member=_validate_member(args.member or ""),
    )
    acct = f" 账号 {args.account}" if args.account else ""
    print(f"已添加定期存款 #{did}: {args.amount} {args.currency} @ {args.bank or '未知银行'}{acct}")


def cmd_deposit_list(args):
    rows = get_deposits(currency=args.currency, active_only=args.active)
    if not rows:
        print("没有定期存款记录。")
        return
    for r in rows:
        status = "进行中" if not r["maturity_date"] or r["maturity_date"] >= str(date.today()) else "已到期"
        acct = f" 账号 {r['account']}" if r["account"] else ""
        print(f"#{r['id']} [{status}] {r['amount']} {r['currency']} "
              f"| {r['bank']}{acct} | {r['start_date']} → {r['maturity_date'] or '未设'} "
              f"| {r['term_months']}个月 @ {r['rate']}%")


def cmd_transfer_add(args):
    res = add_transfer(
        from_amount=args.from_amount,
        from_currency=args.from_currency,
        to_amount=args.to_amount,
        to_currency=args.to_currency,
        from_desc=args.from_desc or "",
        from_type=args.from_type or "",
        from_deposit_id=args.from_deposit_id,
        rate=args.rate,
        exchange_date=args.exchange_date or "",
        to_bank=args.to_bank or "",
        to_account=args.to_account or "",
        to_type=args.to_type,
        transfer_date=args.transfer_date or "",
        to_term=args.to_term or 0,
        to_rate=args.to_rate or 0.0,
        to_maturity=args.to_maturity or "",
        notes=args.notes or "",
        member=_validate_member(args.member or ""),
    )
    msg = (f"已记录划转 #{res['transfer_id']}: "
           f"{args.from_amount} {args.from_currency} → {args.to_amount} {args.to_currency} "
           f"@ {args.to_bank or '未知银行'}（{args.to_type}）")
    if res["to_deposit_id"]:
        msg += f"，已自动建定期存款 #{res['to_deposit_id']}"
    print(msg)


def cmd_transfer_list(args):
    rows = get_transfers(
        currency=args.currency,
        to_bank=args.to_bank,
        type_=args.type,
        start=args.start,
        end=args.end,
        to_deposit_id=args.to_deposit_id,
        from_deposit_id=args.from_deposit_id,
        trace=args.trace,
        limit=args.limit or 200,
    )
    if not rows:
        print("没有划转记录。")
        return
    for r in rows:
        src = r["from_desc"] or r["from_type"] or "?"
        if r["from_deposit_id"]:
            src += f"(定期#{r['from_deposit_id']})"
        dst = "/".join(x for x in (r["to_bank"], r["to_account"]) if x) or "?"
        link = f" 定期#{r['to_deposit_id']}" if r["to_deposit_id"] else ""
        note = f" | {r['notes']}" if r["notes"] else ""
        print(f"#{r['id']} [{r['transfer_date'] or r['exchange_date'] or '?'}] "
              f"{r['from_amount']} {r['from_currency']}（{src}）→ "
              f"{r['to_amount']} {r['to_currency']} @{r['rate']} → "
              f"{dst}（{r['to_type']}{link}）{note}")


def cmd_tax_add(args):
    import json
    data = json.loads(args.data) if args.data else {}
    tid = add_tax_filing(
        year=args.year,
        country=args.country,
        data=data,
        filing_date=args.filing_date or "",
        receipt_path=args.receipt or "",
        notes=args.notes or "",
        member=_validate_member(args.member or ""),
    )
    print(f"已添加报税记录 #{tid}: {args.year} {args.country}")


def cmd_tax_list(args):
    rows = get_tax_filings(year=args.year, country=args.country)
    if not rows:
        print("没有报税记录。")
        return
    for r in rows:
        data = r["data"]
        income = data.get("total_income", data.get("income", "N/A"))
        tax_paid = data.get("tax_paid", data.get("tax_owed", "N/A"))
        print(f"#{r['id']} [{r['year']}] {r['country']} | 收入: {income} | 缴税: {tax_paid} "
              f"| 申报日期: {r['filing_date'] or '未知'}")


def cmd_fx_set(args):
    set_exchange_rate(args.from_, args.to, args.rate)
    print(f"汇率已更新: 1 {args.from_} = {args.rate} {args.to}")


def cmd_fx_get(args):
    rate = get_latest_rate(args.from_, args.to)
    if rate:
        print(f"1 {args.from_} = {rate} {args.to}")
    else:
        print(f"未找到 {args.from_} → {args.to} 的汇率。")


def cmd_member_add(args):
    if not args.telegram and not args.wechat:
        raise ValueError("至少提供一个 --telegram 或 --wechat 频道 id")
    members_registry.add_member(args.name, telegram=args.telegram, wechat=args.wechat)
    print(f"已登记成员 {args.name}")
    cmd_member_list(args)


def cmd_member_list(_args):
    members = members_registry.load_members()
    if not members:
        print("没有已登记成员。用 member-add 添加。")
        return
    for name, b in members.items():
        tg = ",".join(b.get("telegram") or []) or "-"
        wx = ",".join(b.get("wechat") or []) or "-"
        print(f"{name}: telegram={tg} wechat={wx}")


def cmd_member_remove(args):
    ok = members_registry.remove_member(args.name)
    print(f"{'已删除成员' if ok else '未找到成员'} {args.name}")


def main():
    parser = argparse.ArgumentParser(description="Family Assistant — Expense Tracker CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="初始化数据库")

    # add
    p = sub.add_parser("add", help="添加交易")
    p.add_argument("--type", required=True, choices=list(TRANSACTION_TYPES))
    p.add_argument("--amount", type=float, required=True)
    p.add_argument("--currency", default=get_base_currency())
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--category")
    p.add_argument("--desc")
    p.add_argument("--receipt")
    p.add_argument("--notes")
    p.add_argument("--member", help="归属成员（须已登记）")
    p.add_argument("--force", action="store_true", help="跳过重复检查，强制写入")

    # list
    p = sub.add_parser("list", help="查询交易")
    p.add_argument("--type")
    p.add_argument("--category")
    p.add_argument("--currency")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--limit", type=int)
    p.add_argument("--member", help="按成员过滤")

    # delete
    p = sub.add_parser("delete", help="删除交易")
    p.add_argument("--id", type=int, required=True)

    # summary
    p = sub.add_parser("summary", help="按分类汇总")
    p.add_argument("--type", default="expense")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int)
    p.add_argument("--member", help="按成员过滤")
    p.add_argument("--by-member", action="store_true", help="按成员汇总")

    # monthly
    p = sub.add_parser("monthly", help="按月汇总")
    p.add_argument("--type", default="expense")
    p.add_argument("--year", type=int)
    p.add_argument("--member", help="按成员过滤")

    # deposit add
    p = sub.add_parser("deposit-add", help="添加定期存款")
    p.add_argument("--amount", type=float, required=True)
    p.add_argument("--currency", default=get_base_currency())
    p.add_argument("--bank")
    p.add_argument("--account", help="账号/账户号")
    p.add_argument("--term", type=int)
    p.add_argument("--rate", type=float)
    p.add_argument("--start-date", required=True)
    p.add_argument("--maturity")
    p.add_argument("--receipt")
    p.add_argument("--notes")
    p.add_argument("--member", help="归属成员（须已登记）")

    # deposit list
    p = sub.add_parser("deposit-list", help="查询定期存款")
    p.add_argument("--currency")
    p.add_argument("--active", action="store_true")

    # transfer add（资金划转/换汇溯源）
    p = sub.add_parser("transfer-add", help="记录资金划转/换汇（目标为定期时自动建定期存款）")
    p.add_argument("--from-amount", type=float, required=True)
    p.add_argument("--from-currency", required=True)
    p.add_argument("--to-amount", type=float, required=True)
    p.add_argument("--to-currency", required=True)
    p.add_argument("--to-type", required=True, help="目标账户类型：活期/定期")
    p.add_argument("--from-desc", help="源账户描述，如 活期/工行")
    p.add_argument("--from-type", help="源账户类型：活期/定期")
    p.add_argument("--from-deposit-id", type=int, help="源若为已记录定期存款，链接其 id")
    p.add_argument("--rate", type=float, help="换汇汇率；不填按 to/from 计算")
    p.add_argument("--exchange-date", help="换汇日期 YYYY-MM-DD")
    p.add_argument("--to-bank")
    p.add_argument("--to-account")
    p.add_argument("--transfer-date", help="到账/转账日期 YYYY-MM-DD")
    p.add_argument("--to-term", type=int, help="目标定期期限（月）")
    p.add_argument("--to-rate", type=float, help="目标定期年利率(%)")
    p.add_argument("--to-maturity", help="目标定期到期日 YYYY-MM-DD")
    p.add_argument("--notes")
    p.add_argument("--member", help="归属成员（须已登记）")

    # transfer list / 溯源
    p = sub.add_parser("transfer-list", help="查询划转记录（溯源资金来源）")
    p.add_argument("--currency", help="匹配源或目标币种")
    p.add_argument("--to-bank")
    p.add_argument("--type", help="匹配源或目标类型 活期/定期")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--to-deposit-id", type=int, help="查某定期存款的资金来源")
    p.add_argument("--from-deposit-id", type=int, help="查某定期存款的去向")
    p.add_argument("--trace", help="模糊匹配 描述/银行/账号/备注")
    p.add_argument("--limit", type=int)

    # tax add
    p = sub.add_parser("tax-add", help="添加报税记录")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--country", required=True)
    p.add_argument("--data", help="JSON 字符串")
    p.add_argument("--filing-date")
    p.add_argument("--receipt")
    p.add_argument("--notes")
    p.add_argument("--member", help="归属成员（须已登记）")

    # tax list
    p = sub.add_parser("tax-list", help="查询报税记录")
    p.add_argument("--year", type=int)
    p.add_argument("--country")

    # member 管理（仅本机使用；不在 wechat.allowed_commands 白名单内，Agent 调不到）
    p = sub.add_parser("member-add", help="登记成员并绑定频道 id（仅本机）")
    p.add_argument("name")
    p.add_argument("--telegram", action="append", help="Telegram chat id，可多次")
    p.add_argument("--wechat", action="append", help="微信用户 id，可多次")

    sub.add_parser("member-list", help="列出已登记成员")

    p = sub.add_parser("member-remove", help="删除成员（账目保留成员名）")
    p.add_argument("name")

    # fx set
    p = sub.add_parser("fx-set", help="设置汇率")
    p.add_argument("--from", dest="from_", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--rate", type=float, required=True)

    # fx get
    p = sub.add_parser("fx-get", help="查询汇率")
    p.add_argument("--from", dest="from_", required=True)
    p.add_argument("--to", required=True)

    # categories
    p = sub.add_parser("categories", help="列出合法分类（来自 config.json）")
    p.add_argument("--type", choices=list(TRANSACTION_TYPES), help="只看某交易类型")

    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "add": cmd_add,
        "list": cmd_list,
        "delete": cmd_delete,
        "summary": cmd_summary,
        "monthly": cmd_monthly,
        "deposit-add": cmd_deposit_add,
        "deposit-list": cmd_deposit_list,
        "transfer-add": cmd_transfer_add,
        "transfer-list": cmd_transfer_list,
        "tax-add": cmd_tax_add,
        "tax-list": cmd_tax_list,
        "fx-set": cmd_fx_set,
        "fx-get": cmd_fx_get,
        "categories": cmd_categories,
        "member-add": cmd_member_add,
        "member-list": cmd_member_list,
        "member-remove": cmd_member_remove,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
