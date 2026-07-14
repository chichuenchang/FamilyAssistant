"""
Family Assistant — Form Filler CLI（PDF 表格填写）

Agent 经白名单子命令调用，输出纯文本。一次填表 = 一个会话
（data/<成员>/forms/<id>.json），一字段一问跨消息不丢。

用法: python .codewhale/skills/Form_Filler/cli.py <command> [args]

依赖（可选，缺席优雅降级）:
    pypdf      — AcroForm 可填写 PDF（tier 1）
    pypdfium2  — 平面 PDF 逐页渲染（tier 2）
    Pillow     — 盖字 + 重组 PDF（tier 2）
    腾讯云 OCR — 平面表标签坐标（OCR skill）
"""

import argparse
import json
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OCR"))

import form_fill
import form_overlay
import form_session
import paths as _paths


def _die(msg: str):
    print(f"[错误] {msg}")
    sys.exit(1)


def _resolve_source(path: str, member: str) -> Path:
    """来源 PDF 闸门：须存在、在 data_root 内、且属本成员目录或家庭共享目录。
    与 agent_core._resolve_sendable 同一套规则（防 LLM 跨成员读文件）。"""
    try:
        p = Path(path)
        ap = (p if p.is_absolute() else _paths.resolve_rel(str(p))).resolve()
        root = _paths.data_root().resolve()
        if not (ap.exists() and ap.is_file() and ap.is_relative_to(root)):
            _die("路径不允许或文件不存在")
        allowed = [_paths.family_dir().resolve(), _paths.member_dir(member).resolve()]
        if not any(ap.is_relative_to(a) for a in allowed):
            _die("路径不允许或文件不存在")
        return ap
    except (ValueError, OSError):
        _die("路径不允许或文件不存在")


# 进行中会话状态（可自动接续的）
_ACTIVE_STATUSES = ("collecting", "defining")


def _load_session(args, strict: bool = False, note_to_stderr: bool = False) -> dict:
    """按 id 加载会话；id 无效/不存在且非 strict 时自动接续最近的进行中会话。

    LLM 跨消息容易忘掉真实会话 id（甚至拿 PDF 文件名当 id 编一个），
    strict=False 时兜底到该成员最近的进行中会话，避免填表流程原地打转。
    form-cancel 保持 strict——取消错会话比报错更糟。
    form-render 传 note_to_stderr=True：其 stdout 首行必须是文件路径（哨兵契约）。"""
    try:
        return form_session.load(args.member, args.session)
    except (FileNotFoundError, ValueError) as e:
        if strict:
            _die(str(e))
        active = [s for s in form_session.list_sessions(args.member)
                  if s.get("status") in _ACTIVE_STATUSES]
        if not active:
            _die(f"{e}，且没有进行中的填表会话（可用 fill_form_scan 新建）")
        s = active[0]
        print(f"（会话 id {args.session} 无效，已自动接续最近会话 {s['id']}）",
              file=sys.stderr if note_to_stderr else sys.stdout)
        return s


