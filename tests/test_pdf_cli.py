# tests/test_pdf_cli.py — PDF_Editor CLI（进程内加载，排版 LLM 打桩）。
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("reportlab")
pytest.importorskip("pypdfium2")

from pypdf import PdfReader

import pdf_plan
from pdf_samples import build_acro_pdf, build_digital_pdf, build_png, page_texts

_CLI = (Path(__file__).resolve().parent.parent
        / ".codewhale" / "skills" / "PDF_Editor" / "cli.py")

TEXT_OP = {"op": "text", "page": 0, "x": 320, "y": 164, "text": "ZHANGSAN"}


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("DATA_ROOT", str(root))
    return root


@pytest.fixture
def cli(data_root):
    spec = importlib.util.spec_from_file_location("pdf_editor_cli", _CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def inbox(data_root):
    d = data_root / "jim" / "inbox" / "2026-09"
    d.mkdir(parents=True)
    return d


def _planner(monkeypatch, ops, notes=(), seen=None):
    def fake(layout_text, prior_ops, instruction, chat=None):
        if seen is not None:
            seen.append({"layout": layout_text, "prior": prior_ops, "instruction": instruction})
        return list(ops), list(notes)
    monkeypatch.setattr(pdf_plan, "compile_ops", fake)


def _run(cli, capsys, *argv):
    code = 0
    try:
        cli.main(list(argv))
    except SystemExit as e:
        code = e.code or 0
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _sid(out):
    return next(l for l in out.splitlines() if l.startswith("session=")).split("=", 1)[1]


def test_inspect_lists_what_the_pdf_asks_for(cli, capsys, inbox):
    pdf = build_acro_pdf(inbox / "form.pdf")
    code, out, _ = _run(cli, capsys, "pdf-inspect", "--file", str(pdf), "--member", "jim")
    assert code == 0 and out.startswith("session=")
    assert "类型: acroform" in out and '字段 name="name" | 标签: Full name | text' in out
    assert "x=" not in out


def test_edit_writes_pdf_inside_session_and_prints_sentinel(cli, capsys, inbox, data_root, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    _planner(monkeypatch, [TEXT_OP], notes=["缺生日"])
    code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                        "--instruction", "名字填 ZHANGSAN", "--member", "jim")
    assert code == 0
    lines = out.splitlines()
    sid = _sid(out)
    assert lines[0] == f"jim/pdf_edits/{sid}/form_edited.pdf" and lines[1] == f"session={sid}"
    assert "提示: 缺生日" in lines
    assert "ZHANGSAN" in page_texts(data_root / lines[0])[0]
    plan = pdf_plan.load("jim", sid)
    assert plan["history"] == ["名字填 ZHANGSAN"] and plan["ops"][0]["text"] == "ZHANGSAN"
    assert plan["out"] == lines[0]


def test_inspect_then_edit_share_one_session(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    _, out, _ = _run(cli, capsys, "pdf-inspect", "--file", str(pdf), "--member", "jim")
    _planner(monkeypatch, [TEXT_OP])
    _, out2, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                      "--instruction", "x", "--member", "jim")
    assert _sid(out) == _sid(out2)


def test_correction_by_session_sees_prior_ops_and_rerenders(cli, capsys, inbox, data_root, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    seen = []
    _planner(monkeypatch, [TEXT_OP], seen=seen)
    _, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                     "--instruction", "first", "--member", "jim")
    sid = _sid(out)
    _planner(monkeypatch, [{**TEXT_OP, "text": "LISI"}], seen=seen)
    code, out2, _ = _run(cli, capsys, "pdf-edit", "--session", sid,
                         "--instruction", "改成 LISI", "--member", "jim")
    assert code == 0 and _sid(out2) == sid
    assert seen[0]["prior"] == [] and seen[1]["prior"][0]["text"] == "ZHANGSAN"
    assert "[x=" in seen[1]["layout"]                         # 排版 LLM 拿到的是带坐标的版面
    text = page_texts(data_root / out2.splitlines()[0])[0]
    assert "LISI" in text and "ZHANGSAN" not in text
    assert pdf_plan.load("jim", sid)["history"] == ["first", "改成 LISI"]


def test_bad_session_id_falls_back_to_latest(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    _planner(monkeypatch, [TEXT_OP])
    _, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                     "--instruction", "a", "--member", "jim")
    code, out2, err = _run(cli, capsys, "pdf-edit", "--session", "form.pdf",
                           "--instruction", "b", "--member", "jim")
    assert code == 0 and _sid(out2) == _sid(out)
    assert out2.splitlines()[0].endswith("form_edited.pdf")   # 首行仍是路径
    assert "已自动接续" in err


def test_fresh_starts_a_new_session(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    seen = []
    _planner(monkeypatch, [TEXT_OP], seen=seen)
    _, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                     "--instruction", "a", "--member", "jim")
    _, out2, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf), "--fresh",
                      "--instruction", "b", "--member", "jim")
    assert _sid(out) != _sid(out2) and seen[1]["prior"] == []


