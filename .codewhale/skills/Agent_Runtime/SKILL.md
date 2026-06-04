# Agent Runtime

> Agent 运行时 skill：频道无关的 `Agent` 大脑 + 各远程频道传输层（微信、Telegram、未来其他）。让用户从手机上通过各类聊天软件远程操控电脑上的 Family Assistant。核心思想：**一个频道无关的 Agent，多个轻量传输层**。

## 概述

本 skill 自带全部代码，位于本目录 `.codewhale/skills/Agent_Runtime/`：

```
.codewhale/skills/Agent_Runtime/
├── SKILL.md          ← 本文件
├── agent_core.py     ← 频道无关 Agent（共用大脑）
├── wechat_ilink.py   ← 微信传输层
└── telegram_bot.py   ← Telegram 传输层
```

所有远程频道共用同一个大脑 —— 本目录 `agent_core.py` 里的 `Agent`。无论消息从微信还是 Telegram 进来，Agent 行为、指令、工具完全一致。频道只负责"收消息 → 转交 Agent → 回消息"，不含任何业务逻辑。业务逻辑（记账/查账）仍在 `scripts/cli.py`，OCR 在 `scripts/ocr.py`，Agent 经路径回调它们。

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
reply = agent.handle(text, user="<频道内唯一id>")        # 文字消息
reply = agent.handle_image(img_path, user="<频道内唯一id>")  # 图片消息
```

- `user` = 该频道内用户/会话的唯一标识（微信 `from_user`、Telegram `chat_id`）。Agent 按 `user` 隔离对话历史，互不串台。
- `handle` 返回的字符串即最终回复，原样发回频道即可。
- `Agent()` 构造时加载 `FamilyAssistant.md` + `config.json` 组装 system prompt，进程内常驻复用，不要每条消息都 new。

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
```

微信凭据加密存于 `data/wechat_creds.json`；Telegram 去重 offset 存于 `data/.telegram_offset`。

## 新增频道

加一个频道 = 在本目录写一个薄传输层文件，调上面的契约。骨架：

```python
# .codewhale/skills/Agent_Runtime/mychannel_bot.py
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]   # 向上 3 级到项目根
sys.path.insert(0, str(HERE))   # 同目录 agent_core
sys.path.insert(0, str(ROOT))   # scripts.ocr / cli（经 agent_core）
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
- 用频道内唯一 id 作 `user`，保证多用户历史隔离。
- 图片消息：先存到 `receipts/inbox/`，再调 `agent.handle_image(path, user)`。
- 长回复需分段的频道（如 Telegram 4096 字限制）自行在传输层切分（见 `telegram_bot.py:send_message`）。
- 不在传输层写任何记账/查账逻辑 —— 全部交给 Agent。

## 安全

- **命令白名单**：Agent 只能调 `agent_core.py` `_run_cli` 里 `allowed` 集合内的 CLI 子命令（add/list/summary/monthly/delete/deposit-*/tax-*/fx-*）。其余一律拒绝。
- **凭据本地化**：所有频道凭据（微信扫码态、Telegram token）只存本地，不外传。
- Telegram token 走环境变量，不写进仓库。

## 环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `DEEPSEEK_API_KEY` | Agent LLM（所有频道共用） | ✅ |
| `DEEPSEEK_BASE_URL` | LLM 自定义端点（默认官方） | ❌ |
| `TELEGRAM_BOT_TOKEN` | Telegram 频道 | Telegram 时必需 |
| `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY` | 图片 OCR（见 [OCR Skill](../OCR/SKILL.md)） | 收图片时 |

## 依赖

- 微信：`pip install "weixin-ilink[qr]"`
- Telegram：零外部包（仅标准库 urllib）
- Agent 核心：零外部包（urllib 调 DeepSeek）

## 相关

- 业务逻辑（记账/查账/汇率）见 [Expense Tracker](../Expense_Tracker/SKILL.md)
- 票据图片识别见 [OCR](../OCR/SKILL.md)
- 飞书 `scripts/feishu_inbox.py` 是另一种形态：只拉取群里的票据图片到 `receipts/inbox/`，不做实时对话，不走本契约。
