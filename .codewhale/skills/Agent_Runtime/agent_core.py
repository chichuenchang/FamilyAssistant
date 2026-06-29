"""
Agent Core — 频道无关的全量上下文智能助手。

所有远程频道（微信、Telegram、未来其他）共用这一个 Agent，行为一致。
与 CodeWhale 工作方式一致：读取整个项目文档，理解意图，自主决策。
告别关键词路由，每条消息都带完整项目上下文调 DeepSeek API。

模式:
    启动时加载项目文档 → 构建 system prompt
    每条消息 → system + 对话历史 + 用户消息 → DeepSeek（function calling）
    LLM 自主选择工具 → 执行 → LLM 生成自然语言回复

频道接入契约（详见 .codewhale/skills/Agent_Runtime/SKILL.md）:
    agent.handle(text, user, member)        # 文字消息
    agent.handle_image(path, user, member)  # 图片消息
    user = 频道内唯一 id（隔离各用户对话历史）
    member = members.resolve 解析出的成员名；为空直接返回空串（未注册来源不碰 LLM）

依赖:
    DEEPSEEK_API_KEY

用法:
    from agent_core import Agent  # 同目录传输层直接 import
    agent = Agent()
    reply = agent.handle("这个月花了多少", user="wx_xxx")
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 本文件位于 .codewhale/skills/Agent_Runtime/ ，向上 3 级到项目根
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "OCR"))  # OCR skill 的 ocr.py
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 members
# 注：CLI 经 subprocess 调用（见 _run_cli），无需加入 sys.path

import members as _members_registry
import paths as _paths

_log = logging.getLogger("familyassist.agent")


# ── config.json（值的单一事实来源；不在代码里重复硬编码） ──────

def _load_config_dict() -> dict:
    """解析项目根 config.json；缺失/损坏返回 {}（用下方回退）。"""
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_CONFIG = _load_config_dict()

# 票据/文档目录经 paths（单一事实来源）：家庭共享。
RECEIPTS_DIR = _paths.family_dir() / "receipts"
DOCUMENTS_DIR = _paths.family_dir() / "documents"


def receipt_month_dir(dt: date | None = None) -> Path:
    """票据按月分子目录：Family/receipts/YYYY-MM/，不存在则创建。"""
    return _paths.family_receipts_dir(dt)


def member_inbox_dir(member: str, dt: date | None = None) -> Path:
    """来图暂存目录（按发送成员）：data/<成员>/inbox/YYYY-MM/，不存在则创建。

    传输层把来图先存发送者的 inbox（成员私有）；分类后：备忘图→该成员 notes，
    票据→Family/receipts，文档→Family/documents。避免私有图先落到家庭共享目录。"""
    return _paths.member_inbox_dir(member, dt)


# ── 调试日志（各 Bot 共用；默认开，--no-debug 关） ─────────────────

def setup_logging(debug: bool = True) -> logging.Logger:
    """配置 "familyassist" 日志器，各传输层（telegram/wechat）调一次即可。

    项目规范：所有 Bot 默认开调试日志（debug=True）。新增 Bot 直接 setup_logging() 即继承。
    debug=True（默认）：DEBUG 全量，同时写 stderr 和 data/bot_debug.log（含完整 traceback），
                供排查 OCR/记账/工具调用链路。
    debug=False（--no-debug）：仅 WARNING 及以上，安静运行。
    子日志器（familyassist.telegram 等）自动继承本配置。
    """
    logger = logging.getLogger("familyassist")
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    if logger.handlers:  # 幂等：重复调用不叠加 handler
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if debug:
        log_dir = ROOT / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "bot_debug.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.debug("调试日志已开启 → %s", log_dir / "bot_debug.log")
    return logger

# CLI 命令白名单（config.json wechat.allowed_commands，缺失回退内置集）
_FALLBACK_ALLOWED = {
    "add", "list", "summary", "monthly", "delete",
    "deposit-add", "deposit-list", "tax-add", "tax-list",
    "fx-get", "fx-set",
    "transfer-add", "transfer-list",
}
ALLOWED_COMMANDS = set(_CONFIG.get("wechat", {}).get("allowed_commands") or _FALLBACK_ALLOWED)

# 子命令 → 所属 skill（未列出的归 Expense_Tracker）
_DOC_COMMANDS = {"doc-add", "doc-list", "doc-show", "doc-due",
                 "doc-update", "doc-ack", "doc-remove"}
_BACKUP_COMMANDS = {"backup-now", "backup-status", "backup-verify",
                    "backup-restore", "backup-reorg"}
_NOTE_COMMANDS = {"note-add", "note-list", "note-search", "note-delete", "note-pin"}
_SHEET_COMMANDS = {"sheet-create", "sheet-list", "sheet-show", "sheet-set",
                   "sheet-unset", "sheet-row-add", "sheet-row-edit",
                   "sheet-row-delete", "sheet-rename", "sheet-pin", "sheet-delete"}
_CHART_COMMANDS = {"chart-render"}
_DOC_FILE_COMMANDS = {"doc-file"}
_CAL_COMMANDS = {"cal-add", "cal-list", "cal-done", "cal-delete",
                 "cal-sync", "cal-status"}
_REACH_COMMANDS = {"web-search", "web-read", "yt-summary"}
_ANYSEARCH_COMMANDS = {"any-search", "any-extract", "any-subdomains"}

# 备忘/日程/联网命令始终允许（Agent 核心能力，不随 wechat 白名单配置开关）
ALLOWED_COMMANDS |= _NOTE_COMMANDS
ALLOWED_COMMANDS |= _SHEET_COMMANDS
ALLOWED_COMMANDS |= _CHART_COMMANDS
ALLOWED_COMMANDS |= _DOC_FILE_COMMANDS
ALLOWED_COMMANDS |= _CAL_COMMANDS
ALLOWED_COMMANDS |= _REACH_COMMANDS
ALLOWED_COMMANDS |= _ANYSEARCH_COMMANDS


def _cli_path(cmd: str) -> Path:
    """子命令 → 所属 skill 的 CLI 路径。"""
    if cmd in _DOC_COMMANDS or cmd in _DOC_FILE_COMMANDS:
        skill = "Document_Keeper"
    elif cmd in _BACKUP_COMMANDS:
        skill = "Remote_Backup"
    elif cmd in _NOTE_COMMANDS or cmd in _SHEET_COMMANDS or cmd in _CHART_COMMANDS:
        skill = "Note_Keeper"
    elif cmd in _CAL_COMMANDS:
        skill = "Calendar_Keeper"
    elif cmd in _REACH_COMMANDS:
        skill = "Web_Reach"
    elif cmd in _ANYSEARCH_COMMANDS:
        skill = "Any_Search"
    else:
        skill = "Expense_Tracker"
    return ROOT / ".codewhale" / "skills" / skill / "cli.py"


# ── system prompt ───────────────────────────────────────────

def _build_system_prompt() -> str:
    """组装 system prompt：身份 + config 提取的事实 + 行为准则。

    工具定义走 API tools 参数。FamilyAssistant.md 是开发文档（文件路径、
    CLI 示例），对运行时对话无用，不进 prompt——省每条消息的 token。
    分类/币种从 config.json 提取为紧凑列表，不嵌原始 JSON。
    """
    today = date.today()
    tx_types = "/".join(_TX_TYPES)
    currencies = "/".join(_CURRENCIES)
    doc_types = "/".join(_DOC_TYPES)

    # 家庭成员 + 别名/法定名（data/members.json；空注册表则整段省略）
    members_cfg = _members_registry.load_members()
    member_block = ""
    if isinstance(members_cfg, dict) and members_cfg:
        rows = []
        for n, b in members_cfg.items():
            als = [str(a) for a in (b.get("aliases") or [])] if isinstance(b, dict) else []
            rows.append(f"- {n}" + (f"（别名/法定名: {'、'.join(als)}）" if als else ""))
        member_block = (
            "\n\n## 家庭成员\n" + "\n".join(rows) +
            "\n- 票据/合同/证件等文档或对话里出现上述别名/法定名时，视为对应成员"
            "（用于文档标题、按成员查询过滤、理解\"这是谁的\"）。"
            "\n- 写入类操作的归属永远是发消息的成员（代码强制），别名不改变归属。")

    return f"""你是 Family Assistant，一个运行在微信/Telegram 等远程频道里的个人/家庭 AI 助手。

