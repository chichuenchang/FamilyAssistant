#!/usr/bin/env python3
"""Any_Search CLI — 实时联网搜索/抓取（AnySearch JSON-RPC API）。

子命令:
  any-search    --query "..."     联网搜索（通用，或带 --domain/--sub-domain 做垂直搜索）
  any-extract   --url "..."       抓取并提取单个网页全文（markdown）
  any-subdomains --domains "a,b"  列出垂直领域可用子域及参数（垂直搜索前的发现步骤）

输出为抓取到的原文/结果，交由 Agent 的 LLM 总结。失败打印 [错误] … 并 exit 0，
让 Agent 自然地告诉用户"没查到"。无 ANYSEARCH_API_KEY 时走匿名访问（限额较低）。
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

# 把本 skill 目录加入 sys.path（同目录 anysearch）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anysearch


def main() -> int:
    p = argparse.ArgumentParser(
        prog="Any_Search", description="实时联网搜索/抓取（AnySearch）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("any-search", help="联网搜索（通用/垂直）")
    s.add_argument("--query", required=True, help="搜索关键词/问题")
    s.add_argument("--domain", choices=anysearch.AVAILABLE_DOMAINS,
                   help="垂直领域（可选；做结构化搜索时配 --sub_domain）")
    s.add_argument("--sub_domain",
                   help="子域路由键（如 finance.quote）；先用 any-subdomains 发现")
    s.add_argument("--sub_domain_params",
                   help="子域参数：JSON 或 key=value,key2=value2")
    s.add_argument("--max_results", type=int,
                   help="返回结果数 1-10（默认 10）")

    e = sub.add_parser("any-extract", help="抓取并提取单个网页全文")
    e.add_argument("--url", required=True, help="网页 URL")

    d = sub.add_parser("any-subdomains", help="列出垂直领域子域及参数")
    d.add_argument("--domains", required=True, help="单个或逗号分隔的多个领域")

    args = p.parse_args()
    call = anysearch.anysearch_call

    if args.cmd == "any-search":
        out = anysearch.search(
            args.query, call=call, domain=args.domain,
            sub_domain=args.sub_domain, sub_domain_params=args.sub_domain_params,
            max_results=args.max_results)
    elif args.cmd == "any-extract":
        out = anysearch.extract(args.url, call=call)
    elif args.cmd == "any-subdomains":
        out = anysearch.subdomains(args.domains, call=call)
    else:  # pragma: no cover — argparse(required=True) already guards this
        out = "[错误] 未知命令"

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
