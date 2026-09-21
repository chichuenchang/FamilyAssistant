"""
Agent Core — 频道无关的全量上下文智能助手。

所有远程频道（微信、Telegram、未来其他）共用这一个 Agent，行为一致。
与 CodeWhale 工作方式一致：读取整个项目文档，理解意图，自主决策。
告别关键词路由，每条消息都带完整项目上下文调 LLM（模型可切换，见 llm_client）。

模式:
    启动时加载项目文档 → 构建 system prompt
    每条消息 → system + 对话历史 + 用户消息 → LLM（function calling）
    LLM 自主选择工具 → 执行 → LLM 生成自然语言回复

频道接入契约（详见 .codewhale/skills/Agent_Runtime/SKILL.md）:
    agent.handle(text, user, member)        # 文字消息
    agent.handle_media(paths, text, user, member)  # 图片/PDF + 随后文字
    user = 频道内唯一 id（隔离各用户对话历史）
    member = members.resolve 解析出的成员名；为空直接返回空串（未注册来源不碰 LLM）

依赖:
    当前模型的 API key 环境变量（默认 DEEPSEEK_API_KEY；模型表见 llm_client / config.json llm）

用法:
    from agent_core import Agent  # 同目录传输层直接 import
    agent = Agent()
    reply = agent.handle("这个月花了多少", user="wx_xxx")
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import chat_archive
import context_budget as _budget
import llm_client as _llm
import members as _members_registry
import paths as _paths
import skill_registry
import tool_runtime as rt

_log = logging.getLogger("familyassist.agent")


# ── config.json（值的单一事实来源；不在代码里重复硬编码） ──────

_CONFIG = rt.CONFIG

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

# 调试日志封顶：单文件 2 MB × (1 + 3 份轮转) ≈ 8 MB
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 3


def setup_logging(debug: bool = True) -> logging.Logger:
    """配置 "familyassist" 日志器，各传输层（telegram/wechat）调一次即可。

    项目规范：所有 Bot 默认开调试日志（debug=True）。新增 Bot 直接 setup_logging() 即继承。
    debug=True（默认）：DEBUG 全量，同时写 stderr 和 data/.state/bot_debug.log（含完整 traceback），
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
    # 控制台只打消息本体（全量），不带 时间/[级别]/logger 名 前缀；完整格式留给日志文件
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(sh)
    if debug:
        log_file = _paths.state_file("bot_debug.log")
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.debug("调试日志已开启 → %s", log_file)
    return logger



# ── 技能注册表：各 skill 的 agent_tools.py 合并（新增 skill 零改动本文件） ──
# 契约见 skill_registry.py。命令白名单/路由/超时住在 tool_runtime，此处只转发同一对象。

REGISTRY = skill_registry.load()

ALLOWED_COMMANDS = rt.ALLOWED
_CLI_TIMEOUTS = rt.CLI_TIMEOUTS
_cli_path = rt.cli_path
_run_cli = rt.run_cli
_relocate_image = rt.relocate_image
_resolve_sendable = rt.resolve_sendable
_TOOL_MAP = REGISTRY.tool_map
TOOL_SCHEMAS = REGISTRY.schemas
_MEMBER_LOCKED = REGISTRY.member_locked
_CONTEXT_TOOLS = REGISTRY.context_tools
_UNTRUSTED_TOOLS = REGISTRY.untrusted_tools
_IMAGE_TOOLS = REGISTRY.image_tools
_DOC_TOOLS = REGISTRY.doc_tools
_SHOW_TOOLS = REGISTRY.show_tools


def _apply_member(tool_name: str, targs: dict, member: str) -> dict:
    """MEMBER_LOCKED 工具：剥离 LLM 给的 member，注入解析出的成员名（写入归属 /
    按成员私有的读写皆强制，LLM 不得跨成员）。其余工具原样放行。"""
    if tool_name in _MEMBER_LOCKED:
        targs = {k: v for k, v in targs.items() if k.lstrip("-") != "member"}
        if member:
            targs["member"] = member
    return targs