## 你是谁
- 你可以帮用户记账、查账、汇总开销、管理定期存款、查询汇率、OCR 票据、记私人备忘等
- 你友好、简洁、直接——回复不用太长{member_block}

## 记账合法值（来自配置，必须从中选）
- 交易类型: {tx_types}
- 币种: {currencies}（默认基准 {_BASE_CUR}）
- 各类型分类: {_CATS_DESC}

## 文档管理（家庭重要文档归档与到期提醒）
- 文档类型: {doc_types}
- 用户发来 合同/保单/证件 等重要文档，或说"存一下这个文件"→ add_document（尽量带 expiry 到期日和 action-note 到期动作）
- 用户问"租约什么时候到期""我们有哪些保险""找一下XX保单"→ list_documents / show_document
- 用户问"有什么要到期的""最近有什么要办的"→ due_documents
- 用户说"续约了""换新证了"→ update_document 改到期日；旧文档另存时把旧的 status 改 superseded
- 用户说"知道了""别再提醒"→ ack_document

## 日程与待办（与远程日历静默同步，按成员私有）
- 未来{_CAL_LOOKAHEAD}天**你（当前成员）的**日程/待办会自动注入上下文（每人只见自己的，活动与待办分库）
- **不要主动播报日程**：仅当用户问到（"接下来有什么安排""待办清单"）或与当前话题直接相关时才提及
- **加日程前先查重**：调 add_event/add_task 前，先比对已注入的未来日程（窗口外可先 list_schedule）。
  判定重复看"同一件事"——同一天 + 同一活动，标题措辞不同也算（如"游泳烧烤"vs"Hotdog Roast & Swim"）。
  对每条疑似重复按下面三种情况处理，**任何一种都必须明确告诉用户你做了什么，绝不静默**：
  1) 已有的更详细（有时间/地点/备注而新的没有）→ 丢弃新的，不 add；告诉用户"已存在更详细的同名日程，未重复添加"。
  2) 新的更详细（补了时间/地点/备注等有用信息）→ 先 remove_schedule_item 删旧（会同步删远端），再 add_event 加新的；
     告诉用户"发现重复，已用信息更全的版本替换旧的"。
  3) 新旧基本一致、新的没补任何有用信息 → 丢弃新的；告诉用户"已存在相同日程，未重复添加"。
  拿不准是不是同一件事，或替换会丢用户可能在意的东西时 → 先问用户，别擅自删。
  无重复 → 正常添加。报告里点明每条的处理（已加/已跳过重复/已替换），让用户能纠正或补特殊要求。
- 用户说"安排/约了/X号要做Y/加个日程/活动"→ add_event（活动必须有日期；有具体时间则给 start/end）
- 用户说"要做X/记个待办/任务"→ add_task（有截止日给 due）
- **必须先调工具再回复**：用户的话只要听起来是要加/记一件事——给了日期、时间、"几点去X""X号做Y""帮我加/记/设个提醒/约了/安排"等——你**这一轮就必须立即调用对应写工具**（活动→add_event，待办→add_task），拿到返回结果后再回复。绝不能只用自然语言说"已加上/搞定/记好了"而不调工具；没调工具=这件事根本没做。拿不准是活动还是待办、或缺日期时间，就先调最合适的工具用默认值，或一句话问清后再调——但不要假装已完成
- **calendar_status 不是凭据**：它只反映已入队的同步条数，不能据此声称某条新日程已存在或已同步——某日程到底有没有，以你 add_event 的返回为准
- **归属默认发送者**：活动/待办默认进**你（当前成员）**的日历/待办，即使内容关于别的家庭成员
  （如你发 Robin 的活动，默认进你的日历）；从图片建的，原始图也存你名下。仅当用户**明确**说
  "加到 X 的日历/记到 X 的待办"时，才用 for-member 路由到该成员。备忘不跨成员（按成员私有）。
- 用户问"接下来有什么安排/这周有什么事/我的待办"→ 按上下文回答或调 list_schedule
- 用户说"做完了/办完了"→ complete_task；"取消/不去了"→ remove_schedule_item
- 用户说"刷新日历/同步日历"→ sync_calendar；问同步状态 → calendar_status
- 新增/完成/取消会自动同步到远程日历；"待同步"= 暂未推送会自动重试，无需向用户解释技术细节

## 数据备份（可选功能）
- 用户问"备份了吗""上次备份什么时候"→ backup_status
- 用户说"立刻备份""把数据同步到云盘"→ backup_now
- 用户问"云端和本地一致吗"→ backup_verify
- backup_status 显示未启用/未配置时：告知备份是可选功能，需要在电脑上按
  Remote_Backup/SKILL.md 完成 Google Drive 授权并启用；不要反复推销
- 数据恢复（backup-restore）只能在电脑上手动执行，你调不到

## 回复风格
- 简洁、易读是第一优先级：先给结论/结果，能一句话说清就不写三句
- 永远不要向用户刷屏式罗列命令、操作步骤或功能菜单；一次回复聚焦当前这件事
- 不要主动列"你可以让我做X/Y/Z"的能力清单，除非用户明确问"你能干什么"
- 列表只在确实有多条并列信息时用（如多笔账单汇总），且每条尽量一行

