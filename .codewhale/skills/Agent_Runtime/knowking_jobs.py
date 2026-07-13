"""
KnowKing 后台任务桥 — 把独立项目 KnowKing（懂王）的 `kk ask` 跨平台舆情搜集接入
频道无关 Agent，用"后台跑 + 完成后自动推送"模式（用户选定）。

为什么要桥而不是普通 CLI 工具：
  - KnowKing 是**独立 uv 项目**（D:\\PROJECTS\\KnowKing），自带 venv 和 .env
    （RAPIDAPI_KEY / JUSTONEAPI_TOKEN / DEEPSEEK_API_KEY），不在 .codewhale/skills 下，
    调用方式是 `uv run kk ask "<主题>"`（位置参数），不是本项目 CLI 的 --flag 风格。
  - 一次 `kk ask` 是一个完整 DeepSeek agent 会话，跨 8 平台搜集要**数分钟**。
    Agent.handle() 同步返回单条回复，若同步等待会把该会话卡死数分钟。
    故：提交即返回"已开始"，后台线程跑，出报告后由传输层轮询推送给发起人。

投递复用现有模式（与 Document_Keeper/reminder.check_and_push、backup_tick 同构）：
    传输层轮询里调 poll_and_deliver(send_fn, channel) → 把该频道已完成的任务发回发起人。

任务落盘 data/.knowking_jobs/<id>.json（点前缀 = 运行时瞬态，不进备份）：
    {id, channel, user, member, topic, status, report, error, created_at}
    status ∈ running / done / error。投递成功即删文件（避免重复推送与堆积）。

.env 权威性：子进程 cwd 设为 KnowKing 项目目录，让其 dotenv_values(".env") 读到
    KnowKing 自己的 .env；并把本机可能已导出的 DEEPSEEK*/KK_*/provider 键从子环境
    剔除（_child_env），保证 KnowKing 用它自己的密钥/模型，不被 bot 进程环境污染。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 paths
import paths as _paths

_log = logging.getLogger("familyassist.knowking")

# 默认 KnowKing 项目位置；可被 config.json knowking.project_dir 或环境变量 KNOWKING_DIR 覆盖
_DEFAULT_PROJECT_DIR = r"D:\PROJECTS\KnowKing"
_ROOT = Path(__file__).resolve().parents[3]

# 子进程超时：kk agent_timeout_s 默认 300s，外加搜集/展开，给足余量（可 config 覆盖）
_DEFAULT_TIMEOUT_S = 900
# running 任务超过此秒数仍未落地（bot 重启、线程死亡）→ 视为中断，推送失败通知
_DEFAULT_STALE_S = 1800

# 会污染 KnowKing 的本机环境变量：DEEPSEEK*/KK_* 前缀 + 两个 provider 密钥。
# 剔除后 KnowKing 的 .env 成为唯一事实来源（其 Config.load 精度：.env < 进程环境）。
_STRIP_ENV_PREFIXES = ("DEEPSEEK", "KK_")
_STRIP_ENV_EXACT = {"RAPIDAPI_KEY", "JUSTONEAPI_TOKEN"}


def _load_config() -> dict:
    try:
        cfg = json.loads((_ROOT / "config.json").read_text(encoding="utf-8"))
        return cfg.get("knowking") or {}
    except Exception:
        return {}


def project_dir() -> Path:
    """KnowKing 项目根：env KNOWKING_DIR > config knowking.project_dir > 默认。"""
    p = os.environ.get("KNOWKING_DIR") or _load_config().get("project_dir") or _DEFAULT_PROJECT_DIR
    return Path(p)


def _timeout_s() -> int:
    try:
        return int(_load_config().get("timeout_s") or _DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def jobs_dir(jdir: Optional[Path] = None) -> Path:
    if jdir is not None:
        return Path(jdir)
    d = _paths.data_root() / ".knowking_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> float:
    return time.time()


# ── 任务文件读写（原子写：写 .tmp 再 replace，避免轮询读到半截 JSON） ──

def _job_path(jdir: Path, job_id: str) -> Path:
    return jdir / f"{job_id}.json"


def _write_job(jdir: Path, job: dict) -> None:
    p = _job_path(jdir, job["id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _read_job(jdir: Path, job_id: str) -> Optional[dict]:
    try:
        return json.loads(_job_path(jdir, job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _iter_jobs(jdir: Path):
    for f in sorted(jdir.glob("*.json")):
        try:
            yield json.loads(f.read_text(encoding="utf-8")), f
        except (OSError, ValueError):
            continue


def _has_running_for_user(jdir: Path, channel: str, user: str) -> bool:
    for job, _f in _iter_jobs(jdir):
        if (job.get("status") == "running" and job.get("channel") == channel
                and str(job.get("user")) == str(user)):
            return True
    return False


# ── report 抽取 ─────────────────────────────────────────────

_REPORT_START = "=== REPORT ==="
_REPORT_END = "=== END REPORT ==="


def extract_report(stdout: str) -> str:
    """从 kk ask 的 stdout 抽出 === REPORT === 与 === END REPORT === 之间的正文；
    无标记则退化为整段 strip。"""
    s = stdout or ""
    i = s.find(_REPORT_START)
    if i == -1:
        return s.strip()
    j = s.find(_REPORT_END, i)
    body = s[i + len(_REPORT_START): j if j != -1 else len(s)]
    return body.strip()


# ── 子进程执行 KnowKing ─────────────────────────────────────

def _uv_bin() -> str:
    """定位 uv 可执行文件：PATH 优先，回退用户默认安装位置。"""
    found = shutil.which("uv")
    if found:
        return found
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for cand in (Path(home) / ".local" / "bin" / "uv.exe",
                 Path(home) / ".local" / "bin" / "uv"):
        if cand.exists():
            return str(cand)
    return "uv"  # 交给系统找；找不到则 FileNotFoundError → 任务 error


def _child_env() -> dict:
    """给 KnowKing 子进程的环境：剔除本机 DEEPSEEK*/KK_*/provider 键，
    让 KnowKing 的 .env 成为权威；保留 PATH 等（uv 需要）。"""
    env = {}
    for k, v in os.environ.items():
        if k in _STRIP_ENV_EXACT:
            continue
        if any(k.startswith(pre) for pre in _STRIP_ENV_PREFIXES):
            continue
        env[k] = v
    return env


def _default_runner(topic: str) -> tuple[bool, str]:
    """跑 `uv run kk ask "<topic>"`（cwd=KnowKing 项目，让其 .env 生效）。
    返回 (成功?, 报告或错误文本)。"""
    proj = project_dir()
    argv = [_uv_bin(), "run", "--project", str(proj), "kk", "ask", topic]
    try:
        r = subprocess.run(
            argv, cwd=str(proj), env=_child_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_timeout_s(),
        )
    except subprocess.TimeoutExpired:
        return False, f"KnowKing 运行超时（>{_timeout_s()}s）"
    except FileNotFoundError:
        return False, "找不到 uv 命令（KnowKing 需要 uv 运行）"
    except Exception as e:  # noqa: BLE001 — 任何失败都转成任务 error，绝不外抛
        return False, f"KnowKing 启动失败: {e}"
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        return False, msg[-800:] or f"KnowKing 退出码 {r.returncode}"
    return True, extract_report(r.stdout)


# ── 任务运行（后台线程目标） ────────────────────────────────

def _run_job(job_id: str, *, runner: Optional[Callable[[str], tuple]] = None,
             jdir: Optional[Path] = None) -> None:
    """执行一个任务并把结果写回任务文件。任何异常都落成 error 状态，绝不外抛。"""
    d = jobs_dir(jdir)
    job = _read_job(d, job_id)
    if job is None:
        _log.warning("KnowKing 任务文件缺失，跳过: %s", job_id)
        return
    run = runner or _default_runner
    try:
        ok, text = run(job["topic"])
    except Exception as e:  # noqa: BLE001
        _log.exception("KnowKing 任务执行异常: %s", job_id)
        ok, text = False, f"内部错误: {e}"
    if ok:
        job["status"] = "done"
        job["report"] = text
    else:
        job["status"] = "error"
        job["error"] = text
    _write_job(d, job)


# ── 提交 ────────────────────────────────────────────────────

def submit(topic: str, channel: str, user: str, member: str, *,
           background: bool = True,
           runner: Optional[Callable[[str], tuple]] = None,
           jdir: Optional[Path] = None) -> tuple[Optional[str], str]:
    """提交一个 KnowKing 查询。返回 (job_id | None, 给用户的即时回复)。

    - 主题为空 → (None, 提示)。
    - 同频道同用户已有 running 任务 → (None, 忙提示)，避免并发刷爆。
    - 否则落盘 running 任务；background=True 起守护线程后台跑，返回"已开始"。
    """
    topic = (topic or "").strip()
    if not topic:
        return None, "要查什么？请给出主题内容（如\"用 knowking 查大家怎么看 X\"）。"
    d = jobs_dir(jdir)
    if _has_running_for_user(d, channel, str(user)):
        return None, "你已经有一个 KnowKing 查询正在进行中，出结果会自动发你，请稍候。"

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job = {"id": job_id, "channel": channel, "user": str(user), "member": member or "",
           "topic": topic, "status": "running", "report": "", "error": "",
           "created_at": _now()}
    _write_job(d, job)

    if background:
        threading.Thread(
            target=_run_job, args=(job_id,),
            kwargs={"runner": runner, "jdir": d},
            daemon=True, name=f"knowking-{job_id}").start()

    ack = (f"🔎 KnowKing 已开始查询「{topic}」，正在跨社交平台搜集（约需几分钟），"
           f"出报告后自动发你。")
    return job_id, ack


# ── 投递（传输层轮询调用） ──────────────────────────────────

def _format_delivery(job: dict) -> str:
    topic = job.get("topic", "")
    if job.get("status") == "done":
        return f"🔎 KnowKing 报告 —「{topic}」\n\n{job.get('report', '').strip()}"
    return f"⚠️ KnowKing 查询「{topic}」失败：{job.get('error', '') or '未知原因'}"


def poll_and_deliver(send_fn: Callable[[str, str], object], channel: str, *,
                     jdir: Optional[Path] = None,
                     stale_seconds: int = _DEFAULT_STALE_S) -> int:
    """把某频道已完成/失败的 KnowKing 任务推送给发起人。返回成功投递条数。

    - 只处理 channel 匹配的任务；running 未超时的跳过。
    - running 超过 stale_seconds（bot 重启/线程死）→ 记为超时 error 再投递。
    - 投递成功即删任务文件（防重复推送）；send_fn 抛异常则保留文件下轮重试。
    - 单条投递异常不影响其余任务。
    """
    d = jobs_dir(jdir)
    delivered = 0
    now = _now()
    for job, f in _iter_jobs(d):
        if job.get("channel") != channel:
            continue
        status = job.get("status")
        if status == "running":
            age = now - float(job.get("created_at") or 0)
            if age < stale_seconds:
                continue
            job["status"] = "error"
            job["error"] = "KnowKing 查询超时或中断（可重试）"
            status = "error"
        elif status not in ("done", "error"):
            continue
        try:
            send_fn(job["user"], _format_delivery(job))
        except Exception:  # noqa: BLE001 — 投递失败保留文件，下轮重试；不影响其他任务
            _log.exception("KnowKing 报告投递失败，保留任务下轮重试: %s", job.get("id"))
            continue
        delivered += 1
        try:
            f.unlink(missing_ok=True)
        except OSError:
            _log.exception("KnowKing 任务文件删除失败（已投递）: %s", job.get("id"))
    return delivered