def _apply_context(tool_name: str, targs: dict, channel: str, user: str,
                   member: str, turn_id: int = 0, text: str = "") -> dict:
    """CONTEXT_TOOLS：注入发起频道 + 发起人 id + 成员名（异步投递需要）
    + 本轮序号 __turn_id（每用户单调递增）与用户原话 __text（对外不可撤回动作的
    两轮确认闸门，见 Mail_Keeper/mail_draft.check）。确定性来自代码而非 LLM。
    其余工具原样放行。"""
    if tool_name in _CONTEXT_TOOLS:
        targs = dict(targs)
        targs["__channel"] = channel or ""
        targs["__user"] = str(user) if user else ""
        targs["__turn_id"] = int(turn_id)
        targs["__text"] = text
        if member:
            targs["member"] = member
    return targs


def _apply_fence(tool_name: str, result: str) -> str:
    """UNTRUSTED_TOOLS：结果来自非本地来源，套围栏后才进 LLM。其余工具原样放行。"""
    return rt.fence(result, tool_name) if tool_name in _UNTRUSTED_TOOLS else result


# ── system prompt ───────────────────────────────────────────

# 与具体 skill 无关的准则；各 skill 自己的条目由 manifest PROMPT_RULES 提供，排在前面
_CORE_RULES = [
    "用户闲聊/问候 → 简短回复，不用调工具",
    "需要精确信息时（金额、日期）才调工具，闲聊不调",
    "工具执行后会返回结果，你基于结果用自然语言回复",
    '如果用户没有指定日期，默认今天（见"当前时间"块）；用户问现在几点/今天几号，直接按该块回答',
    "回复中不要暴露技术细节（如 SQLite、CLI 等）",
    rt.FENCE_RULE,
]


def _build_system_prompt(idle_clear_hours: float | None = None) -> str:
    """组装 system prompt：身份 + 成员 + 各 skill 段落（manifest）+ 通用准则。

    工具定义走 API tools 参数。SKILL.md / FamilyAssistant.md 是开发文档，不进 prompt。
    """
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

    idle_hours = (_IDLE_CLEAR_HOURS if idle_clear_hours is None
                  else float(idle_clear_hours))
    idle_note = (f"；用户闲置超过 {idle_hours:g} 小时后也会自动清空"
                 if idle_hours > 0 else "")

    sections = "\n\n".join(REGISTRY.prompt_sections)
    rules = "\n".join(f"- {r}" for r in [*REGISTRY.prompt_rules, *_CORE_RULES])

    return f"""你是 Family Assistant，一个运行在微信/Telegram 等远程频道里的个人/家庭 AI 助手。

## 你是谁
- 你可以帮用户记账、查账、汇总开销、管理定期存款、查询汇率、OCR 票据、记私人备忘等
- 你简洁、直接{member_block}

{sections}

## 对话上下文
- 用户随时可发 /clear（或"清除上下文"）清空与你的对话上下文{idle_note}
- 被问"你能不能清除上下文/记忆"时，如实说明上述机制，不要说做不到

## 斜杠命令（系统直接处理，你调不到；用户迷茫/问怎么用时照此说明，让用户自己发）
- /model — 查当前模型与可切换列表；/model <名字或别名> — 切换；/model reset — 恢复环境变量/默认
- /effort — 查当前推理档；/effort low|medium|high|max — 调档；/effort reset — 恢复环境变量/默认
- 只影响发命令的用户本人，重启后保留；推理档越高想得越深、回复越慢
- 用户没说困惑就别主动提这些命令（守"回复风格"：不刷屏罗列功能）

## 回复风格
- 简洁、易读是第一优先级：先给结论/结果，能一句话说清就不写三句
- 永远不要向用户刷屏式罗列命令、操作步骤或功能菜单；一次回复聚焦当前这件事
- 不要主动列"你可以让我做X/Y/Z"的能力清单，除非用户明确问"你能干什么"
- 列表只在确实有多条并列信息时用（如多笔账单汇总），且每条尽量一行

## 行为准则
{rules}
"""


IMG_SENTINEL = "\x01IMG:"
DOC_SENTINEL = "\x01DOC:"

# max_tokens 截断（finish_reason=length）
TRUNCATED_REPLY_MARK = "（回复超出长度上限被截断）"
TRUNCATED_TOOLS_NOTE = ("[系统] 你上一条输出超出长度上限被截断，其中的工具调用参数不完整，"
                        "全部未执行。请减少推理、把工作拆成更小的批次重新调用。")
