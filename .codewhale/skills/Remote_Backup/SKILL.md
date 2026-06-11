# Remote Backup

> 用户数据云盘镜像。git 管代码，本 skill 管用户数据（账本/票据/文档/配置）。
> 可选功能，默认关闭。本 skill 是被 Agent 调用的工具，自己不做决策。

## 代码位置

```
.codewhale/skills/Remote_Backup/
├── SKILL.md            ← 本文件
├── backup_sync.py      ← 同步引擎（真实实现）：清单、脏标记、防抖、镜像、恢复
├── backup_provider.py  ← Google Drive 实现（drive.file 最小权限；契约见文件头注释）
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

## 用户开启备份（当前 provider = Google Drive）

1. Google Cloud Console：建项目 → 启用 Google Drive API → OAuth 同意屏幕
   （External，发布状态设 **In production**，否则 refresh token 7 天过期）→
   创建 OAuth 客户端（**Desktop app**）→ 拿到 Client ID / Client Secret。
2. `setx GDRIVE_CLIENT_ID "..."`、`setx GDRIVE_CLIENT_SECRET "..."`（新终端生效）。
3. 一次性授权：`python .codewhale/skills/Remote_Backup/backup_provider.py --auth`
   → 浏览器批准 → 按提示 `setx GDRIVE_REFRESH_TOKEN "..."`。
4. `config.json` 设 `backup.enabled: true`，重启机器人。
5. `backup-now` 首次全量上传，`backup-verify` 确认一致。
6. 新设备恢复：克隆代码库 → 设置 3 个环境变量 → `backup-restore`。

权限范围 `drive.file`：本应用只能看到自己上传的文件，看不到 Drive 其他内容。
云端布局：全部文件平铺在 `remote_root` 文件夹，相对路径存于 appProperties.rel。

**换云盘**：按 `backup_provider.py` 文件头契约（5 个函数）重写该文件即可，
引擎零改动。**提交 provider 前自查**：代码里不得出现任何字面 token/key，
凭据只能 `os.environ` 读取。

## 配置（config.json `backup` 段）

`enabled`（默认 false）/ `debounce_seconds`（60）/ `include`（备份集）/
`remote_root`（云端根目录名）。改后重启进程生效。

## 边界

- ❌ 双向同步/冲突解决（单向 本地→云端；恢复是显式手动操作）
- ❌ 上传前加密（依赖云盘自身的静态加密；未来可加）
- ❌ 版本历史（用云盘自带的）
