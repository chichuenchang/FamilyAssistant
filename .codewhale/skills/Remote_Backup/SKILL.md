# Remote Backup

> 用户数据云盘镜像。git 管代码，本 skill 管用户数据（账本/票据/文档/配置）。
> 可选功能，默认关闭。本 skill 是被 Agent 调用的工具，自己不做决策。

## 代码位置

```
.codewhale/skills/Remote_Backup/
├── SKILL.md            ← 本文件
├── backup_sync.py      ← 同步引擎（真实实现）：按成员镜像、脏标记、防抖、恢复
├── backup_provider.py  ← Google Drive provider 类（drive.file 最小权限；凭据前缀化）
├── cli.py              ← backup-now / backup-status / backup-verify / backup-restore
└── ../Agent_Runtime/
    ├── members.py      ← backup_pref()：每成员 backup 块解析
    └── migrate_backup.py ← 旧→新一次性迁移（bot 停用时运行）
```

## 工作方式

- **按成员备份**：每个成员在 `members.json` 里有一个 `backup` 块，列出 `scopes`
  （ROOT 相对路径前缀；`config.json` 是基础设施别名，始终可包含）。
  无 `backup` 块的成员不备份（仅本地）。
- 写入路径调 `backup_sync.mark_dirty()`（已接好：两个 CLI 的写命令 + 传输层存图）。
- 传输层轮询调 `backup_sync.backup_tick()`：启用 + 脏 + 距最后写入
  ≥ `debounce_seconds` → 遍历各成员，每个用各自 provider/凭据跑一轮独立镜像。
  任一成员失败保留脏，下轮重试。本地永远是事实源。
- 镜像 = 该成员 scope 内文件哈希比对增量上传 + 本地删除同步删远端。
  SQLite 用一致性快照上传。
- **全局防抖时钟** `data/.backup_state.json` 一份（共享）；**每成员独立清单与状态**
  `data/<Member>/.backup_manifest.json` + `.backup_state.json`。
- 凭据类文件硬排除，永不上传。

## CLI

| 命令 | 行为 | Agent 可调 |
|------|------|-----------|
| `backup-now [--member NAME]` | 立即同步指定/全部启用成员（忽略防抖） | ✅ |
| `backup-status [--member NAME]` | 全局 + 每成员状态：启用/配置/待同步/文件数/错误 | ✅ |
| `backup-verify [--member NAME]` | 每成员清单 vs 云端一致性 | ✅ |
| `backup-restore --member NAME [--force] [--provider P] [--prefix P] [--remote-root R] [--dir D]` | 新设备：从云端恢复某成员数据（仅本机） | ❌ 仅本机 |
| `backup-reorg --member NAME` | 一次性：把云端平铺文件按 rel 归入镜像文件夹树（元数据移动，零重传），幂等 | ❌ 仅本机 |

```bash
python .codewhale/skills/Remote_Backup/cli.py backup-status
python .codewhale/skills/Remote_Backup/cli.py backup-status --member "Jim Zheng"
python .codewhale/skills/Remote_Backup/cli.py backup-now
python .codewhale/skills/Remote_Backup/cli.py backup-restore --member "Jim Zheng" --prefix GDRIVE --remote-root FamilyAssistant
python .codewhale/skills/Remote_Backup/cli.py backup-reorg --member "Jim Zheng"
```

## 用户开启备份（当前 provider = Google Drive）

每个成员独立配置，可备份到各自的云盘账号。

1. 在 `members.json` 中为目标成员添加 `backup` 块：
   ```json
   "Jim Zheng": {
     "dir": "Jim",
     "backup": {
       "provider": "google_drive",
       "cred_prefix": "GDRIVE",
       "remote_root": "FamilyAssistant",
       "enabled": true,
       "scopes": ["Jim", "Family", "config.json"]
     }
   }
   ```
   字段说明：
   - `provider` — 云盘类型（目前 `google_drive`）；
   - `cred_prefix` — 环境变量前缀，缺省 `GDRIVE`（凭据变量名 `{prefix}_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN`）；
   - `remote_root` — 云端根目录名，缺省取成员目录名（`dir` 字段）；
   - `enabled` — 是否启用（`true` / `false`）；
   - `scopes` — ROOT 相对前缀列表；`config.json` 是基础设施别名，`members.json` 是注册表别名，
     如需备份写明即可。

