"""
Family Assistant — Expense Tracker CLI

Agent 通过 CLI 子命令操作数据库，输出纯文本或 JSON。
用法: python .codewhale/skills/Expense_Tracker/cli.py <command> [args]
"""

import argparse
import os
import sys
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

from db import (
    init_db,
    add_transaction,
    get_transactions,
    delete_transaction,
    summarize_by_category,
    monthly_summary,
    add_deposit,
    get_deposits,
    add_tax_filing,
    get_tax_filings,
    set_exchange_rate,
    get_latest_rate,
    convert_to_base,
    get_categories,
    get_base_currency,
)

TRANSACTION_TYPES = ("expense", "income", "investment", "savings")


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
    result = summarize_by_category(
        type_=args.type or "expense",
        year=args.year,
        month=args.month,
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
    result = monthly_summary(type_=args.type or "expense", year=args.year)
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
        term_months=args.term or 0,
        rate=args.rate or 0.0,
        start_date=args.start_date,
        maturity_date=args.maturity or "",
        receipt_path=args.receipt or "",
        notes=args.notes or "",
    )
    print(f"已添加定期存款 #{did}: {args.amount} {args.currency} @ {args.bank or '未知银行'}")


def cmd_deposit_list(args):
    rows = get_deposits(currency=args.currency, active_only=args.active)
    if not rows:
        print("没有定期存款记录。")
        return
    for r in rows:
        status = "进行中" if not r["maturity_date"] or r["maturity_date"] >= str(__import__("datetime").date.today()) else "已到期"
        print(f"#{r['id']} [{status}] {r['amount']} {r['currency']} "
              f"| {r['bank']} | {r['start_date']} → {r['maturity_date'] or '未设'} "
              f"| {r['term_months']}个月 @ {r['rate']}%")


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


def main():
    parser = argparse.ArgumentParser(description="Family Assistant — Expense Tracker CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="初始化数据库")

    # add
    p = sub.add_parser("add", help="添加交易")
    p.add_argument("--type", required=True, choices=["expense", "income", "investment", "savings"])
    p.add_argument("--amount", type=float, required=True)
    p.add_argument("--currency", default=get_base_currency())
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--category")
    p.add_argument("--desc")
    p.add_argument("--receipt")
    p.add_argument("--notes")
    p.add_argument("--force", action="store_true", help="跳过重复检查，强制写入")

    # list
    p = sub.add_parser("list", help="查询交易")
    p.add_argument("--type")
    p.add_argument("--category")
    p.add_argument("--currency")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--limit", type=int)

    # delete
    p = sub.add_parser("delete", help="删除交易")
    p.add_argument("--id", type=int, required=True)

    # summary
    p = sub.add_parser("summary", help="按分类汇总")
    p.add_argument("--type", default="expense")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int)

    # monthly
    p = sub.add_parser("monthly", help="按月汇总")
    p.add_argument("--type", default="expense")
    p.add_argument("--year", type=int)

    # deposit add
    p = sub.add_parser("deposit-add", help="添加定期存款")
    p.add_argument("--amount", type=float, required=True)
    p.add_argument("--currency", default=get_base_currency())
    p.add_argument("--bank")
    p.add_argument("--term", type=int)
    p.add_argument("--rate", type=float)
    p.add_argument("--start-date", required=True)
    p.add_argument("--maturity")
    p.add_argument("--receipt")
    p.add_argument("--notes")
    p.add_argument("--force", action="store_true", help="跳过重复检查，强制写入")

    # deposit list
    p = sub.add_parser("deposit-list", help="查询定期存款")
    p.add_argument("--currency")
    p.add_argument("--active", action="store_true")

    # tax add
    p = sub.add_parser("tax-add", help="添加报税记录")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--country", required=True)
    p.add_argument("--data", help="JSON 字符串")
    p.add_argument("--filing-date")
    p.add_argument("--receipt")
    p.add_argument("--notes")
    p.add_argument("--force", action="store_true", help="跳过重复检查，强制写入")

    # tax list
    p = sub.add_parser("tax-list", help="查询报税记录")
    p.add_argument("--year", type=int)
    p.add_argument("--country")

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
        "tax-add": cmd_tax_add,
        "tax-list": cmd_tax_list,
        "fx-set": cmd_fx_set,
        "fx-get": cmd_fx_get,
        "categories": cmd_categories,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
