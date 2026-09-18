# Agent Runtime

> Agent 运行时 skill：频道无关的 `Agent` 大脑 + 各远程频道传输层（微信、Telegram、未来其他）。让用户从手机上通过各类聊天软件远程操控电脑上的 Family Assistant。核心思想：**一个频道无关的 Agent，多个轻量传输层**。

## 概述

本 skill 自带全部代码，位于本目录 `.codewhale/skills/Agent_Runtime/`：

```
.codewhale/skills/Agent_Runtime/
├── SKILL.md            ← 本文件
├── agent_core.py       ← 频道无关 Agent（共用大脑）
├── llm_client.py       ← DeepSeek 调用 + /model /effort 每用户覆盖
├── context_budget.py   ← 历史 token 粗估 + 整轮裁剪（纯函数）
├── transport_base.py   ← 频道共用生命周期（闸门/投递/后台节拍）；新增频道继承它
├── skill_registry.py   ← 发现/合并各 skill 的 agent_tools.py（manifest 契约见模块头）
├── tool_runtime.py     ← manifest 共用：run_cli / schema 助手 / 路径闸门
├── agent_tools.py      ← 本目录自带工具（knowking / send_file）
├── bootstrap.py        ← sys.path 单一入口
├── backup_hook.py      ← 各 CLI 写入后 mark_dirty
├── jsonfile.py         ← config.json / 状态文件读写（缺失/损坏读作 {}）
├── google_oauth.py     ← Google provider 共用 --auth 回环授权
├── members.py          ← 成员注册表（频道 id → 成员名；存 git 忽略的 data/members.json）
├── paths.py            ← 磁盘布局解析（数据落盘位置的单一事实来源）
├── migrate_storage.py  ← 旧单库/旧目录 → 按成员分库的一次性迁移
├── knowking_jobs.py    ← 懂王（KnowKing）跨平台舆情桥：后台跑 + 完成后推送
├── wechat_ilink.py     ← 微信传输层
└── telegram_bot.py     ← Telegram 传输层
```

所有远程频道共用同一个大脑 —— 本目录 `agent_core.py` 里的 `Agent`。无论消息从微信还是 Telegram 进来，Agent 行为、指令、工具完全一致。频道只负责"收消息 → 转交 Agent → 回消息"，不含任何业务逻辑。业务逻辑（记账/查账）在 `.codewhale/skills/Expense_Tracker/cli.py`（Agent 经 subprocess 调用），OCR 在 `.codewhale/skills/OCR/ocr.py`（进程内 import；全部 skill 目录经 `bootstrap.py` 一行挂上）。

```
微信     ─┐
Telegram ─┼─► Agent.handle(text, user) ─► DeepSeek + 工具 ─► 回复
未来频道 ─┘
```

## 架构契约

新频道只需满足这个契约，零改 Agent：

```python
from agent_core import Agent   # 传输层与 agent_core 同目录

agent = Agent()
reply = agent.handle(text, user="<频道内唯一id>", member="<成员名>")        # 文字消息
reply = agent.handle_media(paths, text, user="<频道内唯一id>", member="<成员名>")  # 图片/PDF + 随后文字指令
```

- 来件（图片/PDF）不回复：`Transport.on_media` 按 `user` 静默攒着，下条文字（Telegram 附言也算）到了才连同全部来件调 `handle_media`，交出即不再攒；文字明显与来件无关时 LLM 按提示让来件作废。命令（`agent_core.is_command`）不带来件：`/clear` 连来件一起清，`/model` `/effort` 时来件继续等。下载失败仍提示重发。攒件在内存，进程重启即丢。

- `user` = 该频道内用户/会话的唯一标识（微信 `from_user`、Telegram `chat_id`）。Agent 按 `user` 隔离对话历史，互不串台。
- `member` = `members.resolve(频道, 频道id)` 解析出的成员名。**必传**：为空时 Agent 直接返回空串（防御纵深，未注册来源不碰 LLM）。
- `handle` 返回的字符串即最终回复，原样发回频道即可。
- `Agent()` 构造时从 `config.json` 提取合法值组装 system prompt（不嵌入 FamilyAssistant.md，省 token），进程内常驻复用，不要每条消息都 new。
- **上下文自动管理**（旋钮在 `config.json` `agent` 块，`Agent()` 构造参数可覆盖，0=关闭）：`context_max_tokens`（默认 30000）= 每用户对话历史 token 预算，超出从最旧一问一答成对丢弃，保留最近上下文；`idle_clear_hours`（默认 4）= 用户闲置超过 N 小时后，下一条消息前自动清空其对话历史。用户随时可发 `/clear`（或"清除上下文"）手动清空。

