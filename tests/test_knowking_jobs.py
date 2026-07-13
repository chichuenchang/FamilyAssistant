# tests/test_knowking_jobs.py — KnowKing 后台任务桥（提交→后台跑→轮询投递）。
#
# 纯逻辑（report 抽取、任务状态机、投递分发、child env 消毒、uv 命令拼装）全部
# 用注入的 runner / 假 send_fn 测，不碰 uv、不碰网络、不起真线程。
import json
import os
import sys
from pathlib import Path

import pytest

AR = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"
sys.path.insert(0, str(AR))

import knowking_jobs as kj


@pytest.fixture
def jdir(tmp_path):
    d = tmp_path / ".knowking_jobs"
    d.mkdir()
    return d


def _read_job(jdir, job_id):
    return json.loads((jdir / f"{job_id}.json").read_text(encoding="utf-8"))


# ── extract_report ──────────────────────────────────────────

class TestExtractReport:
    def test_between_markers(self):
        out = kj.extract_report(
            "noise line\nrun: x\n\n=== REPORT ===\n\nThe real report.\n\n=== END REPORT ===\nrun: y")
        assert out == "The real report."

    def test_no_markers_falls_back_to_stripped_stdout(self):
        assert kj.extract_report("  just some text  ") == "just some text"

    def test_empty(self):
        assert kj.extract_report("") == ""

    def test_marker_but_empty_body(self):
        out = kj.extract_report("=== REPORT ===\n\n=== END REPORT ===")
        assert out == ""


# ── submit ──────────────────────────────────────────────────

class TestSubmit:
    def test_writes_running_job_and_returns_ack(self, jdir):
        job_id, ack = kj.submit("为什么大家喜欢 ThinkPad", "telegram", "555", "Jim Zheng",
                                background=False, jdir=jdir)
        assert job_id
        assert "ThinkPad" in ack
        j = _read_job(jdir, job_id)
        assert j["status"] == "running"
        assert j["channel"] == "telegram" and j["user"] == "555"
        assert j["member"] == "Jim Zheng" and j["topic"] == "为什么大家喜欢 ThinkPad"

    def test_busy_guard_blocks_second_running_job_same_user(self, jdir):
        kj.submit("t1", "telegram", "555", "Jim", background=False, jdir=jdir)
        job_id, ack = kj.submit("t2", "telegram", "555", "Jim", background=False, jdir=jdir)
        assert job_id is None
        assert "已经" in ack or "进行中" in ack or "稍" in ack

    def test_different_users_not_blocked(self, jdir):
        a, _ = kj.submit("t1", "telegram", "555", "Jim", background=False, jdir=jdir)
        b, _ = kj.submit("t2", "telegram", "777", "Wen", background=False, jdir=jdir)
        assert a and b and a != b

    def test_empty_topic_rejected(self, jdir):
        job_id, ack = kj.submit("   ", "telegram", "555", "Jim", background=False, jdir=jdir)
        assert job_id is None
        assert "主题" in ack or "内容" in ack

    def test_background_false_does_not_run(self, jdir):
        # background=False + no manual _run_job → job stays running (no runner invoked)
        job_id, _ = kj.submit("t", "telegram", "5", "Jim", background=False, jdir=jdir)
        assert _read_job(jdir, job_id)["status"] == "running"


# ── _run_job ────────────────────────────────────────────────

class TestRunJob:
    def test_success_sets_done_with_report(self, jdir):
        job_id, _ = kj.submit("t", "telegram", "5", "Jim", background=False, jdir=jdir)
        kj._run_job(job_id, runner=lambda topic: (True, f"report for {topic}"), jdir=jdir)
        j = _read_job(jdir, job_id)
        assert j["status"] == "done"
        assert j["report"] == "report for t"

    def test_failure_sets_error(self, jdir):
        job_id, _ = kj.submit("t", "telegram", "5", "Jim", background=False, jdir=jdir)
        kj._run_job(job_id, runner=lambda topic: (False, "boom"), jdir=jdir)
        j = _read_job(jdir, job_id)
        assert j["status"] == "error"
        assert "boom" in j["error"]

    def test_runner_raising_sets_error_not_propagates(self, jdir):
        job_id, _ = kj.submit("t", "telegram", "5", "Jim", background=False, jdir=jdir)

        def boom(topic):
            raise RuntimeError("kaboom")

        kj._run_job(job_id, runner=boom, jdir=jdir)  # must not raise
        j = _read_job(jdir, job_id)
        assert j["status"] == "error"
        assert "kaboom" in j["error"]

    def test_missing_job_file_is_noop(self, jdir):
        kj._run_job("nonexistent", runner=lambda t: (True, "x"), jdir=jdir)  # no raise


