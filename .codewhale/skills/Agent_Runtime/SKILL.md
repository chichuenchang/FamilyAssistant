# Agent Runtime

> Agent 运行时 skill：频道无关的 `Agent` 大脑 + 各远程频道传输层（微信、Telegram、未来其他）。让用户从手机上通过各类聊天软件远程操控电脑上的 Family Assistant。核心思想：**一个频道无关的 Agent，多个轻量传输层**。

## 概述

本 skill 自带全部代码，位于本目录 `.codewhale/skills/Agent_Runtime/`：

```
.codewhale/skills/Agent_Runtime/
├── SKILL.md          ← 本文件
├── agent_core.py     ← 频道无关 Agent（共用大脑）
├── members.py        ← 成员注册表（频道 id → 成员名；存 git 忽略的 data/members.json）
├── wechat_ilink.py   ← 微信传输层
└── telegram_bot.py   ← Telegram 传输层
```

所有远程频道共用同一个大脑 —— 本目录 `agent_core.py` 里的 `Agent`。无论消息从微信还是 Telegram 进来，Agent 行为、指令、工具完全一致。频道只负责"收消息 → 转交 Agent → 回消息"，不含任何业务逻辑。业务逻辑（记账/查账）在 `.codewhale/skills/Expense_Tracker/cli.py`（Agent 经 subprocess 调用），OCR 在 `.codewhale/skills/OCR/ocr.py`（Agent 经 `sys.path` import）。

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
reply = agent.handle_image(img_path, user="<频道内唯一id>", member="<成员名>")  # 图片消息
```

- `user` = 该频道内用户/会话的唯一标识（微信 `from_user`、Telegram `chat_id`）。Agent 按 `user` 隔离对话历史，互不串台。
- `member` = `members.resolve(频道, 频道id)` 解析出的成员名。**必传**：为空时 Agent 直接返回空串（防御纵深，未注册来源不碰 LLM）。
- `handle` 返回的字符串即最终回复，原样发回频道即可。
- `Agent()` 构造时从 `config.json` 提取合法值组装 system prompt（不嵌入 FamilyAssistant.md，省 token），进程内常驻复用，不要每条消息都 new。

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

# 两个 Bot 均支持 --debug：调试日志写 data/bot_debug.log（默认关）
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --debug
python .codewhale/skills/Agent_Runtime/telegram_bot.py --debug
```

微信凭据加密存于 `data/wechat_creds.json`；Telegram 去重 offset 存于 `data/.telegram_offset`。

## 新增频道

加一个频道 = 在本目录写一个薄传输层文件，调上面的契约。骨架：

```python
# .codewhale/skills/Agent_Runtime/mychannel_bot.py
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))   # 同目录 agent_core
from agent_core import Agent

def run():
    agent = Agent()                      # 一次构造，常驻复用
    for msg in receive_loop():           # ← 频道 SDK 的收消息循环
        reply = agent.handle(msg.text, user=str(msg.sender_id))
        send(msg.sender_id, reply)       # ← 频道 SDK 的发消息

if __name__ == "__main__":
    run()
```

要点：
- 每条消息先过成员闸门：`members.resolve(频道, 频道id)` 返回 None → 静默丢弃（不回复、不进 LLM）。
- 用频道内唯一 id 作 `user`（隔离对话历史），解析出的成员名作 `member` 传给 `agent.handle(text, user, member)` / `agent.handle_image(path, user, member)`。
- 图片消息：先存到按月子目录 `receipts/YYYY-MM/`（用 `agent_core.receipt_month_dir()`），再调 `agent.handle_image(path, user)`。
- 长回复需分段的频道（如 Telegram 4096 字限制）自行在传输层切分（见 `telegram_bot.py:send_message`）。
- 不在传输层写任何记账/查账逻辑 —— 全部交给 Agent。

## 安全

- **命令白名单**：来自 `config.json` 的 `wechat.allowed_commands`（`agent_core.ALLOWED_COMMANDS` 读取，config 缺失才回退内置集）。Agent 只能调白名单内的 CLI 子命令，其余一律拒绝。增删命令改 `config.json` 即可。
- **票据目录**：`agent_core.RECEIPTS_DIR` 来自 `config.json` `receipts_dir`，不在代码里硬编码。
- **成员注册表**：`data/members.json`（git 不跟踪 — 姓名/频道 id 属隐私）只在本机用 `cli.py member-add/list/remove` 管理（不在命令白名单内，Agent 调不到）。未注册频道 id 一律静默丢弃；写入类账目的归属由 `agent_core` 注入解析出的成员名，LLM 给的 member 一律剥离（防冒名）。
- **凭据本地化**：所有频道凭据（微信扫码态、Telegram token）只存本地，不外传。
- Telegram token 走环境变量，不写进仓库。

## 环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `DEEPSEEK_API_KEY` | Agent LLM（所有频道共用） | ✅ |
| `DEEPSEEK_BASE_URL` | LLM 自定义端点（默认官方） | ❌ |
| `TELEGRAM_BOT_TOKEN` | Telegram 频道 | Telegram 时必需 |
| `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY` | 图片 OCR（见 [OCR Skill](../OCR/SKILL.md)） | 收图片时 |
| `GDRIVE_CLIENT_ID` / `GDRIVE_CLIENT_SECRET` / `GDRIVE_REFRESH_TOKEN` | 云盘备份（`backup_tick` 在传输层轮询里跑，见 [Remote Backup](../Remote_Backup/SKILL.md)） | backup.enabled 时 |

## 依赖

- 微信：`pip install "weixin-ilink[qr]"`
- Telegram：零外部包（仅标准库 urllib）
- Agent 核心：零外部包（urllib 调 DeepSeek）

## 相关

- 业务逻辑（记账/查账/汇率）见 [Expense Tracker](../Expense_Tracker/SKILL.md)
- 票据图片识别见 [OCR](../OCR/SKILL.md)
