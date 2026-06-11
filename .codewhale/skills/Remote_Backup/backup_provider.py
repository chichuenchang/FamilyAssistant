"""
Remote Backup — 云盘 Provider 占位实现（用户私有部分）

这是整个 skill 里**唯一**留给用户自己实现的文件。云盘选择和凭据高度私密，
由用户让自己的编码 Agent 按下面的契约实现（Google Drive / Dropbox / OneDrive /
S3 / WebDAV 均可），替换本文件内容。实现指南见同目录 SKILL.md。

契约（backup_sync 只按此调用，不关心云盘细节）：

- 所有 remote_rel 为相对路径、正斜杠（如 "receipts/2026-06/x.jpg"）。
  云端实际位置 = config.json backup.remote_root + "/" + remote_rel，
  remote_root 由本模块自己读取并拼接。
- 凭据一律走环境变量，不写进代码、不打日志。
- 上传必须覆盖同名远端文件（镜像语义）。
- list_remote() 返回 {remote_rel: {"size": int}}，列出 remote_root 下全部文件。
- 任何网络/认证错误：抛普通 Exception（引擎会记录并在下一轮重试）。

未实现状态：is_configured() 返回 False，其余抛 NotImplementedError——
引擎据此优雅跳过，整个 skill 处于"已接线、未启用"的安全状态。
"""

from pathlib import Path

_MSG = "backup_provider 未实现 — 见 .codewhale/skills/Remote_Backup/SKILL.md 设置指南"


def is_configured() -> bool:
    """凭据就绪且可用时返回 True。未实现时必须返回 False。"""
    return False


def upload(local_path: Path, remote_rel: str) -> None:
    """把本地文件上传到云端 remote_root/remote_rel，覆盖已存在的。"""
    raise NotImplementedError(_MSG)


def delete(remote_rel: str) -> None:
    """删除云端 remote_root/remote_rel。文件不存在视为成功。"""
    raise NotImplementedError(_MSG)


def list_remote() -> dict:
    """列出 remote_root 下全部文件：{remote_rel: {"size": int}}。"""
    raise NotImplementedError(_MSG)


def download(remote_rel: str, local_path: Path) -> None:
    """把云端 remote_root/remote_rel 下载到本地 local_path（覆盖）。"""
    raise NotImplementedError(_MSG)