# ── poll_and_deliver ────────────────────────────────────────

def _seed_job(jdir, job_id, **over):
    base = {"id": job_id, "channel": "telegram", "user": "5", "member": "Jim",
            "topic": "t", "status": "done", "report": "R", "error": "",
            "created_at": 1000.0}
    base.update(over)
    (jdir / f"{job_id}.json").write_text(json.dumps(base, ensure_ascii=False),
                                         encoding="utf-8")


class TestPollAndDeliver:
    def test_delivers_done_job_and_removes_file(self, jdir):
        _seed_job(jdir, "j1", status="done", report="hello report", topic="TP")
        sent = []
        n = kj.poll_and_deliver(lambda u, t: sent.append((u, t)), "telegram", jdir=jdir)
        assert n == 1
        assert sent[0][0] == "5"
        assert "hello report" in sent[0][1] and "TP" in sent[0][1]
        assert not (jdir / "j1.json").exists()  # cleaned after delivery

    def test_filters_by_channel(self, jdir):
        _seed_job(jdir, "j1", channel="wechat")
        sent = []
        n = kj.poll_and_deliver(lambda u, t: sent.append((u, t)), "telegram", jdir=jdir)
        assert n == 0 and sent == []
        assert (jdir / "j1.json").exists()  # untouched

    def test_skips_running_jobs(self, jdir):
        _seed_job(jdir, "j1", status="running", created_at=kj._now())
        sent = []
        n = kj.poll_and_deliver(lambda u, t: sent.append((u, t)), "telegram", jdir=jdir)
        assert n == 0 and sent == []
        assert (jdir / "j1.json").exists()

    def test_error_job_delivers_warning(self, jdir):
        _seed_job(jdir, "j1", status="error", error="no platforms", report="", topic="X")
        sent = []
        kj.poll_and_deliver(lambda u, t: sent.append((u, t)), "telegram", jdir=jdir)
        assert "no platforms" in sent[0][1]
        assert "X" in sent[0][1]

    def test_send_failure_keeps_file_for_retry(self, jdir):
        _seed_job(jdir, "j1", status="done")

        def boom(u, t):
            raise IOError("network down")

        n = kj.poll_and_deliver(boom, "telegram", jdir=jdir)
        assert n == 0
        assert (jdir / "j1.json").exists()  # not deleted → retried next poll

    def test_stale_running_becomes_error_delivered(self, jdir):
        _seed_job(jdir, "j1", status="running", created_at=kj._now() - 100000)
        sent = []
        n = kj.poll_and_deliver(lambda u, t: sent.append((u, t)), "telegram",
                                jdir=jdir, stale_seconds=3600)
        assert n == 1
        assert "超时" in sent[0][1] or "中断" in sent[0][1]

    def test_stale_error_persisted_even_if_send_fails(self, jdir):
        # stale 判定先落盘：投递失败后文件必须已是 error（不会下轮重复 stale 计时）
        _seed_job(jdir, "j1", status="running", created_at=kj._now() - 100000)

        def boom(u, t):
            raise IOError("down")

        n = kj.poll_and_deliver(boom, "telegram", jdir=jdir, stale_seconds=3600)
        assert n == 0
        assert _read_job(jdir, "j1")["status"] == "error"

    def test_unlink_failure_marks_delivered_no_resend(self, jdir, monkeypatch):
        # 投递成功但删文件失败 → 标 delivered；下轮只清理、不重发
        _seed_job(jdir, "j1", status="done", report="R")
        sent = []

        def bad_unlink(self, missing_ok=False):
            raise OSError("locked")

        real_unlink = kj.Path.unlink
        monkeypatch.setattr(kj.Path, "unlink", bad_unlink)
        n = kj.poll_and_deliver(lambda u, t: sent.append(t), "telegram", jdir=jdir)
        assert n == 1 and len(sent) == 1
        assert _read_job(jdir, "j1")["status"] == "delivered"

        monkeypatch.setattr(kj.Path, "unlink", real_unlink)
        n2 = kj.poll_and_deliver(lambda u, t: sent.append(t), "telegram", jdir=jdir)
        assert n2 == 0 and len(sent) == 1          # 没有第二次推送
        assert not (jdir / "j1.json").exists()      # 文件被清理

    def test_concurrent_submits_only_one_wins(self, jdir):
        # 忙闸门加锁：并发提交同用户，只有一个拿到 job_id
        import threading
        results = []

        def go(i):
            results.append(kj.submit(f"t{i}", "telegram", "555", "Jim",
                                     background=False, jdir=jdir)[0])

        ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sum(1 for r in results if r) == 1

    def test_one_bad_send_does_not_block_others(self, jdir):
        _seed_job(jdir, "j1", user="5", status="done")
        _seed_job(jdir, "j2", user="6", status="done")
        sent = []

        def send(u, t):
            if u == "5":
                raise IOError("down")
            sent.append(u)

        kj.poll_and_deliver(send, "telegram", jdir=jdir)
        assert sent == ["6"]
        assert (jdir / "j1.json").exists() and not (jdir / "j2.json").exists()