## 行为准则
- 用户说"记账""花了""买了"→ 提取金额/分类/日期 → 调 add_transaction
- 用户说"查账""这个月花了多少"→ 调 list_transactions 或 get_summary
- 用户说"存了定期""买了理财"→ add_deposit；"我有哪些定期"→ list_deposits
- 用户说"报税""今年报了多少税"→ add_tax / list_tax
- 用户说"换汇""把X块换成美元""转到X银行存定期""转钱"→ add_transfer（尽量问全：源账户/金额/币种→目标金额/币种/银行/账号/类型/日期）
- 用户问"这笔定期/活期哪来的""资金来源""查某笔存款来源"→ list_transfers（按 to-deposit-id 或 trace 关键词）
- 用户说"汇率"→ get_fx_rate；"美元汇率改成X"→ set_fx_rate
- 用户说"记一下""帮我记住""备忘"（非记账类杂项信息）→ save_note；重要长期信息建议 pinned
- 用户问"我记过什么""XX是什么来着""车位/wifi密码是多少"→ search_notes 或 list_notes
- 工作表（长期结构化跟踪）：仅当用户明确说"建个表/做个 worksheet/长期记录这些字段/这些流水"时才用 create_worksheet；普通"记一下"仍用 save_note，不要升级成工作表。kv=事实清单（房贷利率/保单号），table=流水（血压/体重/读数打卡）。更新已存表用 set_worksheet_field（kv）或 add_worksheet_row/edit_worksheet_row（table）；查全表用 show_worksheet
- 用户要"图/可视化/趋势/图表/show me the chart"→ 先确认数据在哪张工作表（必要时 show_worksheet 取全），抽出对应数字，调 visualize_data 画图；图会自动发给用户，你只需简短说明
- 用户说"把我的租约/保单发给我""发我那个文件/那张图"→ send_document（先 list/show 拿 id）或 send_file（data 内相对路径）；文件会自动发给用户
- 备忘按成员私有：只能看到当前用户自己的备忘，这是系统强制的，无需向用户解释
- 用户问"最新新闻/外面在发生什么/帮我查一下X" → 优先 anysearch_search（更准，可选 domain 垂直搜索：finance/health/academic/travel/code 等，先 anysearch_subdomains 发现子域）；web_search 为备选。发链接让看/总结文章 → anysearch_extract（备选 web_read）；发 YouTube 链接让总结 → youtube_summarize。工具返回抓取到的原文，你据此用中文总结报告；抓取失败就如实说没查到，别编造
- 用户闲聊/问候 → 直接友好回复，不用调工具
- 需要精确信息时（金额、日期）才调工具，闲聊不调
- 工具执行后会返回结果，你基于结果用自然语言回复
- 如果用户没有指定日期，默认今天 {today}
- 回复中不要暴露技术细节（如 SQLite、CLI 等）
"""


# ── CLI 执行器 ──────────────────────────────────────────────

def _run_cli(cmd: str, args: dict[str, Any] = None) -> str:
    """执行 CLI 命令并返回 stdout。"""
    if cmd not in ALLOWED_COMMANDS:
        return f"[错误] 命令不允许: {cmd}"

    cli_args = [cmd]
    if args:
        for k, v in args.items():
            flag = k if k.startswith("-") else f"--{k}"  # 容忍裸键（type→--type）
            if v is True:
                cli_args.append(flag)                     # 布尔开关，无值（如 --force）
            elif v is False or v is None or v == "":
                continue                                  # 未设置则跳过
            else:
                cli_args.append(flag)
                cli_args.append(str(v))

    cli_path = _cli_path(cmd)
    try:
        result = subprocess.run(
            [sys.executable, str(cli_path)] + cli_args,
            capture_output=True, text=True, cwd=str(ROOT),
            timeout=30, encoding="utf-8", errors="replace",
        )
        return result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "[错误] 超时"
    except Exception as e:
        return f"[错误] {e}"


IMG_SENTINEL = "\x01IMG:"
DOC_SENTINEL = "\x01DOC:"


def split_reply(reply: str) -> tuple[str, list[str], list[str]]:
    """剥离 \\x01IMG:/\\x01DOC: 哨兵行，返回 (可见文本, [图片], [文档]) 三元组。"""
    imgs, docs, keep = [], [], []
    for line in (reply or "").split("\n"):
        if line.startswith(IMG_SENTINEL):
            p = line[len(IMG_SENTINEL):].strip()
            if p:
                imgs.append(p)
        elif line.startswith(DOC_SENTINEL):
            p = line[len(DOC_SENTINEL):].strip()
            if p:
                docs.append(p)
        else:
            keep.append(line)
    return "\n".join(keep).strip(), imgs, docs


# ── 工具实现 ────────────────────────────────────────────────

def _tool_add_transaction(args): return _run_cli("add", args)
def _tool_list_transactions(args): return _run_cli("list", args)
def _tool_get_summary(args): return _run_cli("summary", args)
def _tool_get_monthly(args): return _run_cli("monthly", args)
def _tool_list_deposits(args): return _run_cli("deposit-list", args)
def _tool_add_deposit(args): return _run_cli("deposit-add", args)
def _tool_get_fx_rate(args): return _run_cli("fx-get", args)
def _tool_set_fx_rate(args): return _run_cli("fx-set", args)
def _tool_add_tax(args): return _run_cli("tax-add", args)
def _tool_list_tax(args): return _run_cli("tax-list", args)
def _tool_add_transfer(args): return _run_cli("transfer-add", args)
def _tool_list_transfers(args): return _run_cli("transfer-list", args)
def _tool_delete_transaction(args): return _run_cli("delete", args)
def _tool_add_document(args): return _run_cli("doc-add", args)
def _tool_list_documents(args): return _run_cli("doc-list", args)
def _tool_show_document(args): return _run_cli("doc-show", args)
def _tool_due_documents(args): return _run_cli("doc-due", args)
def _tool_update_document(args): return _run_cli("doc-update", args)
def _tool_ack_document(args): return _run_cli("doc-ack", args)
def _tool_backup_now(args): return _run_cli("backup-now", args)
def _tool_backup_status(args): return _run_cli("backup-status", args)
def _tool_backup_verify(args): return _run_cli("backup-verify", args)
def _relocate_image(src: str, member: str, domain: str) -> str:
    """来图从暂存（成员 inbox，data_root 内）搬到该成员某域 YYYY-MM/，返回 data 相对路径。

    domain ∈ notes/schedule/tasks。传输层把来图先存 data/<成员>/inbox/，分类后搬到对应域。
    代码确定性执行（不交给 LLM 决定）。失败保留原路径，绝不丢图。
    仅处理 data_root 内的文件 + 已知成员；否则原样返回。"""
    try:
        if not member:
            return src
        p = Path(src)
        resolved = (p if p.is_absolute() else ROOT / p).resolve()
        droot = _paths.data_root().resolve()
        if not (resolved.exists() and resolved.is_relative_to(droot)):
            return src
        dest_dir = _paths.member_domain_image_dir(member, domain)
        dest = dest_dir / resolved.name
        i = 1
        while dest.exists():
            dest = dest_dir / f"{resolved.stem}_{i}{resolved.suffix}"
            i += 1
        resolved.rename(dest)
        _log.debug("来图已移动 %s → %s", resolved, dest)
        return _paths.to_rel(dest)
    except Exception:
        _log.exception("来图移动失败（保留原路径）")
        return src


def _relocate_note_image(src: str, member: str) -> str:
    """备忘图片搬到成员 notes/（_relocate_image 的 notes 域包装）。"""
    return _relocate_image(src, member, "notes")


def _tool_save_note(args):
    src = args.get("source-image", "")
    if src:
        member = args.get("member", "") or args.get("--member", "")
        args = {**args, "source-image": _relocate_note_image(src, member)}
    return _run_cli("note-add", args)
def _tool_list_notes(args): return _run_cli("note-list", args)
def _tool_search_notes(args): return _run_cli("note-search", args)
def _tool_delete_note(args): return _run_cli("note-delete", args)
def _tool_pin_note(args): return _run_cli("note-pin", args)


def _tool_create_worksheet(args): return _run_cli("sheet-create", args)
def _tool_list_worksheets(args): return _run_cli("sheet-list", args)
def _tool_show_worksheet(args): return _run_cli("sheet-show", args)
def _tool_set_worksheet_field(args): return _run_cli("sheet-set", args)
def _tool_unset_worksheet_field(args): return _run_cli("sheet-unset", args)
def _tool_delete_worksheet_row(args): return _run_cli("sheet-row-delete", args)
def _tool_rename_worksheet(args): return _run_cli("sheet-rename", args)
def _tool_pin_worksheet(args): return _run_cli("sheet-pin", args)
def _tool_delete_worksheet(args): return _run_cli("sheet-delete", args)


def _tool_add_worksheet_row(args):
    args = dict(args)
    data = args.pop("data", None)
    if isinstance(data, (dict, list)):
        args["data"] = json.dumps(data, ensure_ascii=False)
    elif data is not None:
        args["data"] = str(data)
    return _run_cli("sheet-row-add", args)


def _tool_edit_worksheet_row(args):
    args = dict(args)
    data = args.pop("data", None)
    if isinstance(data, (dict, list)):
        args["data"] = json.dumps(data, ensure_ascii=False)
    elif data is not None:
        args["data"] = str(data)
    return _run_cli("sheet-row-edit", args)


def _tool_visualize_data(args):
    args = dict(args)
    spec = args.pop("spec", None)
    if isinstance(spec, (dict, list)):
        args["spec"] = json.dumps(spec, ensure_ascii=False)
    elif spec is not None:
        args["spec"] = str(spec)
    return _run_cli("chart-render", args)


def _resolve_sendable(path: str, member: str) -> str | None:
    """送文件闸门：路径须存在、是文件、在 data_root 内，且属家庭共享或本成员目录。
    通过 → 返回 data 相对路径；否则 None。"""
    try:
        p = Path(path)
        ap = (p if p.is_absolute() else _paths.resolve_rel(str(path))).resolve()
        root = _paths.data_root().resolve()
        if not (ap.exists() and ap.is_file() and ap.is_relative_to(root)):
            return None
        allowed = [_paths.family_dir().resolve()]
        if member:
            allowed.append(_paths.member_dir(member).resolve())
        if not any(ap.is_relative_to(a) for a in allowed):
            return None
        return _paths.to_rel(ap)
    except (ValueError, OSError):
        return None


def _tool_send_document(args):
    return _run_cli("doc-file", {"id": args.get("id")})


def _tool_send_file(args):
    member = args.get("member", "")
    rel = _resolve_sendable(args.get("path", ""), member)
    return rel if rel else "[错误] 路径不允许或文件不存在"


def _target_member(args: dict, sender: str) -> str:
    """日程/待办的目标成员：显式 for-member（须已登记）覆盖，否则归发送者。

    默认归发送者（即使内容关于别人），仅当用户明确指定别的成员时路由过去；
    未登记的 for-member 退回发送者（不凭空建目录）。备忘不走此路（按成员私有）。
    """
    target = (args.pop("for-member", "") or "").strip() or sender
    if target != sender and target not in _members_registry.member_names():
        return sender
    return target


def _tool_add_event(args):
    args = dict(args)
    sender = args.get("member", "")
    target = _target_member(args, sender)
    src = args.get("source-image", "")
    if src:                                   # 原始图始终归发送者名下
        args["source-image"] = _relocate_image(src, sender, "schedule")
    args["member"] = target                   # 活动入目标成员日历
    return _run_cli("cal-add", {**args, "kind": "event"})


def _tool_add_task(args):
    # LLM 用 due 表达截止日，CLI 统一收 --date
    args = dict(args)
    due = args.pop("due", "")
    if due:
        args["date"] = due
    sender = args.get("member", "")
    target = _target_member(args, sender)
    src = args.get("source-image", "")
    if src:
        args["source-image"] = _relocate_image(src, sender, "tasks")
    args["member"] = target
    return _run_cli("cal-add", {**args, "kind": "task"})


def _tool_list_schedule(args): return _run_cli("cal-list", args)
def _tool_complete_task(args): return _run_cli("cal-done", args)
def _tool_remove_schedule_item(args): return _run_cli("cal-delete", args)
def _tool_sync_calendar(args): return _run_cli("cal-sync", args)
def _tool_calendar_status(args): return _run_cli("cal-status", args)

def _tool_web_search(args): return _run_cli("web-search", args)
def _tool_web_read(args): return _run_cli("web-read", args)
def _tool_youtube_summarize(args): return _run_cli("yt-summary", args)
def _tool_anysearch_search(args): return _run_cli("any-search", args)
def _tool_anysearch_extract(args): return _run_cli("any-extract", args)
def _tool_anysearch_subdomains(args): return _run_cli("any-subdomains", args)

def _tool_ocr_image(args):
    path = args.get("path", "")
    # 安全：path 来自 LLM（间接来自用户消息），只允许数据根 data/ 内的文件
    # （票据/文档/成员 inbox/备忘图片皆在其下），防止把任意本地文件 base64 后
    # 发给腾讯云/DeepSeek（数据外泄）。
    try:
        p = Path(path)
        resolved = (p if p.is_absolute() else ROOT / p).resolve()
        allowed_root = _paths.data_root().resolve()
        if not resolved.is_relative_to(allowed_root):
            return f"[错误] 只允许识别数据目录内的图片: {allowed_root}"
    except (OSError, ValueError):
        return "[错误] 无效的图片路径"
    try:
        from ocr import ocr_extract, is_available
        if is_available():
            info = ocr_extract(str(resolved))
            return json.dumps(info, ensure_ascii=False) if info else "[未识别到文字]"
        return "[OCR 未配置]"
    except Exception as e:
        return f"[OCR 错误] {e}"


_TOOL_MAP = {
    "add_transaction": _tool_add_transaction,
    "list_transactions": _tool_list_transactions,
    "get_summary": _tool_get_summary,
    "get_monthly": _tool_get_monthly,
    "list_deposits": _tool_list_deposits,
    "add_deposit": _tool_add_deposit,
    "get_fx_rate": _tool_get_fx_rate,
    "set_fx_rate": _tool_set_fx_rate,
    "add_tax": _tool_add_tax,
    "list_tax": _tool_list_tax,
    "add_transfer": _tool_add_transfer,
    "list_transfers": _tool_list_transfers,
    "delete_transaction": _tool_delete_transaction,
    "ocr_image": _tool_ocr_image,
    "add_document": _tool_add_document,
    "list_documents": _tool_list_documents,
    "show_document": _tool_show_document,
    "due_documents": _tool_due_documents,
    "update_document": _tool_update_document,
    "ack_document": _tool_ack_document,
    "backup_now": _tool_backup_now,
    "backup_status": _tool_backup_status,
    "backup_verify": _tool_backup_verify,
    "save_note": _tool_save_note,
    "list_notes": _tool_list_notes,
    "search_notes": _tool_search_notes,
    "delete_note": _tool_delete_note,
    "pin_note": _tool_pin_note,
    "create_worksheet": _tool_create_worksheet,
    "list_worksheets": _tool_list_worksheets,
    "show_worksheet": _tool_show_worksheet,
    "set_worksheet_field": _tool_set_worksheet_field,
    "unset_worksheet_field": _tool_unset_worksheet_field,
    "add_worksheet_row": _tool_add_worksheet_row,
    "edit_worksheet_row": _tool_edit_worksheet_row,
    "delete_worksheet_row": _tool_delete_worksheet_row,
    "rename_worksheet": _tool_rename_worksheet,
    "pin_worksheet": _tool_pin_worksheet,
    "delete_worksheet": _tool_delete_worksheet,
    "visualize_data": _tool_visualize_data,
    "send_document": _tool_send_document,
    "send_file": _tool_send_file,
    "add_event": _tool_add_event,
    "add_task": _tool_add_task,
    "list_schedule": _tool_list_schedule,
    "complete_task": _tool_complete_task,
    "remove_schedule_item": _tool_remove_schedule_item,
    "sync_calendar": _tool_sync_calendar,
    "calendar_status": _tool_calendar_status,
    "web_search": _tool_web_search,
    "web_read": _tool_web_read,
    "youtube_summarize": _tool_youtube_summarize,
    "anysearch_search": _tool_anysearch_search,
    "anysearch_extract": _tool_anysearch_extract,
    "anysearch_subdomains": _tool_anysearch_subdomains,
}

# 写工具集合：归属强制由代码注入（防 LLM 冒名记到别人头上）
_MEMBER_WRITE_TOOLS = {"add_transaction", "add_deposit", "add_transfer", "add_tax",
                       "add_document", "add_event", "add_task"}

# 备忘工具全部强制注入 member（读写皆是 — 备忘按成员私有，LLM 不得跨成员读写）
_NOTE_TOOLS = {"save_note", "search_notes", "list_notes", "delete_note", "pin_note"}

# 工作表工具同样按成员私有，读写一律强制注入 member（LLM 不得跨成员读写）
_SHEET_TOOLS = {"create_worksheet", "list_worksheets", "show_worksheet",
                "set_worksheet_field", "unset_worksheet_field", "add_worksheet_row",
                "edit_worksheet_row", "delete_worksheet_row", "rename_worksheet",
                "pin_worksheet", "delete_worksheet", "visualize_data",
                "send_document", "send_file"}

# 日程工具按成员私有：完成/取消/查询/同步/状态一律强制注入发送者 member。
# 不注入则 CLI member="" → 命中空库（cal-done）或 _member_stores 抛错（cal-delete/cal-list），
# 工具形同失效；且供 member 即读他人私有日历。统一强制锁到发送者，与备忘/工作表一致。
_CAL_MEMBER_TOOLS = {"complete_task", "remove_schedule_item", "list_schedule",
                     "sync_calendar", "calendar_status"}

# 工具按产出附件分类：成功调用时 handle() 收集路径，尾部追加对应哨兵
_IMAGE_TOOLS = {"visualize_data"}
_DOC_TOOLS = {"send_document", "send_file"}


def _apply_member(tool_name: str, targs: dict, member: str) -> dict:
    """写工具：剥离 LLM 给的 member，注入解析出的成员名。读工具原样放行。
    备忘/工作表/日程工具（含读/删/完成/取消）一律强制注入，保证按成员隔离。"""
    if (tool_name in _MEMBER_WRITE_TOOLS or tool_name in _NOTE_TOOLS
            or tool_name in _SHEET_TOOLS or tool_name in _CAL_MEMBER_TOOLS):
        targs = {k: v for k, v in targs.items() if k.lstrip("-") != "member"}
        if member:
            targs["member"] = member
    return targs


# ── 工具 JSON Schema（DeepSeek function calling，OpenAI 兼容格式） ──
# 参数名与 CLI 标志一致（含连字符），_run_cli 直接转 --flag。
# 枚举值来自 config.json（单一事实来源）。

_TX_TYPES = list(_CONFIG.get("categories", {}).keys()) or [
    "expense", "income", "investment", "savings"]
_CURRENCIES = _CONFIG.get("supported_currencies") or ["USD", "CNY", "CAD"]
_BASE_CUR = _CONFIG.get("base_currency") or "USD"
_CATS_DESC = json.dumps(_CONFIG.get("categories", {}), ensure_ascii=False)
_DOC_TYPES = list(_CONFIG.get("doc_types") or ["other"])
_DOC_STATUSES = ["active", "expired", "archived", "superseded"]
_CAL_LOOKAHEAD = int((_CONFIG.get("calendar") or {}).get("lookahead_days") or 10)
_WORKSHEET_PIN_ROW_CAP = int((_CONFIG.get("notes") or {}).get("worksheet_pin_row_cap") or 80)


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props,
                       "required": required or []},
    }}


def _s(desc: str, **kw) -> dict:
    return {"type": "string", "description": desc, **kw}


def _num(desc: str) -> dict:
    return {"type": "number", "description": desc}


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


TOOL_SCHEMAS = [
    _fn("add_transaction", "记一笔账（支出/收入/投资/储蓄）", {
        "type": _s("交易类型", enum=_TX_TYPES),
        "amount": _num("金额，正数"),
        "currency": _s(f"币种，默认 {_BASE_CUR}", enum=_CURRENCIES),
        "date": _s("日期 YYYY-MM-DD"),
        "category": _s(f"分类，必须从合法分类中选: {_CATS_DESC}"),
        "desc": _s("描述，如 午餐"),
        "notes": _s("备注"),
        "force": {"type": "boolean", "description": "跳过重复检查强制写入（仅在用户确认非重复后用）"},
    }, ["type", "amount", "date"]),
    _fn("list_transactions", "查询交易流水", {
        "type": _s("交易类型", enum=_TX_TYPES),
        "category": _s("分类"),
        "currency": _s("币种", enum=_CURRENCIES),
        "start": _s("开始日期 YYYY-MM-DD"),
        "end": _s("结束日期 YYYY-MM-DD"),
        "limit": _int("最多返回条数"),
        "member": _s("按成员过滤，如只看某个家庭成员的账"),
    }),
    _fn("get_summary", "按分类汇总金额（分币种）", {
        "type": _s("交易类型，默认 expense", enum=_TX_TYPES),
        "year": _int("年份"),
        "month": _int("月份 1-12"),
        "member": _s("按成员过滤，如只看某个家庭成员的账"),
        "by-member": {"type": "boolean", "description": "按成员汇总（谁花了多少）"},
    }),
    _fn("get_monthly", "按月汇总金额（分币种）", {
        "type": _s("交易类型，默认 expense", enum=_TX_TYPES),
        "year": _int("年份"),
        "member": _s("按成员过滤，如只看某个家庭成员的账"),
    }),
    _fn("list_deposits", "查询定期存款", {
        "currency": _s("币种", enum=_CURRENCIES),
        "active": {"type": "boolean", "description": "只看未到期的"},
    }),
    _fn("add_deposit", "新增定期存款记录", {
        "amount": _num("本金"),
        "currency": _s("币种", enum=_CURRENCIES),
        "bank": _s("银行名"),
        "account": _s("账号"),
        "term": _int("期限（月）"),
        "rate": _num("年利率(%)"),
        "start-date": _s("起存日 YYYY-MM-DD"),
        "maturity": _s("到期日 YYYY-MM-DD"),
        "notes": _s("备注"),
    }, ["amount", "start-date"]),
    _fn("get_fx_rate", "查询汇率", {
        "from": _s("源币种", enum=_CURRENCIES),
        "to": _s("目标币种", enum=_CURRENCIES),
    }, ["from", "to"]),
    _fn("set_fx_rate", "设置汇率", {
        "from": _s("源币种", enum=_CURRENCIES),
        "to": _s("目标币种", enum=_CURRENCIES),
        "rate": _num("汇率：1 源币种 = rate 目标币种"),
    }, ["from", "to", "rate"]),
    _fn("add_tax", "新增报税记录", {
        "year": _int("税务年度"),
        "country": _s("国家", enum=["US", "CA"]),
        "data": _s('报税数据，JSON 字符串，如 {"total_income": 100000, "tax_paid": 20000}'),
        "filing-date": _s("申报日期 YYYY-MM-DD"),
        "notes": _s("备注"),
    }, ["year", "country"]),
    _fn("list_tax", "查询报税记录", {
        "year": _int("税务年度"),
        "country": _s("国家", enum=["US", "CA"]),
    }),
    _fn("add_transfer", "记录资金划转/换汇（溯源；目标为定期时自动建定期存款）", {
        "from-amount": _num("源金额"),
        "from-currency": _s("源币种", enum=_CURRENCIES),
        "to-amount": _num("目标金额"),
        "to-currency": _s("目标币种", enum=_CURRENCIES),
        "to-type": _s("目标账户类型：活期/定期"),
        "from-desc": _s("源账户描述，如 活期/工行"),
        "from-type": _s("源账户类型：活期/定期"),
        "from-deposit-id": _int("源若为已记录定期存款，其 id"),
        "rate": _num("换汇汇率；不填按 to/from 计算"),
        "exchange-date": _s("换汇日期 YYYY-MM-DD"),
        "to-bank": _s("目标银行"),
        "to-account": _s("目标账号"),
        "transfer-date": _s("到账/转账日期 YYYY-MM-DD"),
        "to-term": _int("目标定期期限（月）"),
        "to-rate": _num("目标定期年利率(%)"),
        "to-maturity": _s("目标定期到期日 YYYY-MM-DD"),
        "notes": _s("备注"),
    }, ["from-amount", "from-currency", "to-amount", "to-currency", "to-type"]),
    _fn("list_transfers", "查询划转记录/溯源资金来源", {
        "currency": _s("匹配源或目标币种", enum=_CURRENCIES),
        "to-bank": _s("目标银行"),
        "type": _s("匹配源或目标类型 活期/定期"),
        "start": _s("开始日期 YYYY-MM-DD"),
        "end": _s("结束日期 YYYY-MM-DD"),
        "to-deposit-id": _int("查某定期存款的资金来源"),
        "from-deposit-id": _int("查某定期存款的去向"),
        "trace": _s("模糊匹配 描述/银行/账号/备注"),
        "limit": _int("最多返回条数"),
    }),
    _fn("ocr_image", "OCR 识别票据/账单图片，逐笔提取交易明细（返回 transactions 数组，"
        "非账单总额）。拿到后逐笔调 add_transaction 记账", {
        "path": _s("图片路径"),
    }, ["path"]),
    _fn("delete_transaction", "删除一条交易", {
        "id": _int("交易 id"),
    }, ["id"]),
    _fn("add_document", "归档一份家庭重要文档（合同/保单/证件等），登记到期日以便提醒", {
        "type": _s("文档类型", enum=_DOC_TYPES),
        "title": _s("文档名称，如 2026公寓租约"),
        "issuer": _s("签发方：房东/保险公司/政府机构"),
        "number": _s("编号：保单号/证件号"),
        "issue-date": _s("签发日期 YYYY-MM-DD"),
        "expiry": _s("到期日期 YYYY-MM-DD；长期有效不填"),
        "action-note": _s("到期要做什么，如 提前60天通知房东"),
        "remind-days": _int("提前几天提醒（不填用默认值）"),
        "file": _s("原始文件路径（图片已保存的路径）"),
        "ocr-text": _s("OCR 识别全文，用于日后关键词检索"),
        "notes": _s("备注"),
        "force": {"type": "boolean", "description": "跳过重复检查强制写入（仅在用户确认非重复后用）"},
    }, ["type", "title"]),
    _fn("list_documents", "查询已归档的家庭文档", {
        "type": _s("文档类型", enum=_DOC_TYPES),
        "member": _s("按成员过滤"),
        "keyword": _s("关键词，匹配标题/OCR全文/备注"),
        "status": _s("状态（默认隐藏 archived/superseded）", enum=_DOC_STATUSES),
        "limit": _int("最多返回条数"),
    }),
    _fn("show_document", "查看某文档完整信息（含文件路径）", {
        "id": _int("文档 id"),
    }, ["id"]),
    _fn("due_documents", "查询即将到期/已过期的文档", {
        "days": _int("查看几天内到期（不填按各文档默认提前量）"),
    }),
    _fn("update_document", "更新文档信息（续约改到期日、改状态归档等）", {
        "id": _int("文档 id"),
        "type": _s("文档类型", enum=_DOC_TYPES),
        "title": _s("文档名称"),
        "issuer": _s("签发方"),
        "number": _s("编号"),
        "issue-date": _s("签发日期 YYYY-MM-DD"),
        "expiry": _s("新到期日 YYYY-MM-DD（改后重新进入提醒）"),
        "action-note": _s("到期要做什么"),
        "remind-days": _int("提前几天提醒"),
        "status": _s("状态", enum=_DOC_STATUSES),
        "notes": _s("备注"),
    }, ["id"]),
    _fn("ack_document", "确认某文档的到期提醒（之后不再每日重复提醒）", {
        "id": _int("文档 id"),
    }, ["id"]),
    _fn("backup_now", "立即把用户数据镜像到云盘（需用户已配置 backup provider）", {}),
    _fn("backup_status", "查看云盘备份状态（是否启用/已配置/待同步/上次同步/错误）", {}),
    _fn("backup_verify", "校验云端镜像与本地清单是否一致", {}),
    _fn("save_note", "保存一条个人备忘（杂项信息：车位号/wifi密码/课表/名片等）。"
        "仅本人可见", {
        "content": _s("备忘内容（图片来源时传 OCR 出的关键信息）"),
        "source-image": _s("来源图片路径（图片备忘时填已保存路径）"),
        "pinned": {"type": "boolean", "description": "置顶：重要长期信息每次对话自动带上"},
    }, ["content"]),
    _fn("list_notes", "列出本人最近的备忘", {
        "limit": _int("最多返回条数（默认 20）"),
    }),
    _fn("search_notes", "按关键词搜索本人的备忘（用户问\"我记过什么\"\"XX是什么来着\"）", {
        "keyword": _s("关键词，匹配备忘内容"),
    }, ["keyword"]),
    _fn("delete_note", "删除本人的一条备忘", {
        "id": _int("备忘 id"),
    }, ["id"]),
    _fn("pin_note", "置顶/取消置顶本人的一条备忘", {
        "id": _int("备忘 id"),
        "unpin": {"type": "boolean", "description": "true=取消置顶"},
    }, ["id"]),
    _fn("create_worksheet", "创建一张工作表，用于长期跟踪结构化信息。仅当用户明确要求"
        "\"建个表/做个 worksheet/长期记录这些\"时才用；普通杂事用 save_note。"
        "kind=kv 是事实清单（字段→值，如房贷利率/到期）；kind=table 是流水记录"
        "（多行，每行动态列，如血压/体重打卡）", {
        "title": _s("工作表名（唯一，作为后续引用的句柄）"),
        "kind": _s("kv=事实清单 / table=流水记录", enum=["kv", "table"]),
        "pinned": {"type": "boolean", "description": "置顶：每次对话自动带上全表内容"},
    }, ["title", "kind"]),
    _fn("list_worksheets", "列出本人的工作表（名称/类型/规模）", {}),
    _fn("show_worksheet", "显示一张工作表的完整内容", {
        "title": _s("工作表名"),
    }, ["title"]),
    _fn("set_worksheet_field", "在 kv 工作表上设置/覆盖一个字段", {
        "title": _s("工作表名"),
        "field": _s("字段名"),
        "value": _s("字段值"),
    }, ["title", "field", "value"]),
    _fn("unset_worksheet_field", "从 kv 工作表删除一个字段", {
        "title": _s("工作表名"),
        "field": _s("字段名"),
    }, ["title", "field"]),
    _fn("add_worksheet_row", "向 table 工作表追加一行（列名→值，列可动态新增）", {
        "title": _s("工作表名"),
        "data": {"type": "object", "description": "一行数据，键=列名 值=单元格值"},
    }, ["title", "data"]),
    _fn("edit_worksheet_row", "覆盖 table 工作表的某一行（按行 id）", {
        "title": _s("工作表名"),
        "row-id": _int("行 id（见 show_worksheet 的 #号）"),
        "data": {"type": "object", "description": "整行新数据（覆盖式）"},
    }, ["title", "row-id", "data"]),
    _fn("delete_worksheet_row", "删除 table 工作表的某一行（按行 id）", {
        "title": _s("工作表名"),
        "row-id": _int("行 id"),
    }, ["title", "row-id"]),
    _fn("rename_worksheet", "重命名一张工作表", {
        "title": _s("当前名"),
        "new-title": _s("新名"),
    }, ["title", "new-title"]),
    _fn("pin_worksheet", "置顶/取消置顶工作表（置顶=每次对话自动带上全表）", {
        "title": _s("工作表名"),
        "unpin": {"type": "boolean", "description": "true=取消置顶"},
    }, ["title"]),
    _fn("delete_worksheet", "删除整张工作表（含所有行）", {
        "title": _s("工作表名"),
    }, ["title"]),
    _fn("visualize_data", "把工作表里的数字画成图表（折线/柱状/饼图）并发给用户。"
        "你先从相关工作表取出对应数字（必要时先 show_worksheet），再调本工具。"
        "用户说\"画个图/可视化/看看趋势/show me the chart\"时用", {
        "spec": {
            "type": "object",
            "description": "图表规格：type(line/bar/pie), title, 可选 x_label/y_label, "
                           "x_labels(类别/X轴数组), series(数组，每项 {name, values})。"
                           "line/bar 可多 series；pie 只能一个 series，values 对应 x_labels",
        },
    }, ["spec"]),
    _fn("send_document", "把已归档的文档原件（租约/保单/证件等）发给用户。"
        "先用 list_documents/show_document 找到对应文档的 id", {
        "id": _int("文档 id"),
    }, ["id"]),
    _fn("send_file", "把 data 目录内的一个文件发给用户（path 为 data 相对路径）。"
        "只能发家庭共享文件或你自己的文件", {
        "path": _s("文件的 data 相对路径"),
    }, ["path"]),
    _fn("add_event", "添加家庭日程/活动/安排（自动同步到远程日历）", {
        "title": _s("活动标题，如 孩子游泳课"),
        "date": _s("日期 YYYY-MM-DD"),
        "start": _s("开始时间 HH:MM（不知道具体时间就不填=全天）"),
        "end": _s("结束时间 HH:MM"),
        "all-day": {"type": "boolean", "description": "全天活动"},
        "location": _s("地点"),
        "notes": _s("备注"),
        "source-image": _s("从图片（邀请函/海报/截图）建活动时，传那张已保存图片的路径，"
                           "留存原始材料；纯文字建活动不填"),
        "for-member": _s("默认归你（当前成员）自己的日历，即使活动关于别人也是。仅当用户"
                         "明确说\"加到 X 的日历\"时，传该家庭成员名；否则不填"),
    }, ["title", "date"]),
    _fn("add_task", "添加待办/任务（自动同步到远程待办清单）", {
        "title": _s("待办内容，如 买生日蛋糕"),
        "due": _s("截止日期 YYYY-MM-DD（没有就不填）"),
        "notes": _s("备注"),
        "source-image": _s("从图片（账单/发票/截图）建待办时，传那张已保存图片的路径，"
                           "留存原始材料；纯文字建待办不填"),
        "for-member": _s("默认归你自己的待办清单。仅当用户明确说\"记到 X 的待办\"时，"
                         "传该家庭成员名；否则不填"),
    }, ["title"]),
    _fn("list_schedule", "查询未来日程与开放待办（用户问\"接下来有什么安排\"\"待办清单\"）", {
        "days": _int(f"窗口天数（默认 {_CAL_LOOKAHEAD}）"),
        "kind": _s("只看活动或待办", enum=["event", "task"]),
        "all": {"type": "boolean", "description": "包含已完成/已取消"},
    }),
    _fn("complete_task", "标记一条待办完成（同步到远程）", {
        "id": _int("日程 id"),
    }, ["id"]),
    _fn("remove_schedule_item", "取消一条日程/待办（已上云的同步删除远端）", {
        "id": _int("日程 id"),
    }, ["id"]),
    _fn("sync_calendar", "立即与远程日历强制同步一轮（用户说\"刷新/同步日历\"）", {}),
    _fn("calendar_status", "查看日历同步状态（启用/配置/上次刷新/待同步/错误）", {}),
    _fn("web_search", "联网搜索最新资讯/新闻/动态（用户问\"最新新闻\"\"外面在发生什么\"\"帮我查一下X\"）。"
        "返回抓取到的网页结果原文，你据此用中文总结报告", {
        "query": _s("搜索关键词/问题"),
    }, ["query"]),
    _fn("web_read", "抓取并阅读一个网页链接（用户发链接让看/总结文章时）。返回网页正文，你据此总结", {
        "url": _s("网页 URL"),
    }, ["url"]),
    _fn("youtube_summarize", "获取 YouTube 视频字幕转写（用户发 YouTube 链接让总结时）。"
        "返回字幕全文（无字幕则返回标题+简介），你据此用中文总结视频内容", {
        "url": _s("YouTube 视频 URL"),
    }, ["url"]),
    _fn("anysearch_search", "高质量实时联网搜索（AnySearch）。比 web_search 更准，"
        "查最新资讯/事实/股价/学术/健康等首选。需要垂直领域结构化结果时（finance/health/"
        "academic/travel/code 等），先用 anysearch_subdomains 拿到 sub_domain 再传 domain/sub_domain。"
        "返回结果原文，你据此用中文总结报告", {
        "query": _s("搜索关键词/问题"),
        "domain": _s("垂直领域（可选）", enum=[
            "general", "resource", "social_media", "finance", "academic", "legal",
            "health", "business", "security", "ip", "code", "energy",
            "environment", "agriculture", "travel", "film", "gaming"]),
        "sub_domain": _s("子域路由键（如 finance.quote），垂直搜索时配 domain；先用 anysearch_subdomains 发现"),
        "sub_domain_params": _s("子域参数，key=value,key2=value2 或 JSON（schema 见 anysearch_subdomains 输出）"),
        "max_results": _int("返回结果数 1-10（默认 10）"),
    }, ["query"]),
    _fn("anysearch_extract", "抓取并提取一个网页链接的全文（AnySearch，markdown）。"
        "用户发链接让看/总结、或搜索摘要不够需读全文时用。返回正文，你据此总结", {
        "url": _s("网页 URL"),
    }, ["url"]),
    _fn("anysearch_subdomains", "查某垂直领域的可用子域及参数 schema（垂直 anysearch_search 前的发现步骤）。"
        "返回 domain/sub_domain/query_format/params_schema 表", {
        "domains": _s("单个或逗号分隔的多个领域，如 finance 或 finance,health"),
    }, ["domains"]),
]


# ── 备忘上下文注入 ──────────────────────────────────────────

sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Note_Keeper"))
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Calendar_Keeper"))


def _notes_context(member: str, recent_limit: int = 5, clip: int = 100) -> str:
    """取该成员置顶 + 最近备忘，拼成 system prompt 附加块。

    进程内直调 note_db（每条消息都要取，subprocess 太重）。
    任何失败返回空串 —— 备忘注入绝不能拖垮 handle()。
    """
    try:
        import note_db
        notes = note_db.pinned_and_recent(member, recent_limit=recent_limit)
        if not notes:
            return ""
        lines = []
        for n in notes:
            content = n["content"][:clip] + ("…" if len(n["content"]) > clip else "")
            mark = "📌" if n.get("pinned") else "·"
            lines.append(f"{mark} #{n['id']} {content}")
        return (f"\n\n## 已存备忘（仅 {member} 可见；内容超长已截断，"
                f"完整内容用 search_notes 查）\n" + "\n".join(lines))
    except Exception:
        _log.exception("备忘上下文注入失败（已跳过）")
        return ""


def _worksheets_context(member: str, db_path: str | None = None) -> str:
    """取该成员置顶工作表，整表渲染进 system prompt（选择 B：全量注入）。

    进程内直调 sheet_db。table 超 _WORKSHEET_PIN_ROW_CAP 行截断并提示。
    任何失败返回空串 —— 工作表注入绝不能拖垮 handle()。
    """
    try:
        import sheet_db
        kw = {"db_path": db_path} if db_path else {}
        sheets = sheet_db.pinned_sheets(member, **kw)
        if not sheets:
            return ""
        blocks = []
        for s in sheets:
            if s is None:
                continue
            lines = [f"### {s['title']}（{s['kind']}）"]
            if s["kind"] == "kv":
                for k, v in s["kv_data"].items():
                    lines.append(f"- {k}: {v}")
            else:
                rows = s["rows"]
                shown = rows[:_WORKSHEET_PIN_ROW_CAP]
                for row in shown:
                    cells = "  ".join(f"{k}={v}" for k, v in row["row_data"].items())
                    lines.append(f"- #{row['id']} {cells}")
                if len(rows) > _WORKSHEET_PIN_ROW_CAP:
                    lines.append(f"- …还有 {len(rows) - _WORKSHEET_PIN_ROW_CAP} 行，"
                                 f"用 show_worksheet 看全部")
            blocks.append("\n".join(lines))
        if not blocks:
            return ""
        return (f"\n\n## 已存工作表（仅 {member} 可见，置顶项全量带上）\n"
                + "\n\n".join(blocks))
    except Exception:
        _log.exception("工作表上下文注入失败（已跳过）")
        return ""


def _schedule_context(member: str | None = None, db_path: str | None = None,
                      clip: int = 60, max_lines: int = 15) -> str:
    """成员未来 N 天日程 + 待办，拼成 system prompt 附加块（按成员私有，分活动/待办两库）。

    db_path 给定 → 直读该单库（测试/兼容）；否则按 member 读其 schedule + tasks 两库。
    进程内直调 cal_db（每条消息都要取，subprocess 太重）。
    任何失败返回空串 —— 日程注入绝不能拖垮 handle()。
    """
    try:
        import cal_db
        if db_path:
            rows = cal_db.list_upcoming(days=_CAL_LOOKAHEAD, db_path=db_path)
        elif member:
            rows = []
            for domain in ("schedule", "tasks"):
                rows.extend(cal_db.list_upcoming(
                    days=_CAL_LOOKAHEAD,
                    db_path=str(_paths.member_store(member, domain))))
            rows.sort(key=lambda r: (r["start_at"] == "", r["start_at"], r["id"]))
        else:
            return ""
        if not rows:
            return ""
        lines = []
        for r in rows[:max_lines]:
            title = r["title"][:clip] + ("…" if len(r["title"]) > clip else "")
            if r["kind"] == "event":
                s = r["start_at"]
                when = (s[5:10] + (" " + s[11:16] if len(s) > 10 else " 全天")) if s else ""
                loc = f" @{r['location']}" if r["location"] else ""
                lines.append(f"- {when} {title}{loc}".strip())
            else:
                due = f"（截止 {r['start_at'][5:10]}）" if r["start_at"] else ""
                lines.append(f"- ☐ {title}{due}")
        return (f"\n\n## 你未来{_CAL_LOOKAHEAD}天的日程与待办（按成员私有，已静默同步自你的远程日历；"
                f"不要主动播报，仅在用户问到或相关时使用）\n" + "\n".join(lines))
    except Exception:
        _log.exception("日程上下文注入失败（已跳过）")
        return ""


# ── Agent ───────────────────────────────────────────────────

class Agent:
    """频道无关的全量上下文智能助手。每条消息带完整项目文档 + 对话历史调 DeepSeek。"""

    def __init__(self, history_size: int = 20):
        self.system_prompt = _build_system_prompt()
        self.history_size = history_size
        self.history: dict[str, list[dict]] = defaultdict(list)

    def handle(self, text: str, user: str = "default", member: str = "") -> str:
        # 防御纵深：传输层闸门漏掉的未注册来源，这里二次拦截，不碰 LLM
        if not member:
            return ""
        text = text.strip()
        if not text:
            return "收到空消息。"

        # 频道无关命令：清除本用户对话上下文（不经 LLM，零 token）
        if text.lower() in ("/clear", "清除上下文", "清空上下文", "清空记忆"):
            self.history.pop(user, None)
            return "✅ 对话上下文已清除。"

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return "未配置 DEEPSEEK_API_KEY。"

        member_note = (f"\n\n## 当前对话成员\n{member} —— 写入类操作自动归到该成员名下；"
                       f"查询类工具可用 member 参数按成员过滤。")
        msgs = [{"role": "system",
                 "content": self.system_prompt + member_note
                 + _notes_context(member) + _worksheets_context(member)
                 + _schedule_context(member)}]
        user_history = self.history[user]
        msgs.extend(user_history[-self.history_size * 2:])
        msgs.append({"role": "user", "content": text})

        reply = ""
        tool_log = ""  # 回复里展示的工具调用摘要（按名计数）
        tool_counts: dict[str, int] = {}
        produced_images: list[str] = []  # 图片工具成功产出的 data 相对路径
        produced_docs: list[str] = []     # 文档工具成功产出的 data 相对路径
        # 多轮工具循环：单轮可并发多次调用；上限给足，让账单/流水逐行批量记账
        # 能跨轮记完（行数多时模型分多条回复继续）。普通对话一两轮即 break，不受影响。
        for _ in range(8):
            message = self._call_llm(msgs)
            if message is None:
                return "抱歉，暂时出错了。"

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                reply = (message.get("content") or "").strip()
                break

            msgs.append(message)
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                try:
                    targs = json.loads(tc["function"].get("arguments") or "{}")
                except (json.JSONDecodeError, KeyError):
                    targs = {}
                fn = _TOOL_MAP.get(name)
                targs = _apply_member(name, targs, member)
                result = fn(targs) if fn else f"[错误] 未知工具: {name}"
                # 回复里只按工具名计数（逐条列参数会刷屏）；明细进调试日志
                brief = ", ".join(f"{k}={v}" for k, v in targs.items())
                _log.debug("工具 %s(%s) → %s", name, brief, result[:200])
                if result and not result.startswith("[错误]"):
                    if name in _IMAGE_TOOLS:
                        produced_images.append(result.strip())
                    elif name in _DOC_TOOLS:
                        produced_docs.append(result.strip())
                tool_counts[name] = tool_counts.get(name, 0) + 1
                msgs.append({"role": "tool",
                             "tool_call_id": tc.get("id", ""),
                             "content": result})

        if tool_counts:
            tool_log = "⚙️ " + ", ".join(
                f"{n}×{c}" if c > 1 else n for n, c in tool_counts.items()) + "\n"
        if not reply:
            reply = "（工具已执行，但生成回复失败）" if tool_log else "抱歉，暂时出错了。"
        final = f"{tool_log}\n{reply}".strip() if tool_log else reply
        self._save_history(user, text, reply)
        for p in produced_images:
            final += f"\n{IMG_SENTINEL}{p}"
        for p in produced_docs:
            final += f"\n{DOC_SENTINEL}{p}"
        return final

    def handle_image(self, image_path: str, user: str = "default", member: str = "") -> str:
        """图片/PDF 入口：ocr_image 对两者一视同仁（PDF 走腾讯 IsPdf 逐页），
        OCR 文字交 LLM 按五类分流（记账/文档归档/备忘/日程）。"""
        if not member:
            return ""
        from ocr import ocr_image, is_available
        if not is_available():
            return "📄 材料已收到（已保存）。请用文字描述（如\"午餐45块\"），或配置腾讯云 OCR 自动识别。"
        ocr_text = ocr_image(image_path)
        if ocr_text:
            prompt = (
                f"用户发来一份材料（图片或 PDF），已保存为 {image_path}，OCR结果:\n{ocr_text}\n"
                f"判断内容，按五种情况处理：\n"
                f"1) 单张消费票据：提取金额/日期/类别，调 add_transaction 记一笔。\n"
                f"2) 银行/信用卡/支付App流水或账单（多行消费，PDF 多页账单常见）：逐笔记账，每条明细调一次"
                f" add_transaction（可在一条回复里并发多次调用）。**重点：记的是每一笔交易明细，绝不要把账单总额、"
                f"应还款额、最低还款额、已还款额当成一笔记账**——那些是汇总数字，不是消费。"
                f"每笔的 desc 带上能区分该行的信息（商家+时间，OCR 里有就带），这样同日同额的不同消费"
                f"不会被误判为重复，而重复发同一张截图会被正确拦截。某行确属独立消费却被重复检查拦下时，"
                f"对该行加 force=true 重记。行数多一条回复记不完就分多条继续记。"
                f"无法确定金额/日期的行先列出来问用户，不要瞎记。\n"
                f"3) 重要文档（合同/保单/证件/健康卡/政府或移民表格，PDF 多属此类）：用 add_document 归档，"
                f"file 传上面的保存路径，ocr-text 传 OCR 全文，type 选最合适的；"
                f"有到期日带 expiry，到期要办的事带 action-note。\n"
                f"4) 邀请函/活动海报/预约/带日期时间的安排 → add_event（有日期；有具体时间给 start/end，"
                f"location 给地点）；账单/发票/催款等需要跟进办理的 → add_task（截止日给 due）。"
                f"两者都把上面的保存路径传给 source-image，留存原始材料（日后可定期清理）。"
                f"默认进你（发送者）的日历/待办，即使活动关于别的成员；仅用户明确说加到某成员时"
                f"才传 for-member。\n"
                f"5) 其他有信息价值的材料（路由器标签/课表/名片/告示等杂项）：用 save_note 记备忘，"
                f"content 传 OCR 出的关键信息（整理成一两句话，别原样塞全文），"
                f"source-image 传上面的保存路径。看起来需要长期记住的（如 wifi 密码）加 pinned=true。\n"
                f"注意：开出去的发票/报价单/还没付的账单 = 没有实际现金流，绝不要直接记成收入或支出；"
                f"可建 add_task 跟进收款（带 source-image），别记成 income/expense。\n"
                f"信息不完整就先问用户。记完简要汇报记了什么。"
            )
            return self.handle(prompt, user=user, member=member)
        return "📄 材料已收到（已保存），但 OCR 没识别到文字（可能扫描件/加密）。请用文字告诉我这是什么。"

    def _call_llm(self, messages) -> dict | None:
        """调 DeepSeek chat completions（native function calling）。

        返回 choices[0].message 整个 dict（可能含 tool_calls）；失败返回 None。
        """
        import urllib.request
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        body = json.dumps({
            "model": model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            # DeepSeek V4 是推理模型，reasoning 占用 completion 预算，
            # 预算过低（曾 1500）会被推理耗尽 → content 空、无 tool_calls。
            # 账单图片 OCR 后逐笔记账尤其费 token，预算和超时都给足。
            "temperature": 0.3, "max_tokens": 10000,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
            choice = resp["choices"][0]
            _log.debug("LLM finish=%s tokens=%s tool_calls=%d",
                       choice.get("finish_reason"),
                       resp.get("usage", {}).get("completion_tokens"),
                       len(choice["message"].get("tool_calls") or []))
            if choice.get("finish_reason") == "length":
                _log.warning("LLM 输出被 max_tokens 截断（推理模型预算不足的信号）")
            return choice["message"]
        except Exception as e:
            print(f"[agent] LLM 调用失败: {e}", file=sys.stderr)
            _log.exception("LLM 调用失败")
            return None

    def _save_history(self, user, user_msg, assistant_msg):
        h = self.history[user]
        h.append({"role": "user", "content": user_msg})
        h.append({"role": "assistant", "content": assistant_msg})
        if len(h) > self.history_size * 2:
            self.history[user] = h[-self.history_size * 2:]


# ── 测试入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    print("Family Assistant — Agent Core 测试模式")
    print("频道无关，全量上下文，跟 CodeWhale 一样的工作方式。")
    ok = bool(os.environ.get("DEEPSEEK_API_KEY"))
    print(f"LLM: {'已启用' if ok else '未配置 — 设置 DEEPSEEK_API_KEY'}")
    print("-" * 40)
    agent = Agent()
    while True:
        try:
            msg = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in ("quit", "exit", "q"):
            break
        print(agent.handle(msg, member="本地测试"))
        print()
