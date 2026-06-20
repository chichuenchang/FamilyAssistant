#!/usr/bin/env python3
"""Web_Reach CLI — 只读联网读取/搜索/YouTube 总结。

子命令:
  web-search --query "..."   联网搜索最新资讯（Jina s.jina.ai，无需 key）
  web-read   --url "..."     抓取并清洗单个网页正文（Jina r.jina.ai）
  yt-summary --url "..."     YouTube 取字幕转文字（无字幕回退标题+简介）

输出为抓取到的原文/转写，交由 Agent 的 LLM 总结。失败打印 [错误] … 并 exit 0，
让 Agent 自然地告诉用户"没查到"。
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

# 把本 skill 目录加入 sys.path（同目录 reach）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import reach


def main() -> int:
    p = argparse.ArgumentParser(
        prog="Web_Reach", description="联网读取/搜索/YouTube 总结")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("web-search", help="联网搜索最新资讯")
    s.add_argument("--query", required=True, help="搜索关键词/问题")

    r = sub.add_parser("web-read", help="抓取并阅读一个网页")
    r.add_argument("--url", required=True, help="网页 URL")

    y = sub.add_parser("yt-summary", help="YouTube 字幕转写")
    y.add_argument("--url", required=True, help="YouTube 视频 URL")

    args = p.parse_args()

    if args.cmd == "web-search":
        out = reach.web_search(args.query, fetch=reach.jina_fetch)
    elif args.cmd == "web-read":
        out = reach.web_read(args.url, fetch=reach.jina_fetch)
    elif args.cmd == "yt-summary":
        out = reach.summarize_youtube(
            args.url, get_subs=reach.ytdlp_subs, get_meta=reach.ytdlp_meta)
    else:  # pragma: no cover — argparse(required=True) already guards this
        out = "[错误] 未知命令"

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
