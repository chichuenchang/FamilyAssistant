"""
Family Assistant — PDF Editor CLI

一句指令改 PDF：填表单、写字、打勾、白底覆盖、贴签名、画线、删页/旋转/重排/合并。
一次编辑 = 一个会话（data/<成员>/pdf_edits/<id>/），续改带同一会话即可。

用法: python .codewhale/skills/PDF_Editor/cli.py <command> [args]

依赖（可选，缺席优雅降级）: pypdf（必需）/ reportlab（覆盖层）/
pypdfium2（版面文字坐标）/ 腾讯云 OCR（扫描页坐标）
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import paths as _paths
import pdf_apply
import pdf_layout
import pdf_plan
import tool_runtime as rt
from backup_hook import mark_dirty as _mark_backup_dirty  # 写入后通知备份（失败静默）


def _die(msg: str):
    print(f"[错误] {msg}")
    sys.exit(1)


def _gate(path: str, member: str) -> Path:
    """来源闸门：data_root 内、且属本成员目录或家庭共享目录（同 send_file）。"""
    rel = rt.resolve_sendable(path, member)
    if rel is None:
        _die("路径不允许或文件不存在")
    return _paths.resolve_rel(rel)


def _build_layout(src: Path) -> dict:
    if not pdf_layout.has_pypdf():
        _die("缺少 pypdf 依赖，无法读取 PDF。pip install pypdf")
    try:
        return pdf_layout.build(src)
    except ValueError as e:
        _die(str(e))
    except Exception as e:
        _die(f"PDF 解析失败: {e}")


def _session_for_file(file: str, member: str, fresh: bool = False) -> dict:
    src = _gate(file, member)
    rel = _paths.to_rel(src)
    plan = None if fresh else pdf_plan.latest_for_source(member, rel)
    if plan is None:
        plan = pdf_plan.new_session(member, rel, _build_layout(src))
        _mark_backup_dirty()
    return plan


def _latest_or_die(member: str, why: str) -> dict:
    """LLM 跨消息容易忘掉/编造会话 id：兜底接续该成员最近的会话。
    提示走 stderr —— pdf-edit 的 stdout 首行必须是文件路径（哨兵契约）。"""
    sessions = pdf_plan.list_sessions(member)
    if not sessions:
        _die(f"{why}，且没有可续用的编辑会话（传 file 新建）")
    print(f"（{why}，已自动接续最近会话 {sessions[0]['id']}）", file=sys.stderr)
    return sessions[0]


def _resolve_plan(args) -> dict:
    if args.session:
        try:
            return pdf_plan.load(args.member, args.session)
        except (FileNotFoundError, ValueError) as e:
            if not args.file:
                return _latest_or_die(args.member, str(e))
    if args.file:
        return _session_for_file(args.file, args.member, args.fresh)
    return _latest_or_die(args.member, "没给 file 也没给 session")


def _layout_of(plan: dict, src: Path) -> dict:
    layout = pdf_plan.load_layout(plan)
    if layout is None:                       # 缓存丢了就重取
        layout = _build_layout(src)
        pdf_plan.save_layout(plan, layout)
    return layout


def cmd_inspect(args):
    plan = _session_for_file(args.file, args.member)
    layout = _layout_of(plan, _paths.resolve_rel(plan["source_pdf"]))
    print(f"session={plan['id']}")
    print(pdf_layout.describe(layout, coords=False))


def cmd_edit(args):
    plan = _resolve_plan(args)
    src = _paths.resolve_rel(plan["source_pdf"])
    if not src.exists():
        _die(f"原始 PDF 不存在: {plan['source_pdf']}")
    layout = _layout_of(plan, src)
    try:
        ops, notes = pdf_plan.compile_ops(pdf_layout.describe(layout), plan["ops"],
                                          args.instruction)
    except pdf_plan.PlanError as e:
        _die(str(e))
    ops, warnings = pdf_plan.validate_ops(
        ops, layout, lambda p: rt.resolve_sendable(p, args.member))
    if not ops:
        _die("没有可执行的编辑。" + " ".join(notes + warnings))
    if any(o["op"] in pdf_plan.OVERLAY_OPS for o in ops) and not pdf_apply.has_reportlab():
        _die("写字/打勾/覆盖/贴图需要 reportlab。pip install reportlab")
    out = pdf_plan.session_dir(args.member, plan["id"]) / f"{src.stem}_edited.pdf"
    try:
        warnings += pdf_apply.apply(str(src), ops, layout, str(out), _paths.resolve_rel)
    except ValueError as e:
        _die(str(e))
    except Exception as e:
        _die(f"PDF 编辑失败: {e}")
    plan["ops"] = ops
    plan["history"].append(args.instruction)
    plan["out"] = _paths.to_rel(out)
    pdf_plan.save(plan)
    _mark_backup_dirty()
    print(plan["out"])                           # 第一行 = 路径（哨兵契约）
    print(f"session={plan['id']}")
    for n in notes:
        print(f"提示: {n}")
    for w in warnings:
        print(f"警告: {w}")


def cmd_list(args):
    sessions = pdf_plan.list_sessions(args.member)
    if not sessions:
        print("没有 PDF 编辑会话。")
        return
    for s in sessions:
        last = s["history"][-1] if s["history"] else "（未编辑）"
        print(f"{s['id']} | {s['kind']} | {s['source_pdf']} | "
              f"{len(s['history'])} 次编辑 | 最后指令: {last[:60]} | {s['created']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pdf", description="Family Assistant PDF Editor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pdf-inspect", help="看 PDF 类型/页数/要填什么")
    p.add_argument("--file", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("pdf-edit", help="按一句指令编辑 PDF")
    p.add_argument("--file", default="")
    p.add_argument("--session", default="")
    p.add_argument("--instruction", required=True)
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("pdf-list", help="列出 PDF 编辑会话")
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_list)

    args = ap.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":               # Windows 控制台编码容错
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