def test_file_wins_over_session_of_another_pdf(cli, capsys, inbox, data_root, monkeypatch):
    old = build_digital_pdf(inbox / "old.pdf")
    new = build_digital_pdf(inbox / "new.pdf")
    _planner(monkeypatch, [TEXT_OP])
    _, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(old),
                     "--instruction", "a", "--member", "jim")
    code, out2, _ = _run(cli, capsys, "pdf-edit", "--file", str(new), "--session", _sid(out),
                         "--instruction", "b", "--member", "jim")
    assert code == 0 and _sid(out2) != _sid(out)
    assert out2.splitlines()[0].endswith("new_edited.pdf")


def test_fresh_with_session_drops_prior_ops(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    seen = []
    _planner(monkeypatch, [TEXT_OP], seen=seen)
    _, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                     "--instruction", "a", "--member", "jim")
    for extra in ([], ["--file", str(pdf)]):
        _, out2, _ = _run(cli, capsys, "pdf-edit", "--session", _sid(out), "--fresh", *extra,
                          "--instruction", "b", "--member", "jim")
        assert _sid(out2) != _sid(out) and seen[-1]["prior"] == []


def test_no_file_no_session_no_history_is_an_error(cli, capsys, data_root, monkeypatch):
    _planner(monkeypatch, [TEXT_OP])
    code, out, _ = _run(cli, capsys, "pdf-edit", "--instruction", "a", "--member", "jim")
    assert code == 1 and out.startswith("[错误]")


def test_path_gates(cli, capsys, inbox, data_root, tmp_path, monkeypatch):
    _planner(monkeypatch, [TEXT_OP])
    outside = build_digital_pdf(tmp_path / "outside.pdf")
    other = data_root / "amy" / "inbox"
    other.mkdir(parents=True)
    theirs = build_digital_pdf(other / "theirs.pdf")
    for bad in (outside, theirs, inbox / "missing.pdf"):
        code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(bad),
                            "--instruction", "a", "--member", "jim")
        assert code == 1 and "路径不允许或文件不存在" in out
    fam = data_root / "Family" / "documents"
    fam.mkdir(parents=True)
    shared = build_digital_pdf(fam / "shared.pdf")
    code, _, _ = _run(cli, capsys, "pdf-edit", "--file", str(shared),
                      "--instruction", "a", "--member", "jim")
    assert code == 0


def test_foreign_image_src_is_skipped_not_fatal(cli, capsys, inbox, data_root, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    other = data_root / "amy"
    other.mkdir(parents=True)
    sig = build_png(other / "sig.png")
    _planner(monkeypatch, [TEXT_OP, {"op": "image", "page": 0, "x": 1, "y": 1, "w": 50,
                                     "src": str(sig)}])
    code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                        "--instruction", "a", "--member", "jim")
    assert code == 0 and any("路径不允许" in l for l in out.splitlines() if l.startswith("警告:"))


