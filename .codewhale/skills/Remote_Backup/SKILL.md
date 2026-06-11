# Remote Backup

> 用户数据云盘镜像。git 管代码，本 skill 管用户数据（账本/票据/文档/配置）。
> 可选功能，默认关闭。本 skill 是被 Agent 调用的工具，自己不做决策。

## 代码位置

```
.codewhale/skills/Remote_Backup/
├── SKILL.md            ← 本文件
├── backup_sync.py      ← 同步引擎（真实实现）：清单、脏标记、防抖、镜像、恢复
├── backup_provider.py  ← 云盘占位（用户私有实现，契约见文件头注释）
└── cli.py              ← backup-now / backup-status / backup-verify / backup-restore
```

## 工作方式

- 写入路径调 `backup_sync.mark_dirty()`（已接好：两个 CLI 的写命令 + 传输层存图）。
- 传输层轮询调 `backup_sync.backup_tick()`：启用 + provider 就绪 + 脏 + 距最后写入
  ≥ `debounce_seconds` → 跑一轮镜像。失败自动下轮重试，本地永远是事实源。
- 镜像 = 哈希比对增量上传 + 本地删除同步删远端。SQLite 用一致性快照上传。
- 备份集 = config.json `backup.include`；凭据类文件硬排除，永不上传。

## CLI

| 命令 | 行为 | Agent 可调 |
|------|------|-----------|
| `backup-now` | 立即同步（忽略防抖） | ✅ |
| `backup-status` | 状态：启用/配置/待同步/上次同步/错误 | ✅ |
| `backup-verify` | 清单 vs 云端一致性 | ✅ |
| `backup-restore [--force]` | 新设备从云端拉回全部数据 | ❌ 仅本机 |

```bash
python .codewhale/skills/Remote_Backup/cli.py backup-status
python .codewhale/skills/Remote_Backup/cli.py backup-now
python .codewhale/skills/Remote_Backup/cli.py backup-restore          # 新设备
```

## 用户开启备份（设置清单，给编码 Agent 执行）

1. 读 `backup_provider.py` 文件头契约（5 个函数）。
2. 按用户选的云盘（Google Drive / Dropbox / OneDrive / S3 / WebDAV…）实现这 5 个
   函数，替换文件内容。凭据走环境变量，不进代码、不进日志。
3. `config.json` 设 `backup.enabled: true`。
4. `backup-now` 做首次全量上传，`backup-verify` 确认一致。
5. 新设备恢复：克隆代码库 → 实现/复制 provider → `backup-restore`。

## 配置（config.json `backup` 段）

`enabled`（默认 false）/ `debounce_seconds`（60）/ `include`（备份集）/
`remote_root`（云端根目录名）。改后重启进程生效。

## 边界

- ❌ 双向同步/冲突解决（单向 本地→云端；恢复是显式手动操作）
- ❌ 上传前加密（依赖云盘自身的静态加密；未来可加）
- ❌ 版本历史（用云盘自带的）
