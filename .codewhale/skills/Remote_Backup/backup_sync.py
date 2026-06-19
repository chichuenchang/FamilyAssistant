"""
Remote Backup — 同步引擎（真实实现）

本模块是调用方唯一入口：脏标记、防抖触发、镜像同步、恢复、校验、状态。
云盘操作全部走同目录 backup_provider（占位，由用户私有实现）。

调用契约：
    mark_dirty()    任何用户数据写入后调用。亚毫秒、零网络、永不抛异常。
    backup_tick()   传输层轮询里反复调用。enabled + provider 就绪 + 脏 +
                    距最后写入 >= debounce_seconds 时跑一次 sync()。
    sync()/restore()/verify()/status()  CLI 与 Agent 使用。

状态文件（均不入备份、不入 git）：
    data/.backup_manifest.json  引擎认为云端已有的内容 {rel: {sha256,size,uploaded_at}}
    data/.backup_state.json     {dirty_since, last_write, last_sync, last_error}
测试钩子：环境变量 BACKUP_STATE_DIR 重定位这两个文件（仅测试用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import backup_provider as provider

_FALLBACK_CFG = {
    "enabled": False,
    "debounce_seconds": 60,
    "include": ["data/ledger.db", "receipts", "documents", "config.json"],
    "remote_root": "FamilyAssistant",
}


def _load_cfg() -> dict:
    # BACKUP_CONFIG 环境变量可指向替代 config.json（测试隔离用，不依赖真实配置）
    cfg_path = Path(os.environ.get("BACKUP_CONFIG") or (ROOT / "config.json"))
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg = raw.get("backup")
        if isinstance(cfg, dict):
            return {**_FALLBACK_CFG, **cfg}
    except Exception:
        pass
    return dict(_FALLBACK_CFG)


CFG = _load_cfg()

_STATE_DIR = Path(os.environ.get("BACKUP_STATE_DIR") or (ROOT / "data"))
MANIFEST_FILE = _STATE_DIR / ".backup_manifest.json"
STATE_FILE = _STATE_DIR / ".backup_state.json"

# 永不进备份的路径（即使用户把 data 整个加进 include）
_HARD_EXCLUDE_NAMES = {".telegram_offset", ".doc_reminder_state",
                       ".backup_manifest.json", ".backup_state.json",
                       ".calendar_state.json", ".sync_state.json"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _excluded(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name in _HARD_EXCLUDE_NAMES:
        return True
    if "creds" in name:
        return True
    return False


def mark_dirty() -> None:
    """用户数据写入后调用。绝不抛异常——备份问题不能影响写入本身。"""
    try:
        st = _load_json(STATE_FILE)
        now = _now_iso()
        if not st.get("dirty_since"):
            st["dirty_since"] = now
        st["last_write"] = now
        _save_json(STATE_FILE, st)
    except Exception:
        pass


def _iter_local_files() -> dict[str, Path]:
    """include 集合展开为 {rel(posix): 绝对路径}，应用硬排除。"""
    out: dict[str, Path] = {}
    for entry in CFG["include"]:
        p = ROOT / entry
        if p.is_file():
            rel = p.relative_to(ROOT).as_posix()
            if not _excluded(rel):
                out[rel] = p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(ROOT).as_posix()
                if not _excluded(rel):
                    out[rel] = f
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_sqlite(path: Path) -> Path:
    """SQLite 一致性快照到临时文件（备份 API，连接打开中也安全）。调用方负责删除。"""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(str(path))
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return Path(tmp)


def _provider_ready() -> bool:
    try:
        return bool(provider.is_configured())
    except Exception:
        return False


def sync() -> dict:
    """镜像一轮：上传 新/变更，删除 本地已不存在的远端项。

    清单逐项落盘——中途崩溃只补剩余部分。
    返回 {"uploaded": [...], "deleted": [...], "skipped": int, "errors": [...]}。
    """
    started_last_write = _load_json(STATE_FILE).get("last_write")
    manifest = _load_json(MANIFEST_FILE)
    local = _iter_local_files()
    uploaded: list[str] = []
    deleted: list[str] = []
    errors: list[str] = []
    skipped = 0

    for rel, abs_p in local.items():
        snap: Path | None = None
        try:
            if abs_p.suffix == ".db":
                snap = _snapshot_sqlite(abs_p)
                src = snap
            else:
                src = abs_p
            digest = _sha256(src)
            if manifest.get(rel, {}).get("sha256") == digest:
                skipped += 1
            else:
                provider.upload(src, rel)
                manifest[rel] = {"sha256": digest, "size": src.stat().st_size,
                                 "uploaded_at": _now_iso()}
                _save_json(MANIFEST_FILE, manifest)
                uploaded.append(rel)
        except NotImplementedError:
            raise
        except Exception as e:
            errors.append(f"{rel}: {e}")
        finally:
            if snap is not None:
                try:
                    snap.unlink()
                except OSError:
                    pass

    for rel in [r for r in list(manifest) if r not in local]:
        try:
            provider.delete(rel)
            del manifest[rel]
            _save_json(MANIFEST_FILE, manifest)
            deleted.append(rel)
        except NotImplementedError:
            raise
        except Exception as e:
            errors.append(f"{rel}: {e}")

    # 已知良性竞态：此处读-改-写之间若有并发 mark_dirty（其他进程），其脏标记
    # 可能被覆盖——后果只是该次变更延迟到下一次写入才备份，不丢数据，不加锁。
    st = _load_json(STATE_FILE)
    st["last_sync"] = _now_iso()
    if errors:
        st["last_error"] = "; ".join(errors[:5])
    else:
        st["last_error"] = None
        # 同步期间没有新写入才算干净（有新写入则保持脏，下一轮再跑）
        if st.get("last_write") == started_last_write:
            st["dirty_since"] = None
    _save_json(STATE_FILE, st)
    return {"uploaded": uploaded, "deleted": deleted,
            "skipped": skipped, "errors": errors}


def backup_tick(now: datetime | None = None) -> bool:
    """传输层轮询调用。条件满足则同步一轮，返回是否跑了 sync。永不抛异常。"""
    try:
        if not CFG["enabled"]:
            return False
        st = _load_json(STATE_FILE)
        if not st.get("dirty_since"):
            return False
        last_write = st.get("last_write")
        now = now or datetime.now()
        if last_write:
            elapsed = (now - datetime.fromisoformat(last_write)).total_seconds()
            if elapsed < CFG["debounce_seconds"]:
                return False
        if not _provider_ready():
            return False
        sync()
        return True
    except Exception as e:
        try:
            st = _load_json(STATE_FILE)
            st["last_error"] = str(e)
            _save_json(STATE_FILE, st)
        except Exception:
            pass
        return False


def restore(force: bool = False) -> dict:
    """新设备引导：从云端拉回全部文件并重建清单。

    本地已有用户数据时拒绝（除非 force）——多半是跑错机器了。
    """
    if not _provider_ready():
        raise ValueError("backup_provider 未实现/未配置，无法恢复。见 SKILL.md 设置指南。")
    if not force:
        existing = [rel for rel in _iter_local_files()
                    if rel != "config.json"]
        if existing:
            raise ValueError(
                f"本地已有 {len(existing)} 个用户数据文件（如 {existing[0]}）。"
                f"确认要覆盖请加 --force。")
    remote = provider.list_remote()
    manifest: dict = {}
    downloaded: list[str] = []
    for rel in sorted(remote):
        if _excluded(rel):
            continue
        target = ROOT / rel
        provider.download(rel, target)
        manifest[rel] = {"sha256": _sha256(target), "size": target.stat().st_size,
                         "uploaded_at": _now_iso()}
        downloaded.append(rel)
    _save_json(MANIFEST_FILE, manifest)
    st = _load_json(STATE_FILE)
    st["last_sync"] = _now_iso()
    st["dirty_since"] = None
    st["last_error"] = None
    _save_json(STATE_FILE, st)
    return {"downloaded": downloaded}


def verify() -> dict:
    """清单 vs 云端列表：{"ok": [...], "missing_remote": [...], "extra_remote": [...],
    "size_mismatch": [...]}。"""
    if not _provider_ready():
        raise ValueError("backup_provider 未实现/未配置，无法校验。")
    manifest = _load_json(MANIFEST_FILE)
    remote = provider.list_remote()
    ok, missing, mismatch = [], [], []
    for rel, meta in manifest.items():
        if rel not in remote:
            missing.append(rel)
        elif remote[rel].get("size") not in (None, meta.get("size")):
            mismatch.append(rel)
        else:
            ok.append(rel)
    extra = [r for r in remote if r not in manifest]
    return {"ok": ok, "missing_remote": missing,
            "extra_remote": extra, "size_mismatch": mismatch}


def status() -> dict:
    st = _load_json(STATE_FILE)
    return {
        "enabled": bool(CFG["enabled"]),
        "configured": _provider_ready(),
        "dirty_since": st.get("dirty_since"),
        "last_write": st.get("last_write"),
        "last_sync": st.get("last_sync"),
        "last_error": st.get("last_error"),
        "files_tracked": len(_load_json(MANIFEST_FILE)),
    }