def _mark_backup_dirty() -> None:
    """写入后通知备份引擎（失败静默，绝不影响写入本身）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Remote_Backup"))
        from backup_sync import mark_dirty
        mark_dirty()
    except Exception:
        pass


def _fmt_field_line(i: int, f: dict) -> str:
    t = f["type"]
    if t == "choice":
        t = f"choice[{' / '.join(f['options'])}]"
    return f"{i}. {f['name']} | {f['label']} | {t}"


def cmd_form_scan(args):
    src = _resolve_source(args.file, args.member)
    rel_src = _paths.to_rel(src)

    # 同一 PDF 已有进行中（collecting）的会话 → 直接续用，
    # 避免 LLM 忘会话 id 后重复扫描建出一堆平行会话、进度全丢
    for s in form_session.list_sessions(args.member):
        if s.get("source_pdf") == rel_src and s.get("status") == "collecting":
            a, n = form_session.progress(s)
            print(f"会话: {s['id']}")
            print(f"类型: {s['kind']}（该 PDF 已有进行中的填表会话，续用，进度 {a}/{n}）")
            print(f"字段 ({len(s['fields'])}):")
            for i, f in enumerate(s["fields"], 1):
                print(_fmt_field_line(i, f))
            print("用 fill_form_next 继续提问。")
            return

    # 同一 PDF 半途的 flat 扫描（defining，还没定义字段）重扫即作废，不留僵尸会话
    for s in form_session.list_sessions(args.member):
        if s.get("source_pdf") == rel_src and s.get("status") == "defining":
            s["status"] = "cancelled"
            form_session.save(s)

    if not form_fill.is_available():
        _die("缺少 pypdf 依赖，无法读取 PDF 表单。pip install pypdf")
    try:
        fields = form_fill.scan_fields(str(src))
    except ValueError as e:
        _die(str(e))
    except Exception as e:
        _die(f"PDF 解析失败: {e}")

    if fields:
        s = form_session.new_session(args.member, rel_src, "acroform",
                                     fields=fields)
        _mark_backup_dirty()
        print(f"会话: {s['id']}")
        print("类型: acroform（可填写 PDF）")
        print(f"字段 ({len(fields)}):")
        for i, f in enumerate(s["fields"], 1):
            print(_fmt_field_line(i, f))
        print("用 fill_form_next 逐个提问。")
        return

    # ── 平面/扫描 PDF（tier 2）──
    if not form_overlay.is_available():
        _die("此 PDF 没有可填写字段（平面/扫描表格），需要 pypdfium2+Pillow。"
             "pip install pypdfium2 Pillow")
    import ocr as _ocr
    if not _ocr.is_available():
        _die("此 PDF 没有可填写字段（平面/扫描表格），需要腾讯云 OCR 定位标签。"
             "请配置 TENCENT_SECRET_ID/TENCENT_SECRET_KEY")
    s = form_session.new_session(args.member, rel_src, "flat", status="defining")
    pages_dir = _paths.member_forms_dir(args.member) / f"{s['id']}_pages"
    try:
        pages = form_overlay.render_pages(str(src), str(pages_dir))
    except Exception as e:
        _die(f"PDF 渲染失败: {e}")
    if not pages:
        _die("PDF 没有可渲染页面")
    lines = []
    for pg in pages:
        words = _ocr.ocr_image_words(pg["image"])
        if words is None:
            _die("OCR 识别失败（额度/网络），稍后再试")
        lines.append(f"--- 第 {pg['page'] + 1} 页 {pg['width']}x{pg['height']} ---")
        for w in words:
            lines.append(f"[x={w['x']},y={w['y']},w={w['w']},h={w['h']}] {w['text']}")
        pg["image"] = _paths.to_rel(pg["image"])   # 会话里存 data 相对路径
    s["pages"] = pages
    form_session.save(s)
    _mark_backup_dirty()
    print(f"会话: {s['id']}")
    print(f"类型: flat（平面/扫描 PDF，共 {len(pages)} 页）")
    print("下面是每页 OCR 文本及坐标（页面像素，页宽x高见页头）。")
    print("请根据布局推断需要填写的字段（标签 + 填写区域锚点 anchor，锚点是标签右侧")
    print("或下方的空白区），然后调 fill_form_define_fields 提交字段定义。")
    print("\n".join(lines))


def cmd_form_define(args):
    s = _load_session(args)
    try:
        fields = json.loads(args.fields)
        if not isinstance(fields, list) or not fields:
            _die("fields 必须是非空 JSON 数组")
        form_session.define_fields(s, fields)
    except json.JSONDecodeError as e:
        _die(f"fields 不是合法 JSON: {e}")
    except ValueError as e:
        _die(str(e))
    _mark_backup_dirty()
    print(f"已定义 {len(s['fields'])} 个字段:")
    for i, f in enumerate(s["fields"], 1):
        print(_fmt_field_line(i, f))
    print("用 fill_form_next 逐个提问。")


def cmd_form_next(args):
    s = _load_session(args)
    if s["status"] != "collecting":
        _die(f"会话状态是 {s['status']}")
    f = form_session.next_unanswered(s)
    if f is None:
        a, n = form_session.progress(s)
        print(f"全部字段已回答 ({a}/{n})。可调 fill_form_render 生成 PDF。")
        return
    extra = ""
    if f["type"] == "choice":
        extra = f"，选项: {' / '.join(f['options'])}"
    elif f["type"] == "checkbox":
        extra = "，回答 是/否"
    print(f"下一个字段: {f['name']} | {f['label']} | {f['type']}{extra}")


def cmd_form_set(args):
    s = _load_session(args)
    if s["status"] != "collecting":
        _die(f"会话状态是 {s['status']}")
    try:
        f = form_session.set_value(s, args.field, args.value)
    except ValueError as e:
        _die(str(e))
    _mark_backup_dirty()
    a, n = form_session.progress(s)
    shown = f["value"] if f["value"] != "" else "（留空）"
    print(f"✅ {f['name']} = {shown}（进度 {a}/{n}）")


def cmd_form_render(args):
    s = _load_session(args, note_to_stderr=True)
    if s["status"] == "cancelled":
        _die("会话已取消")
    values = {f["name"]: f["value"] for f in s["fields"]
              if f["value"] is not None}
    if not values:
        _die("还没有任何已回答字段")
    out = _paths.member_forms_dir(args.member) / f"{s['id']}_filled.pdf"
    warnings = []
    if s["kind"] == "acroform":
        if not form_fill.is_available():
            _die("缺少 pypdf 依赖。pip install pypdf")
        src = _paths.resolve_rel(s["source_pdf"])
        if not src.exists():
            _die(f"原始 PDF 不存在: {s['source_pdf']}")
        try:
            form_fill.render(str(src), values, str(out))
        except ValueError as e:
            _die(str(e))
    else:
        if not form_overlay.is_available():
            _die("缺少 pypdfium2/Pillow 依赖。pip install pypdfium2 Pillow")
        pages = [dict(p, image=str(_paths.resolve_rel(p["image"])))
                 for p in s["pages"]]
        missing = [p["image"] for p in pages if not Path(p["image"]).exists()]
        if missing:
            _die("页面图缺失，请重新 form-scan")
        warnings = form_overlay.stamp(pages, s["fields"], str(out))
    s["status"] = "done"
    s["render_path"] = _paths.to_rel(out)
    form_session.save(s)
    _mark_backup_dirty()
    print(s["render_path"])                      # 第一行 = 路径（哨兵契约）
    for w in warnings:
        print(f"警告: {w}")


def cmd_form_list(args):
    sessions = form_session.list_sessions(args.member)
    if not sessions:
        print("没有填表会话。")
        return
    for s in sessions:
        a, n = form_session.progress(s)
        print(f"{s['id']} | {s['status']} | {s['kind']} | 进度 {a}/{n} | {s['created']}")


def cmd_form_cancel(args):
    s = _load_session(args, strict=True)
    s["status"] = "cancelled"
    form_session.save(s)
    _mark_backup_dirty()
    print(f"已取消会话 {s['id']}。")


def main() -> int:
    ap = argparse.ArgumentParser(prog="form", description="Family Assistant Form Filler")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("form-scan", help="识别 PDF 表格字段并建会话")
    p.add_argument("--file", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_scan)

    p = sub.add_parser("form-define", help="平面表：提交 LLM 推断的字段定义")
    p.add_argument("--session", required=True)
    p.add_argument("--fields", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_define)

    p = sub.add_parser("form-next", help="下一个未回答字段")
    p.add_argument("--session", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_next)

    p = sub.add_parser("form-set", help="记录一个字段的答案")
    p.add_argument("--session", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_set)

    p = sub.add_parser("form-render", help="生成填好的 PDF")
    p.add_argument("--session", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_render)

    p = sub.add_parser("form-list", help="列出填表会话")
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_list)

    p = sub.add_parser("form-cancel", help="取消填表会话")
    p.add_argument("--session", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_cancel)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
