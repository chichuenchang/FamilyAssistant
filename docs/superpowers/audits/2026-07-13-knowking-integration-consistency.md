# Consistency Audit — 2026-07-13 (post KnowKing integration)

Scope: commit `cbaa00b`（integrate knowking into agent runtime）落地后、上线前。
Method: cavecrew-reviewer 过 diff（正确性/竞态/安全/清理）+ 主线程 grep 扫
文档/config 对代码的陈述（knowking 提及、备份排除规则、能力/配置枚举表）。
Full test suite green before and after（609 → 613 passed，4 新测试）。
另：真实微信端到端已验证（懂王触发 → 后台 uv run kk ask → 报告推回发起人）。

## Findings

### 代码（reviewer 3🟡，修 2 收 1）

1. 🟡 `knowking_jobs.submit` 忙闸门 TOCTOU — `_has_running_for_user` 检查与
   `_write_job` 落盘之间无互斥，并发提交可双双越过"同用户单任务"闸门。
   Fixed：`_SUBMIT_LOCK` 把检查+落盘圈进同一把锁；新增 8 线程并发测试。
2. 🟡 投递幂等缺口 — stale running → error 只改内存不落盘；投递后 unlink
   失败则文件停留 running，下轮重判 stale 重复推送。
   Fixed：stale→error 先 `_write_job` 落盘再投递；unlink 失败标
   `status=delivered` 落盘，下轮只清理绝不重发。各加测试。
3. 🟡 wechat 投递线程对 send 阻塞无超时 — 与既有 reminder 循环同构
   （SDK 自身超时兜底），保持一致，不改。Accepted.
4. 🔵 config.json `knowking.project_dir` 硬编码绝对路径入库 — 单用户仓库
   + `KNOWKING_DIR` 环境变量可覆盖（已文档化）。Accepted.

### 文档/配置漂移（全部已修）

5. `backup_sync.py` — Agent_Runtime SKILL.md 声称任务文件"点前缀=瞬态不进
   备份"，但备份排除是显式名单而非点前缀规则，宽 scope 会把
   `data/.knowking_jobs/`（含频道 id/查询主题）镜像上云。Fixed：
   `.knowking_jobs` 加入 `_HARD_EXCLUDE_DIRS` + 排除测试；SKILL.md 措辞改为
   指向实际机制。
6. `FamilyAssistant.md` 技能表 — Agent Runtime 行未提懂王桥/触发词。Fixed。
7. `FamilyAssistant.md` config 驱动表 — 缺 `knowking`（project_dir/timeout_s）
   行。Fixed。
8. `FamilyAssistant.md` 项目关键文件 — Agent_Runtime 行缺 knowking_jobs.py。Fixed。
9. `README.md` 功能特性 — 懂王舆情搜集整节缺失（触发词/后台+推送/外部项目
   可选依赖）。Fixed。
10. `Agent_Runtime/SKILL.md` 环境变量表缺 `KNOWKING_DIR`；依赖节缺 uv/KnowKing
    项目要求。Fixed。

## Checked clean

- 子进程注入：topic 走 argv 列表非 shell。✔
- 防冒名：`__channel`/`__user`/`member` 由 `_apply_context` 代码注入，
  LLM 不可伪造投递目标。✔
- 子环境消毒：`DEEPSEEK*`/`KK_*`/provider 键剔除，KnowKing `.env` 权威。✔
- 任务文件原子写（`.tmp` + `os.replace`）。✔
- `knowking` 不在 `ALLOWED_COMMANDS`/`_cli_path` 路由（专用工具，非 CLI 族），
  config `wechat.allowed_commands` `_comment` 无需更新。✔
- `.gitignore`：`data/`（含 `.knowking_jobs/`）整体不入库。✔