# 工具轮数用完仍在调工具：补一次收尾调用，要求只用已有结果作答
TOOL_ROUNDS = 16
TOOL_LIMIT_NOTE = ("[系统] 本轮工具调用次数已用完，不要再调用任何工具。请直接用已拿到的结果"
                   "回复用户：列出已完成的部分，说明还剩哪些没做，并提示用户回复“继续”接着处理。")

CLEAR_COMMANDS = ("/clear", "清除上下文", "清空上下文", "清空记忆")


def is_clear_command(text: str) -> bool:
    return text.strip().lower() in CLEAR_COMMANDS


def is_command(text: str) -> bool:
    """不经 LLM 的频道无关命令（/clear 类、/model、/effort）。传输层据此绕开攒着的来件。"""
    return is_clear_command(text) or _llm.parse_command(text.strip()) is not None


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




# Agent 上下文管理旋钮（config.json "agent" 块；键缺失用默认值，显式 0 = 关闭该机制）
_AGENT_CFG = _CONFIG.get("agent") or {}
_CTX_MAX_TOKENS = int(_AGENT_CFG.get("context_max_tokens", 30000) or 0)
_IDLE_CLEAR_HOURS = float(_AGENT_CFG.get("idle_clear_hours", 4) or 0)



_WEEKDAYS_ZH = "一二三四五六日"


def _now_context() -> str:
    """当前日期时间块。每条消息现算——system prompt 在 __init__ 缓存，
    时间进缓存的话进程跨午夜后日期就冻结在启动日。"""
    now = datetime.now()
    return (f"\n\n## 当前时间\n{now:%Y-%m-%d %H:%M}"
            f"（星期{_WEEKDAYS_ZH[now.weekday()]}，本地时间）")



# ── 转发：实现在 context_budget / llm_client（测试直引这些名字） ──
_estimate_tokens = _budget.estimate_tokens
_msg_tokens = _budget.msg_tokens
_HIST_TOOL_CAP = _budget.HIST_TOOL_CAP
_load_llm_overrides = _llm.load_overrides
_save_llm_overrides = _llm.save_overrides


