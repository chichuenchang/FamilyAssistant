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
import backup_provider

sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Agent_Runtime"))
import members as _members

_FALLBACK_CFG = {"enabled": False, "debounce_seconds": 60}


def _cfg_path() -> Path:
    return Path(os.environ.get("BACKUP_CONFIG") or (ROOT / "config.json"))


def _load_cfg() -> dict:
    try:
        raw = json.loads(_cfg_path().read_text(encoding="utf-8"))
        cfg = raw.get("backup")
        if isinstance(cfg, dict):
            return {**_FALLBACK_CFG,
                    **{k: cfg[k] for k in ("enabled", "debounce_seconds") if k in cfg}}
    except Exception:
        pass
    return dict(_FALLBACK_CFG)


CFG = _load_cfg()


def _data_dirname() -> str:
    """data_root 目录名（config.data_root，缺省 data）。备份 rel 的 <data>/ 段。"""
    try:
        raw = json.loads(_cfg_path().read_text(encoding="utf-8"))
        return raw.get("data_root") or "data"
    except Exception:
        return "data"


_DATA_DIRNAME = _data_dirname()

_STATE_DIR = Path(os.environ.get("BACKUP_STATE_DIR") or (ROOT / "data"))
STATE_FILE = _STATE_DIR / ".backup_state.json"

# 永不进备份的路径（即使用户把 data 整个加进 include）
_HARD_EXCLUDE_NAMES = {".telegram_offset", ".doc_reminder_state",
                       ".backup_manifest.json", ".backup_state.json",
                       ".calendar_state.json", ".sync_state.json",
                       ".image_gc_state.json"}


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


def _scope_to_prefix(token: str) -> str:
    """scope token → ROOT 相对前缀（posix）。

    config.json 是 data_root 外唯一备份文件，识别为别名；其余一律 data_root 相对。
    """
    if token == "config.json":
        return "config.json"
    return f"{_DATA_DIRNAME}/{token}"


def _member_files(scopes: list[str]) -> dict[str, Path]:
    """成员 scope 集合 → {rel(posix): 绝对路径}，应用硬排除。

    token 指向文件则收该文件；指向目录则递归；不存在则静默跳过（migrate 前的空目录）。
    前缀按目录边界匹配（rglob 天然如此），不会把 data/Jimbo 当成 data/Alex。
    """
    out: dict[str, Path] = {}
    for token in scopes:
        p = ROOT / _scope_to_prefix(token)
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


_REGISTRY = {"google_drive": backup_provider.GoogleDriveProvider}


def _make_provider(pref: dict):
    cls = _REGISTRY.get(pref["provider"])
    if cls is None:
        raise RuntimeError(f"未知备份 provider: {pref['provider']}")
    return cls(pref["cred_prefix"], pref["remote_root"])


def _members_path():
    """测试钩子：BACKUP_MEMBERS 指向替代 members.json，否则用 members 默认。"""
    p = os.environ.get("BACKUP_MEMBERS")
    return Path(p) if p else None


def _backup_members() -> list[tuple[str, dict]]:
    """有 backup 块的成员 [(name, pref)]；pref 富化 name + dir（成员目录名）。"""
    mp = _members_path()
    out: list[tuple[str, dict]] = []
    for name in _members.member_names(mp):
        pref = _members.backup_pref(name, mp)
        if pref:
            out.append((name, {**pref, "name": name,
                               "dir": _members.member_dir_name(name, mp)}))
    return out


def _resolve(member: str) -> dict | None:
    for name, pref in _backup_members():
        if name == member:
            return pref
    return None


def _member_state_dir(pref: dict) -> Path:
    return ROOT / _DATA_DIRNAME / pref["dir"]


def _member_manifest_file(pref: dict) -> Path:
    return _member_state_dir(pref) / ".backup_manifest.json"


def _member_state_file(pref: dict) -> Path:
    return _member_state_dir(pref) / ".backup_state.json"


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