## 现有频道

传输层文件均在本目录。命令从项目根目录执行：

| 频道 | 传输层 | 协议 | 启动 |
|------|--------|------|------|
| **微信** | `wechat_ilink.py` | weixin-ilink（扫码登录，长轮询） | `python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run` |
| **Telegram** | `telegram_bot.py` | Telegram Bot API（长轮询） | `python .codewhale/skills/Agent_Runtime/telegram_bot.py` |

```bash
# 微信：本地命令行测试（不连微信）
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode test
# 微信：扫码登录并运行
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run
# 微信：换账号重新扫码
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --relogin

# Telegram：设好 token 直接跑
python .codewhale/skills/Agent_Runtime/telegram_bot.py

# 两个 Bot 默认开调试日志（写 data/.state/bot_debug.log）；用 --no-debug 关闭
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --no-debug
python .codewhale/skills/Agent_Runtime/telegram_bot.py --no-debug
```

微信凭据加密存于 `data/.state/wechat_creds.json`；Telegram 去重 offset 存于 `data/.state/.telegram_offset`。
微信引用反查缓存存于 `data/.state/wechat_recent_msgs.json`（近期入站消息）与
`data/.state/wechat_sent_msgs.json`（bot 出站回复，按时间戳匹配）——均为运行时状态，不进备份。
微信 Bot 启动时抢单实例锁（绑定 `127.0.0.1:47831`）：双开会导致每条消息处理/回复两次，后启动的进程直接退出。

## 新增频道

加一个频道 = 继承 `transport_base.Transport`，实现三个 send_*，把 SDK 消息翻译成 `on_text` / `on_media`。
闸门、节流刷新、来件落盘、哨兵拆分投递、异常兜底、到期提醒 / 懂王投递 / 备份节拍全在基类，不复制。

```python
# .codewhale/skills/Agent_Runtime/mychannel_bot.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # 挂全部 skill 目录
from transport_base import Transport

class MyTransport(Transport):
    channel = "mychannel"          # members.json 里的频道键；member-add 需支持该键
    tag = "my"                     # 控制台前缀
    def send_text(self, target, text): sdk.send(target, text)
    def send_photo(self, target, path): sdk.send_image(target, path)
    def send_document(self, target, path): sdk.send_file(target, path)

def run():
    t = MyTransport()
    for msg in sdk.receive_loop():                       # 收消息循环
        member = t.gate(msg.sender_id)                   # 未注册 → None，静默丢弃
        if member is None:
            continue
        if msg.is_image or msg.is_pdf:
            path = t.inbox_path(member, ".pdf" if msg.is_pdf else ".jpg")
            msg.save(path); t.mark_dirty()
            t.on_media(msg.sender_id, msg.sender_id, member, path)
        else:
            t.on_text(msg.sender_id, msg.sender_id, member, msg.text, quoted=msg.quoted_text)
        t.background_tick()                              # 轮询型 SDK：每轮一次（跑各 manifest 的 SLOW_TICKS + FAST_TICKS）
    # 阻塞型 SDK（无轮询钩子）：改用 t.start_background_threads() 再 sdk.run()

if __name__ == "__main__":
    run()
```

要点：
- `on_text(target, user, member, text, quoted)`：target = 本频道 send_* 认得的投递目标，user = 频道内唯一 id（隔离对话历史）。引用内容传 `quoted`，基类套围栏前置（`with_quote`）。
- 图片与 PDF 同一入口 `on_media`；path 为 None 时基类回"请重发"。非 PDF 文件自行回"暂不支持"。
- 长回复分段（如 Telegram 4096 字限制）在 send_text 里做（见 `telegram_bot.py:send_message`）。
- 微信 iLink 的 `ref_msg` 无内容（实测 2026-07-10），需本地缓存反查——见 `wechat_ilink.py:_quoted_text`；Telegram `reply_to_message` 自带原文。
- 不在传输层写任何业务逻辑 —— 全部交给 Agent。