class Agent:
    """频道无关的全量上下文智能助手。每条消息带完整项目文档 + 对话历史调 LLM。

    上下文自动管理（旋钮在 config.json "agent" 块，构造参数可覆盖，0=关闭）：
    - context_max_tokens: 对话历史 token 预算。超出时从最旧的整轮开始丢弃
      （轮 = user 消息到下一条 user 之前，含工具调用/结果），
      保留最近上下文——老话题不再挤占预算，Agent 保持聚焦。
    - idle_clear_hours: 某用户闲置超过 N 小时后，下一条消息前自动清空其对话历史
      （隔了半天多半是新话题，旧上下文只会干扰）。
    - 用户随时可发 /clear（或"清除上下文"）手动清空。
    """

    def __init__(self, history_size: int = 20,
                 context_max_tokens: int | None = None,
                 idle_clear_hours: float | None = None,
                 channel: str = ""):
        # channel = 传输层名（"wechat"/"telegram"）；异步工具（knowking）据此把结果
        # 推回正确频道。本地测试留空 → 这类工具报错提示不可用。
        self.channel = channel
        self.system_prompt = _build_system_prompt(idle_clear_hours)
        self.history_size = history_size
        self.context_max_tokens = int(
            _CTX_MAX_TOKENS if context_max_tokens is None else context_max_tokens)
        hours = _IDLE_CLEAR_HOURS if idle_clear_hours is None else idle_clear_hours
        self.idle_clear_seconds = float(hours) * 3600
        self.history: dict[str, list[dict]] = defaultdict(list)
        self._last_active: dict[str, float] = {}
        self._turn_seq: dict[str, int] = {}   # 用户 → 轮次序号（两轮确认闸门用，进程内单调）
        self._llm_overrides: dict[str, dict] = _load_llm_overrides()

    def handle(self, text: str, user: str = "default", member: str = "", *,
               said: str | None = None) -> str:
        """said = 用户本人亲手打的字（确认闸门 __text 只认它）。text 可能掺了引用/OCR 等
        外部内容，传输层须另传 said；缺省 = text（本地 CLI 等无掺杂入口）。"""
        said = text if said is None else said
        # 防御纵深：传输层闸门漏掉的未注册来源，这里二次拦截，不碰 LLM
        if not member:
            return ""
        text = text.strip()
        if not text:
            return "收到空消息。"
        # 每条用户消息 = 一轮（/clear、/model 这类也算，中间插一条就断掉待确认草稿的链）
        turn_id = self._turn_seq.get(user, 0) + 1
        self._turn_seq[user] = turn_id

        # 闲置自动清空：距该用户上次消息超过 idle_clear_hours → 旧话题上下文作废
        now = time.time()
        last = self._last_active.get(user)
        if (last is not None and self.idle_clear_seconds > 0
                and now - last >= self.idle_clear_seconds and user in self.history):
            self.history.pop(user, None)
            _log.debug("用户 %s 闲置 %.1f 小时，自动清除对话上下文",
                       user, (now - last) / 3600)
        self._last_active[user] = now

        # 频道无关命令：清除本用户对话上下文（不经 LLM，零 token）
        if is_clear_command(text):
            self.history.pop(user, None)
            return "✅ 对话上下文已清除。"

        # 频道无关命令：/model /effort 运行时切换 LLM（不经 LLM，零 token）
        llm_reply = self._handle_llm_command(text, user)
        if llm_reply is not None:
            return llm_reply

        missing = _llm.missing_key(self._llm_settings(user)[0])
        if missing:
            return f"未配置 {missing}。"

        member_note = (f"\n\n## 当前对话成员\n{member} —— 写入类操作自动归到该成员名下；"
                       f"查询类工具可用 member 参数按成员过滤。")
        # 两条 system：静态项目文档单独一条（提供商可打前缀缓存），时间戳等易变内容在后
        msgs = [{"role": "system", "content": self.system_prompt},
                {"role": "system",
                 "content": _now_context().lstrip() + member_note
                 + self._llm_status_note(user)
                 + REGISTRY.context(member)}]
        # 历史（含跨轮保留的工具调用/结果）由 _save_history 控制长度，这里全量带上
        msgs.extend(self.history[user])
        msgs.append({"role": "user", "content": text})
        # 本轮完整消息序列（user → 中间 assistant/tool → 最终 assistant），
        # 结束后整体进历史——工具结果里的会话 id 等状态必须跨轮可见
        turn: list[dict] = [{"role": "user", "content": text}]

        reply = ""
        truncated = False  # 最终回复因 max_tokens 被截断
        tool_log = ""  # 回复里展示的工具调用摘要（按名计数）
        tool_counts: dict[str, int] = {}
        tokens = {"in": 0, "out": 0}      # 本轮所有 LLM 调用 usage 累计，随工具摘要展示
        fallback = ""                     # 本轮有调用被 fallback 模型顶替则记其名，进徽章
        produced_images: list[str] = []  # 图片工具成功产出的 data 相对路径
        produced_docs: list[str] = []     # 文档工具成功产出的 data 相对路径
        shown: dict[str, str] = {}        # SHOW_TOOLS 成功原文（每工具留最后一次），必达用户
        # 多轮工具循环：单轮可并发多次调用；上限给足，让账单/流水逐行批量记账
        # 能跨轮记完（行数多时模型分多条回复继续）。普通对话一两轮即 break，不受影响。
        for _ in range(TOOL_ROUNDS):
            message = self._call_llm(msgs, user=user)
            if message is None:
                if tool_counts:
                    break   # 工具已生效：走收尾，照常进历史与存档
                return "\n\n".join(["抱歉，暂时出错了。", *shown.values()])
            usage = message.pop("_usage", None) or {}   # 私有键，不得回传 API
            cut = message.pop("_finish", None) == "length"
            used = message.pop("_fallback", None)   # 本次调用的顶替模型，无则空
            fallback = used or fallback
            tokens["in"] += usage.get("prompt_tokens") or 0
            tokens["out"] += usage.get("completion_tokens") or 0
            # 思考链进 INFO 日志；键不 pop——DeepSeek 同轮工具调用须原样回传。
            # 封顶 2000 字：高档推理链 2–50k 字，全量几天就轮转掉 traceback。
            cot = (message.get("reasoning_content") or "").strip()
            if cot:
                _log.info("思考链 用户=%s 模型=%s:\n%s", user,
                          used or self._llm_settings(user)[0], cot[:2000])

            tool_calls = message.get("tool_calls") or []
            if cut and tool_calls:
                # 参数 JSON 可能残缺（解析失败会变空参数照跑工具）：一律不执行，
                # 残缺消息不进 msgs/历史，改告诉模型重来
                _log.warning("截断的 tool_calls 未执行: %s",
                             [tc.get("function", {}).get("name") for tc in tool_calls])
                msgs.append({"role": "user", "content": TRUNCATED_TOOLS_NOTE})
                continue
            if not tool_calls:
                reply = (message.get("content") or "").strip()
                truncated = cut
                break

            msgs.append(message)
            # 历史只存干净结构（去 reasoning_content 等 API 附带的大字段）
            turn.append({"role": "assistant",
                         "content": message.get("content") or "",
                         "tool_calls": tool_calls})
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                try:
                    targs = json.loads(tc["function"].get("arguments") or "{}")
                except (json.JSONDecodeError, KeyError):
                    targs = {}
                fn = _TOOL_MAP.get(name)
                targs = _apply_member(name, targs, member)
                targs = _apply_context(name, targs, self.channel, user, member,
                                       turn_id=turn_id, text=said)
                result = fn(targs) if fn else f"[错误] 未知工具: {name}"
                # 回复里只按工具名计数（逐条列参数会刷屏）；明细进调试日志
                brief = ", ".join(f"{k}={v}" for k, v in targs.items())
                _log.debug("工具 %s(%s) → %s", name, brief, result[:200])
                if result and not result.startswith("[错误]"):
                    if name in _IMAGE_TOOLS:
                        # 每行一张（fetch_images 多张）；按行不按空白拆——路径可含空格
                        produced_images.extend(
                            ln.strip() for ln in result.splitlines() if ln.strip())
                    elif name in _DOC_TOOLS:
                        # pdf-edit 第一行是路径，后续可能有"警告:"行——哨兵只取首行
                        produced_docs.append(result.strip().splitlines()[0])
                    if name in _SHOW_TOOLS:
                        shown[name] = result.replace("\x01", "")   # 外部文本不得伪造哨兵行
                tool_counts[name] = tool_counts.get(name, 0) + 1
                msgs.append({"role": "tool",
                             "tool_call_id": tc.get("id", ""),
                             "content": _apply_fence(name, result)})
                # 先截断再套围栏：反过来闭合标记会被截掉
                turn.append({"role": "tool",
                             "tool_call_id": tc.get("id", ""),
                             "content": _apply_fence(
                                 name, _budget.clip_tool_result(result))})
        else:
            # 轮数用完没 break：收尾一次。仍带 tools（历史里有 tool_calls，
            # 部分 API 不带 tools 会拒），返回的 tool_calls 一律不执行
            _log.warning("工具轮数 %d 用完，收尾调用 用户=%s", TOOL_ROUNDS, user)
            msgs.append({"role": "user", "content": TOOL_LIMIT_NOTE})
            message = self._call_llm(msgs, user=user)
            if message is not None:
                usage = message.pop("_usage", None) or {}
                truncated = message.pop("_finish", None) == "length"
                fallback = message.pop("_fallback", None) or fallback
                tokens["in"] += usage.get("prompt_tokens") or 0
                tokens["out"] += usage.get("completion_tokens") or 0
                reply = (message.get("content") or "").strip()

        badge = []
        if tool_counts:
            badge.append("⚙️ " + ", ".join(
                f"{n}×{c}" if c > 1 else n for n, c in tool_counts.items()))
        if tokens["in"] or tokens["out"]:
            badge.append(f"🪙 {tokens['in']:,} in / {tokens['out']:,} out")
        if fallback:
            badge.append(f"🔁 {fallback} 顶替")
        if badge:
            tool_log = " · ".join(badge) + "\n"
        if truncated:
            # 残缺文本不进历史，免得模型下轮把半句当既成事实接着编
            hist_reply = TRUNCATED_REPLY_MARK
            reply = f"{reply}\n{TRUNCATED_REPLY_MARK}" if reply else TRUNCATED_REPLY_MARK
        else:
            if not reply:
                reply = "（工具已执行，但生成回复失败）" if tool_counts else "抱歉，暂时出错了。"
            hist_reply = reply
        final = f"{tool_log}\n{reply}".strip() if tool_log else reply
        turn.append({"role": "assistant", "content": hist_reply})
        self._save_history(user, turn)
        chat_archive.append(self.channel, user, said, hist_reply)
        if shown:
            final += "\n\n" + "\n\n".join(shown.values())
        for p in produced_images:
            final += f"\n{IMG_SENTINEL}{p}"
        for p in produced_docs:
            final += f"\n{DOC_SENTINEL}{p}"
        return final

    def handle_media(self, paths: list[str], text: str, user: str = "default",
                     member: str = "", *, said: str | None = None) -> str:
        """图片/PDF + 随后文字指令 一并处理。传输层攒下来件，等用户发文字才调（来件本身不回复）。
        ocr_image 对图片/PDF 一视同仁（PDF 走腾讯 IsPdf 逐页）；OCR 不可用或没识别到文字时
        只给路径，靠用户文字指令。said 同 handle（缺省 = text）。"""
        if not member:
            return ""
        from ocr import ocr_image, is_available
        ocr_on = is_available()
        parts = []
        for i, path in enumerate(paths, 1):
            ocr_text = ocr_image(path) if ocr_on else ""
            body = rt.fence(ocr_text, "ocr") if ocr_text else "（无 OCR 文字）"
            parts.append(f"材料{i}: {path}\nOCR结果:\n{body}")
        routes = "\n".join(f"{i}) {r}" for i, r in enumerate(REGISTRY.image_routes, 1))
        prompt = (
            f"用户发来 {len(paths)} 份材料（图片或 PDF），已保存：\n" + "\n\n".join(parts)
            + f"\n\n用户随后的指示:\n{text}\n\n"
            f"指示明显与这些材料无关（如另起话题）→ 材料作废：不处理、不提及，只回应指示。\n"
            f"否则按用户指示处理这些材料。指示没说清归类时，按以下情况判断（取最匹配的一条）：\n{routes}\n"
            f"信息不完整就先问用户。拿不准归哪类时问用户。处理完简要汇报做了什么。"
        )
        return self.handle(prompt, user=user, member=member,
                           said=text if said is None else said)

    def _handle_llm_command(self, text: str, user: str) -> str | None:
        """/model /effort 运行时切换（不经 LLM，零 token）。是切换命令返回回复，否则 None。"""
        return _llm.apply_command(self._llm_overrides, user, text, self._persist_llm_override)

    def _persist_llm_override(self, user: str) -> bool:
        """写回状态文件：先重读合并（另一传输进程的切换不被覆盖），再原子写。"""
        try:
            on_disk = _load_llm_overrides()
            if user in self._llm_overrides:
                on_disk[user] = self._llm_overrides[user]
            else:
                on_disk.pop(user, None)
            _save_llm_overrides(on_disk)
            return True
        except Exception:
            _log.warning("LLM 覆盖状态写回失败", exc_info=True)
            return False

    def _llm_settings(self, user: str) -> tuple[str, str]:
        """该用户生效的 (model, effort)：个人覆盖 > 环境变量 > 默认。"""
        return _llm.settings(self._llm_overrides, user)

    def _llm_status_note(self, user: str) -> str:
        return _llm.status_note(self._llm_overrides, user)

    def _call_llm(self, messages, user: str = "") -> dict | None:
        """返回 choices[0].message dict（可能含 tool_calls）；失败 None。"""
        return _llm.chat(messages, TOOL_SCHEMAS, *self._llm_settings(user))

    def _save_history(self, user, turn_msgs: list[dict]):
        """整轮消息（user → 中间 assistant/tool → 最终 assistant）追加进历史。

        工具调用与结果必须跨轮保留：填表等多轮流程的状态（会话 id）只出现在
        工具结果里，丢了模型下一轮就会瞎编（曾把 PDF 文件名当会话 id 用）。
        裁剪一律按整轮进行（轮 = 一条 user 到下一条 user 之前），
        绝不留下没有配对 assistant tool_calls 的孤儿 tool 消息。
        """
        h = self.history[user]
        h.extend(turn_msgs)
        trimmed = _budget.trim_history(h, self.history_size, self.context_max_tokens)
        if trimmed:
            _log.debug("用户 %s 对话历史超 %d token 预算，丢弃最旧 %d 轮",
                       user, self.context_max_tokens, trimmed)


# ── 测试入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    print("Family Assistant — Agent Core 测试模式")
    print("频道无关，全量上下文，跟 CodeWhale 一样的工作方式。")
    print(_llm.ready_note())
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