# ── hermetic child env + uv command ─────────────────────────

class TestChildEnv:
    def test_strips_deepseek_kk_and_provider_keys(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "bot-key")
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("KK_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("RAPIDAPI_KEY", "bot-rapid")
        monkeypatch.setenv("JUSTONEAPI_TOKEN", "bot-just")
        monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
        env = kj._child_env()
        assert "DEEPSEEK_API_KEY" not in env
        assert "DEEPSEEK_MODEL" not in env
        assert "KK_LOG_LEVEL" not in env
        assert "RAPIDAPI_KEY" not in env
        assert "JUSTONEAPI_TOKEN" not in env
        assert "PATH" in env  # kept so uv resolves

    def test_default_runner_builds_uv_command_with_cwd(self, monkeypatch, tmp_path):
        captured = {}

        class R:
            returncode = 0
            stdout = "=== REPORT ===\n\nOK\n\n=== END REPORT ==="
            stderr = ""

        def fake_run(argv, **kw):
            captured["argv"] = argv
            captured["cwd"] = kw.get("cwd")
            captured["env"] = kw.get("env")
            return R()

        monkeypatch.setattr(kj, "project_dir", lambda: tmp_path)
        monkeypatch.setattr(kj, "_uv_bin", lambda: "uv")
        monkeypatch.setattr(kj.subprocess, "run", fake_run)
        ok, text = kj._default_runner("大家怎么看 X")
        assert ok is True and text == "OK"
        argv = captured["argv"]
        assert argv[0] == "uv" and "run" in argv and "ask" in argv
        assert argv[-1] == "大家怎么看 X"  # topic positional, last
        assert "kk" in argv
        assert str(tmp_path) == str(captured["cwd"])  # cwd = KnowKing project

    def test_default_runner_nonzero_returns_error(self, monkeypatch, tmp_path):
        class R:
            returncode = 1
            stdout = ""
            stderr = "No enabled platforms."

        monkeypatch.setattr(kj, "project_dir", lambda: tmp_path)
        monkeypatch.setattr(kj, "_uv_bin", lambda: "uv")
        monkeypatch.setattr(kj.subprocess, "run", lambda *a, **k: R())
        ok, text = kj._default_runner("t")
        assert ok is False
        assert "No enabled platforms" in text


# ── end-to-end (no network): submit(bg=False) → run → poll ──

def test_end_to_end_offline(jdir):
    job_id, ack = kj.submit("懂王测试", "telegram", "999", "Jim",
                            background=False, jdir=jdir)
    assert "懂王测试" in ack
    kj._run_job(job_id, runner=lambda topic: (True, f"关于「{topic}」的报告"), jdir=jdir)
    sent = []
    kj.poll_and_deliver(lambda u, t: sent.append((u, t)), "telegram", jdir=jdir)
    assert sent[0][0] == "999"
    assert "关于「懂王测试」的报告" in sent[0][1]