## 懂王（KnowKing）跨平台舆情桥

外部独立 uv 项目 [KnowKing](file:///C:/Users/slimj/PROJECTS/KnowKing)（`kk ask "<主题>"`：DeepSeek agent 跨
YouTube/X/Reddit/TikTok/Instagram/Bilibili/Zhihu 搜集"大家在怎么说"，出中立报告）经
`knowking_jobs.py` 接入 Agent，**不改 KnowKing 仓库**。

- **触发**：仅当用户**显式说出 `knowking` / `kk` / `懂王`**（如"用 knowking 查大家怎么看 X"）。
  普通查事实/新闻仍走 `anysearch_search`/`web_search`。工具描述与 system prompt 双重约束。
- **后台 + 推送模型**（`kk ask` 耗时数分钟，同步会卡死会话）：`knowking` 工具调
  `knowking_jobs.submit()` → 落盘 `running` 任务 + 起守护线程跑 `uv run kk ask` → 立即返回
  "已开始"给用户；出报告后由传输层轮询 `poll_and_deliver(send_fn, channel)` 把报告推回**发起人**
  （manifest `FAST_TICKS`；Telegram 挂在长轮询尾部 ~30s，微信走 `fast-tick` 守护线程 ~20s）。
- **频道上下文注入**：`Agent(channel=...)`（各传输层构造时传 `"wechat"`/`"telegram"`）+ handle 里
  `_apply_context` 把 `__channel`/`__user`/`member` 注入 `knowking` 工具参数（代码确定性，LLM 不得伪造投递目标）。本地测试无 channel → 工具返回"仅正式频道可用"。
- **任务落盘** `data/.state/.knowking_jobs/<id>.json`（运行时瞬态，已列入 `backup_sync._HARD_EXCLUDE_DIRS`
  绝不进云备份；投递成功即删，删除失败标 delivered 下轮只清理绝不重发）。
  `running` 超 `stale_seconds`（默认 1800s，bot 重启/线程死）→ 记超时 error 再推送。同频道同用户
  只允许一个在跑（busy 拦截）。
- **.env 权威**：子进程 `cwd` 设为 KnowKing 项目根（让其 `dotenv_values(".env")` 读到自己的
  `RAPIDAPI_KEY`/`JUSTONEAPI_TOKEN`/`DEEPSEEK_API_KEY`），并把本机 `DEEPSEEK*`/`KK_*`/provider
  键从子环境剔除（`_child_env`），保证 KnowKing 用自己的密钥/模型，不被 bot 进程环境污染。
- **配置**：`config.json` `knowking.project_dir`（或环境变量 `KNOWKING_DIR`）定位项目根，
  `knowking.timeout_s` 定子进程超时。需 `uv` 在 PATH（`knowking_jobs._uv_bin` 回退用户默认安装位置）。
- **KnowKing 环境自建**：调用走 `uv run --no-sync`，只用 KnowKing 已有的 `.venv`，uv 不联网。
  该 venv 由 pip 建（KnowKing 需 Python ≥3.13）：
  `py -3.13 -m venv .venv && .venv\Scripts\pip install -e .`（在 KnowKing 项目根执行）。
  依赖变更后需重跑该命令。本机 VPN 会向所有进程注入 hook DLL，uv 一旦联网即
  `EXCEPTION_ILLEGAL_INSTRUCTION` 崩溃（pip/winget 不受影响），故刻意走离线路径。

## 安全

- **命令白名单**：`config.json` 的 `wechat.allowed_commands` 是**记账族**命令白名单（`agent_core.ALLOWED_COMMANDS` 读取，config 缺失才回退内置集）；改它只影响记账族。备忘(note-*)/工作表(sheet-*)/图表(chart-render)/日程(cal-*)/文档(doc-* 除 doc-remove)/备份(backup-now/status/verify)/联网(web-*/any-*) 是 Agent 核心能力，`agent_core` 在装载时**恒定并入** `ALLOWED_COMMANDS`，不受本白名单增删影响。成员增删、doc-remove、backup-restore/reorg 等敏感命令既不在白名单也不并入 → Agent 调不到，仅限本机。
- **磁盘布局**：所有数据落盘位置经 `Agent_Runtime/paths.py`（单一事实来源）。`config.json` `data_root`(默认 data)+`family_dir_name`(默认 Family) 定根。家庭共享在 `data/Family/`（ledger.db 财务、documents.db 文档+成员资料、receipts/、documents/）；成员私有在 `data/<成员>/`（notes/、schedule/、tasks/、inbox/、pdf_edits/）。来图先存发送者 `data/<成员>/inbox/`，分类后搬到对应位置。`agent_core.RECEIPTS_DIR`/`DOCUMENTS_DIR` 由 `paths` 计算，不硬编码。
- **成员注册表**：`data/members.json`（git 不跟踪 — 姓名/频道 id 属隐私）只在本机用 `Expense_Tracker/cli.py member-add/list/remove` 管理（成员命令挂在记账 CLI 上，非本目录；不在命令白名单内，Agent 调不到）。未注册频道 id 一律静默丢弃；写入类账目的归属由 `agent_core` 注入解析出的成员名，LLM 给的 member 一律剥离（防冒名）。
- **外部内容围栏**：非本地来源文本经 `tool_runtime.fence` 才进 LLM——manifest `UNTRUSTED_TOOLS` 的工具结果、来件 OCR、引用消息、日程注入块的数据行。软防线：降低注入命中率，不限制损害；备忘/工作表/成员资料（本地库）未套。
- **凭据本地化**：所有频道凭据（微信扫码态、Telegram token）只存本地，不外传。
- Telegram token 走环境变量，不写进仓库。

## 环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `DEEPSEEK_API_KEY` | Agent LLM（所有频道共用） | ✅ |
| `DEEPSEEK_BASE_URL` | LLM 自定义端点（默认官方） | ❌ |
| `DEEPSEEK_MODEL` | Agent LLM 模型启动默认（默认 `deepseek-v4-flash`；用户可用 `/model` 运行时覆盖） | ❌ |
| `DEEPSEEK_REASONING_EFFORT` | 推理档启动默认，默认 `max`；可设 `high` 降档（用户可用 `/effort` 运行时覆盖） | ❌ |
| `TELEGRAM_BOT_TOKEN` | Telegram 频道 | Telegram 时必需 |
| `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY` | 图片 OCR（见 [OCR Skill](../OCR/SKILL.md)） | 收图片时 |
| `GDRIVE_CLIENT_ID` / `GDRIVE_CLIENT_SECRET` / `GDRIVE_REFRESH_TOKEN` | 云盘备份（`backup_tick` 在传输层轮询里跑，见 [Remote Backup](../Remote_Backup/SKILL.md)） | backup.enabled 时 |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN` | 成员邮箱（前缀取自 members.json `mail.cred_prefix`，见 [Mail Keeper](../Mail_Keeper/SKILL.md)） | 该成员有 mail 块时 |
| `DATA_ROOT` | 数据根目录覆盖（优先于 config `data_root`；测试隔离用，见 `paths.py`） | ❌ |
| `KNOWKING_DIR` | KnowKing 项目根覆盖（优先于 config `knowking.project_dir`） | ❌ |

### 运行时切换（/model /effort）

用户随时可发 `/model flash|pro|reset`、`/effort low|medium|high|max|reset`（不带参数查当前值，
含来源：个人覆盖/环境变量/默认）。每用户覆盖存 `data/.state/.llm_overrides.json`（不入备份），
Agent 启动时读入、切换时合并写回；消息路径不读文件。优先级：个人覆盖 > 环境变量 > 默认。
每轮 system 注入当前生效值（`_llm_status_note`），Agent 可直接回答"你在用什么模型/推理档"。

## 依赖

- 微信：`pip install "weixin-ilink[qr]"`
- Telegram：零外部包（仅标准库 urllib）
- Agent 核心：零外部包（urllib 调 DeepSeek）
- 懂王桥（可选）：需 `uv` 在 PATH + KnowKing 项目就位（依赖都在其自身 venv/.env，本项目零新增包）

## 相关

- 业务逻辑（记账/查账/汇率）见 [Expense Tracker](../Expense_Tracker/SKILL.md)
- 票据图片识别见 [OCR](../OCR/SKILL.md)