def test_own_image_is_stamped(cli, capsys, inbox, data_root, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    build_png(inbox / "sig.png")
    _planner(monkeypatch, [{"op": "image", "page": 0, "x": 100, "y": 100, "w": 120,
                            "src": "jim/inbox/2026-09/sig.png"}])
    code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                        "--instruction", "贴签名", "--member", "jim")
    assert code == 0
    assert len(PdfReader(str(data_root / out.splitlines()[0])).pages[0].images) == 1


def test_no_executable_ops_reports_planner_notes(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    _planner(monkeypatch, [], notes=["指令没给名字的值"])
    code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                        "--instruction", "填名字", "--member", "jim")
    assert code == 1 and "没有可执行的编辑" in out and "指令没给名字的值" in out


def test_empty_ops_undo_prior_edits(cli, capsys, inbox, data_root, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    _planner(monkeypatch, [TEXT_OP])
    _, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                     "--instruction", "a", "--member", "jim")
    sid = _sid(out)
    _planner(monkeypatch, [])
    code, out2, _ = _run(cli, capsys, "pdf-edit", "--session", sid,
                         "--instruction", "把名字删掉", "--member", "jim")
    assert code == 0
    assert "ZHANGSAN" not in page_texts(data_root / out2.splitlines()[0])[0]
    assert pdf_plan.load("jim", sid)["ops"] == []


def test_all_ops_invalid_keeps_prior_edits(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    _planner(monkeypatch, [TEXT_OP])
    _, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                     "--instruction", "a", "--member", "jim")
    sid = _sid(out)
    _planner(monkeypatch, [{"op": "bogus"}])
    code, out2, _ = _run(cli, capsys, "pdf-edit", "--session", sid,
                         "--instruction", "b", "--member", "jim")
    assert code == 1 and "没有可执行的编辑" in out2
    assert pdf_plan.load("jim", sid)["ops"][0]["text"] == "ZHANGSAN"


def test_planner_failure_is_reported(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")

    def boom(*a, **k):
        raise pdf_plan.PlanError("排版模型没给出可用编辑计划")
    monkeypatch.setattr(pdf_plan, "compile_ops", boom)
    code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                        "--instruction", "a", "--member", "jim")
    assert code == 1 and out.strip() == "[错误] 排版模型没给出可用编辑计划"


def test_missing_reportlab_blocks_overlay_but_not_fields(cli, capsys, inbox, monkeypatch):
    monkeypatch.setattr(cli.pdf_apply, "has_reportlab", lambda: False)
    flat = build_digital_pdf(inbox / "flat.pdf")
    _planner(monkeypatch, [TEXT_OP])
    code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(flat),
                        "--instruction", "a", "--member", "jim")
    assert code == 1 and "pip install reportlab" in out
    acro = build_acro_pdf(inbox / "acro.pdf")
    _planner(monkeypatch, [{"op": "field", "name": "name", "value": "ZHANG"}])
    code, out, _ = _run(cli, capsys, "pdf-edit", "--file", str(acro),
                        "--instruction", "a", "--member", "jim")
    assert code == 0


def test_missing_pypdf_is_an_install_hint(cli, capsys, inbox, monkeypatch):
    pdf = build_digital_pdf(inbox / "form.pdf")
    monkeypatch.setattr(cli.pdf_layout, "has_pypdf", lambda: False)
    code, out, _ = _run(cli, capsys, "pdf-inspect", "--file", str(pdf), "--member", "jim")
    assert code == 1 and "pip install pypdf" in out


def test_list_sessions(cli, capsys, inbox, monkeypatch):
    code, out, _ = _run(cli, capsys, "pdf-list", "--member", "jim")
    assert code == 0 and "没有 PDF 编辑会话" in out
    pdf = build_digital_pdf(inbox / "form.pdf")
    _planner(monkeypatch, [TEXT_OP])
    _, edit_out, _ = _run(cli, capsys, "pdf-edit", "--file", str(pdf),
                          "--instruction", "名字填 ZHANGSAN", "--member", "jim")
    _, out, _ = _run(cli, capsys, "pdf-list", "--member", "jim")
    assert _sid(edit_out) in out and "form.pdf" in out and "名字填 ZHANGSAN" in out