def sync(member: str) -> dict:
    """镜像一成员 scope 集合：上传 新/变更，删除 本地已无的远端项。

    清单逐项落盘——中途崩溃只补剩余。返回结果含该成员维度与 clean_write。
    """
    pref = _resolve(member)
    if pref is None:
        raise ValueError(f"成员 {member} 无 backup 配置")
    prov = _make_provider(pref)
    manifest_file = _member_manifest_file(pref)
    started_last_write = _load_json(STATE_FILE).get("last_write")
    manifest = _load_json(manifest_file)
    local = _member_files(pref["scopes"])
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
                prov.upload(src, rel)
                manifest[rel] = {"sha256": digest, "size": src.stat().st_size,
                                 "uploaded_at": _now_iso()}
                _save_json(manifest_file, manifest)
                uploaded.append(rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")
        finally:
            if snap is not None:
                try:
                    snap.unlink()
                except OSError:
                    pass

    # 镜像删除守卫：local 全空但清单非空 = 可疑（盘未挂/数据根配置错/scope 误清空）。
    # 跳过删除以防一轮抹掉整个远端备份；真要清空远端需人工介入。本地始终是事实源。
    if not local and manifest:
        errors.append("scope 解析为空但清单非空：跳过镜像删除以防误删远端（检查 scopes/数据根）")
    else:
        for rel in [r for r in list(manifest) if r not in local]:
            try:
                prov.delete(rel)
                del manifest[rel]
                _save_json(manifest_file, manifest)
                deleted.append(rel)
            except Exception as e:
                errors.append(f"{rel}: {e}")

    mst = _load_json(_member_state_file(pref))
    mst["last_sync"] = _now_iso()
    mst["last_error"] = "; ".join(errors[:5]) if errors else None
    _save_json(_member_state_file(pref), mst)
    return {"member": member, "uploaded": uploaded, "deleted": deleted,
            "skipped": skipped, "errors": errors,
            "clean_write": _load_json(STATE_FILE).get("last_write") == started_last_write}


def backup_tick(now: datetime | None = None) -> bool:
    """传输层轮询调用。脏 + 防抖到点则遍历成员各同步一轮。永不抛异常。

    全局时钟（dirty_since/last_write）共享；仅当所有尝试过的成员都成功且期间无新写入
    才清脏，否则保持脏，下轮重试（sha 闸门 → 不重复上传）。返回是否至少跑了一个成员。
    """
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
        ran = False
        all_clean = True
        for name, pref in _backup_members():
            if not pref["enabled"]:
                continue
            try:
                prov = _make_provider(pref)
                if not prov.is_configured():
                    continue                       # 未配置：不算失败，不阻塞清脏
                res = sync(name)
                ran = True
                if res["errors"] or not res["clean_write"]:
                    all_clean = False
            except Exception as e:
                all_clean = False
                try:
                    mst = _load_json(_member_state_file(pref))
                    mst["last_error"] = str(e)
                    _save_json(_member_state_file(pref), mst)
                except Exception:
                    pass
        if ran and all_clean:
            st = _load_json(STATE_FILE)
            if st.get("last_write") == last_write:
                st["dirty_since"] = None
                _save_json(STATE_FILE, st)
        return ran
    except Exception as e:
        try:
            st = _load_json(STATE_FILE)
            st["last_error"] = str(e)
            _save_json(STATE_FILE, st)
        except Exception:
            pass
        return False


def restore(member: str, force: bool = False, override: dict | None = None) -> dict:
    """从某成员的云端拉回其文件并重建该成员清单。

    override（dict: provider/cred_prefix/remote_root/scopes/dir）用于新设备引导：
    members.json 尚未恢复、成员无 backup 块时，由 CLI 旗标提供 provider 配置。
    """
    pref = _resolve(member) or override
    if pref is None:
        raise ValueError(f"成员 {member} 无 backup 配置，且未提供 override（--prefix/--remote-root）。")
    if "dir" not in pref:
        pref = {**pref, "dir": _members.member_dir_name(member, _members_path())}
    prov = _make_provider(pref)
    if not prov.is_configured():
        raise ValueError("provider 未配置（检查环境变量），无法恢复。")
    if not force:
        existing = [rel for rel in _member_files(pref.get("scopes") or [])
                    if rel != "config.json"]
        if existing:
            raise ValueError(
                f"本地已有 {len(existing)} 个文件（如 {existing[0]}）。确认覆盖请加 --force。")
    remote = prov.list_remote()
    manifest: dict = {}
    downloaded: list[str] = []
    for rel in sorted(remote):
        if _excluded(rel):
            continue
        target = ROOT / rel
        prov.download(rel, target)
        manifest[rel] = {"sha256": _sha256(target), "size": target.stat().st_size,
                         "uploaded_at": _now_iso()}
        downloaded.append(rel)
    _save_json(_member_manifest_file(pref), manifest)
    mst = _load_json(_member_state_file(pref))
    mst["last_sync"] = _now_iso()
    mst["last_error"] = None
    _save_json(_member_state_file(pref), mst)
    return {"member": member, "downloaded": downloaded}


def verify(member: str) -> dict:
    """某成员清单 vs 其云端列表。"""
    pref = _resolve(member)
    if pref is None:
        raise ValueError(f"成员 {member} 无 backup 配置")
    prov = _make_provider(pref)
    if not prov.is_configured():
        raise ValueError("provider 未配置，无法校验。")
    manifest = _load_json(_member_manifest_file(pref))
    remote = prov.list_remote()
    ok, missing, mismatch = [], [], []
    for rel, meta in manifest.items():
        if rel not in remote:
            missing.append(rel)
        elif remote[rel].get("size") not in (None, meta.get("size")):
            mismatch.append(rel)
        else:
            ok.append(rel)
    extra = [r for r in remote if r not in manifest]
    return {"member": member, "ok": ok, "missing_remote": missing,
            "extra_remote": extra, "size_mismatch": mismatch}


def status(member: str | None = None) -> dict:
    """全局时钟 + 每成员一行（启用/配置/provider/last_sync/last_error/已跟踪文件数）。"""
    clock = _load_json(STATE_FILE)
    rows = []
    for name, pref in _backup_members():
        if member and name != member:
            continue
        try:
            configured = _make_provider(pref).is_configured()
        except Exception:
            configured = False
        mst = _load_json(_member_state_file(pref))
        rows.append({
            "member": name,
            "enabled": pref["enabled"],
            "configured": configured,
            "provider": pref["provider"],
            "remote_root": pref["remote_root"],
            "last_sync": mst.get("last_sync"),
            "last_error": mst.get("last_error"),
            "files_tracked": len(_load_json(_member_manifest_file(pref))),
        })
    return {"enabled": bool(CFG["enabled"]),
            "dirty_since": clock.get("dirty_since"),
            "last_write": clock.get("last_write"),
            "members": rows}
