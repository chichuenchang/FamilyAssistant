# tests/test_form_cli.py — Form_Filler CLI（子进程，DATA_ROOT 隔离）。
import os
import subprocess
import sys as _sys
from pathlib import Path as _Path

import pytest

pytest.importorskip("pypdf")

from test_form_fill import build_acro_pdf

_CLI = str(_Path(__file__).resolve().parent.parent
           / ".codewhale" / "skills" / "Form_Filler" / "cli.py")


def _run(*args, data_root, extra_env=None):
    env = {**os.environ, **(extra_env or {}), "DATA_ROOT": str(data_root)}
    return subprocess.run([_sys.executable, _CLI, *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


@pytest.fixture
def data_root(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def inbox_pdf(data_root):
    """member inbox 里的最小 AcroForm PDF。"""
    d = data_root / "jim" / "inbox" / "2026-07"
    d.mkdir(parents=True)
    return build_acro_pdf(d / "form.pdf")


def _sid(scan_stdout):
    return scan_stdout.splitlines()[0].split("会话:")[1].strip()


def test_scan_next_set_render_roundtrip(data_root, inbox_pdf):
    r = _run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "acroform" in r.stdout
    assert "family_name" in r.stdout
    sid = _sid(r.stdout)

    r = _run("form-next", "--session", sid, "--member", "Jim", data_root=data_root)
    assert "family_name" in r.stdout

    r = _run("form-set", "--session", sid, "--field", "family_name",
             "--value", "Zheng", "--member", "Jim", data_root=data_root)
    assert "✅" in r.stdout and "1/2" in r.stdout

    r = _run("form-set", "--session", sid, "--field", "agree",
             "--value", "是", "--member", "Jim", data_root=data_root)
    assert "2/2" in r.stdout

    r = _run("form-next", "--session", sid, "--member", "Jim", data_root=data_root)
    assert "全部字段已回答" in r.stdout

    r = _run("form-render", "--session", sid, "--member", "Jim", data_root=data_root)
    assert r.returncode == 0, r.stdout + r.stderr
    rel = r.stdout.splitlines()[0].strip()
    assert rel == f"jim/forms/{sid}_filled.pdf"
    assert (data_root / rel).exists()
    from pypdf import PdfReader
    got = PdfReader(str(data_root / rel)).get_fields()
    assert got["family_name"]["/V"] == "Zheng"
    assert got["agree"]["/V"] == "/Yes"


def test_scan_rejects_foreign_member_path(data_root, inbox_pdf):
    r = _run("form-scan", "--file", str(inbox_pdf), "--member", "Wenliang",
             data_root=data_root)
    assert r.returncode == 1
    assert "[错误]" in r.stdout


def test_scan_rejects_outside_data_root(data_root, tmp_path):
    outside = tmp_path / "x.pdf"
    outside.write_bytes(b"%PDF")
    r = _run("form-scan", "--file", str(outside), "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 1
    assert "[错误]" in r.stdout


def test_set_invalid_choice_reports_error(data_root, inbox_pdf):
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    r = _run("form-set", "--session", sid, "--field", "agree",
             "--value", "maybe", "--member", "Jim", data_root=data_root)
    assert r.returncode == 1
    assert "[错误]" in r.stdout


def test_list_and_cancel(data_root, inbox_pdf):
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    r = _run("form-list", "--member", "Jim", data_root=data_root)
    assert sid in r.stdout and "collecting" in r.stdout
    r = _run("form-cancel", "--session", sid, "--member", "Jim",
             data_root=data_root)
    assert "已取消" in r.stdout
    r = _run("form-next", "--session", sid, "--member", "Jim", data_root=data_root)
    assert r.returncode == 1 and "[错误]" in r.stdout


def test_scan_same_pdf_reuses_active_session(data_root, inbox_pdf):
    # 填表回归：LLM 忘会话 id 后常重扫同一 PDF——不能建平行会话丢进度
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    _run("form-set", "--session", sid, "--field", "family_name",
         "--value", "Zheng", "--member", "Jim", data_root=data_root)
    r = _run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _sid(r.stdout) == sid          # 续用同一会话
    assert "续用" in r.stdout and "1/2" in r.stdout  # 进度没丢


def test_scan_after_cancel_creates_new_session(data_root, inbox_pdf):
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    _run("form-cancel", "--session", sid, "--member", "Jim", data_root=data_root)
    r = _run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 0
    assert _sid(r.stdout) != sid


def test_bad_session_id_auto_resumes_latest_active(data_root, inbox_pdf):
    # 填表回归：LLM 拿 PDF 文件名当会话 id 编了一个 → 自动接续最近进行中会话
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    r = _run("form-set", "--session", "20260713_222759_wechat",
             "--field", "family_name", "--value", "Zheng",
             "--member", "Jim", data_root=data_root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "自动接续" in r.stdout and sid in r.stdout
    assert "✅" in r.stdout and "1/2" in r.stdout
    r = _run("form-next", "--session", "bogus", "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 0
    assert "agree" in r.stdout  # family_name 已答，下一个是 agree


def test_bad_session_id_without_active_session_errors(data_root, inbox_pdf):
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    _run("form-cancel", "--session", sid, "--member", "Jim", data_root=data_root)
    r = _run("form-next", "--session", "bogus", "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 1
    assert "[错误]" in r.stdout and "没有进行中的填表会话" in r.stdout


def test_cancel_stays_strict_on_bad_session_id(data_root, inbox_pdf):
    # 取消绝不自动接续——取消错会话比报错更糟
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    r = _run("form-cancel", "--session", "bogus", "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 1 and "[错误]" in r.stdout
    r = _run("form-list", "--member", "Jim", data_root=data_root)
    assert sid in r.stdout and "collecting" in r.stdout  # 原会话安然无恙


def test_flat_scan_without_ocr_reports_hint(data_root):
    pytest.importorskip("PIL")
    pytest.importorskip("pypdfium2")
    from PIL import Image
    d = data_root / "jim" / "inbox" / "2026-07"
    d.mkdir(parents=True)
    p = d / "flat.pdf"
    Image.new("RGB", (612, 792), "white").save(p, "PDF")
    r = _run("form-scan", "--file", str(p), "--member", "Jim",
             data_root=data_root,
             extra_env={"TENCENT_SECRET_ID": "", "TENCENT_SECRET_KEY": ""})
    assert r.returncode == 1
    assert "OCR" in r.stdout