2. Google Cloud Console：建项目 → 启用 Google Drive API → OAuth 同意屏幕
   （External，发布状态设 **In production**，否则 refresh token 7 天过期）→
   创建 OAuth 客户端（**Desktop app**）→ 拿到 Client ID / Client Secret。

3. 设置凭据环境变量（前缀与成员 `cred_prefix` 一致）：
   ```cmd
   setx GDRIVE_CLIENT_ID "..."
   setx GDRIVE_CLIENT_SECRET "..."
   ```
   （新终端生效）。若成员使用自定义前缀（如 `WLI_GDRIVE`），变量名相应调整。

4. 一次性授权：
   ```bash
   python .codewhale/skills/Remote_Backup/backup_provider.py --auth [--prefix PREFIX]
   ```
   → 浏览器批准 → 按提示 `setx {PREFIX}_REFRESH_TOKEN "..."`。

5. `config.json` 设 `backup.enabled: true`，重启机器人。

6. `backup-now` 首次全量上传，`backup-verify` 确认一致。

权限范围 `drive.file`：本应用只能看到自己上传的文件，看不到 Drive 其他内容。
云端布局：远端以嵌套文件夹镜像本地目录树，文件按相对路径存放于 `remote_root` 下的逐级子文件夹中（不再平铺）。`appProperties.rel` 仍保存在每个文件上作为引擎的最终依据——查找/列出/删除/恢复均以此字段定位，即使文件被手动移入其他文件夹也不影响，镜像健壮。权限范围 `drive.file` 不变。

**换云盘**：按 `backup_provider.py` 的 `GoogleDriveProvider` 类接口契约
（`is_configured` / `upload` / `delete` / `list_remote` / `download`）实现新类并注册到
`backup_sync._REGISTRY` 即可，引擎零改动。**提交 provider 前自查**：代码里不得出现任何字面
token/key，凭据只能 `os.environ` 读取。

## 新设备恢复（bootstrap）

新设备上无 `members.json`（注册表存在 Jim 的备份里），存在鸡-蛋问题：
需先恢复 Jim → 拿到注册表 → 再恢复其他成员。

1. 克隆代码库，设置 `GDRIVE_CLIENT_ID` / `GDRIVE_CLIENT_SECRET` / `GDRIVE_REFRESH_TOKEN`。
2. **先恢复 Jim**（引导模式，无需注册表已有该成员）：
   ```bash
   python .codewhale/skills/Remote_Backup/cli.py backup-restore --member "Jim Zheng" --prefix GDRIVE --remote-root FamilyAssistant
   ```
   → 拉回 `members.json` + `config.json` + Jim 的 scope 文件。
3. 恢复其他成员（现在注册表已知，正常模式）：
   ```bash
   python .codewhale/skills/Remote_Backup/cli.py backup-restore --member "成员名"
   ```
   若该成员凭据前缀非默认，加 `--prefix`；若云端根目录非目录名，加 `--remote-root`。
   本地已有数据可加 `--force` 覆盖。

## 配置（config.json `backup` 段）

`enabled`（默认 false）/ `debounce_seconds`（60）。改后重启进程生效。
`include` / `remote_root` 已移至 `members.json` 各成员 `backup` 块的 `scopes` / `remote_root`。

## 升级备注（folder-mirror）

云端布局升级到文件夹镜像后，已有远端数据按情况一次性处理：
- **rel 与当前本地一致、但平铺**：`backup-reorg --member NAME` 把平铺文件按 rel 归入嵌套
  文件夹（元数据移动，零字节重传；幂等，第二次零移动；无远端文件则跳过）。
- **rel 来自更早的旧存储布局（与当前不符）**：`backup-now --member NAME` 重新镜像（上传当前
  布局 + 删除旧路径文件），再 `backup-verify --member NAME` 确认一致。

## 边界

- ❌ 双向同步/冲突解决（单向 本地→云端；恢复是显式手动操作）
- ❌ 上传前加密（依赖云盘自身的静态加密；未来可加）
- ❌ 版本历史（用云盘自带的）
- ✅ 按成员隔离：每个成员用自己的 provider/账号/凭据。Jim 已配置云端备份，
  其他成员默认仅本地，直到在 `members.json` 添加 `backup` 块 + 凭据。
