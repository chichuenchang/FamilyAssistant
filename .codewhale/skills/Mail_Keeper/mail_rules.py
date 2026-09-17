"""
Mail Keeper — 新邮件播报的「别再推这种」规则（用户教出来的，纯逻辑）。

起点是**全推**：规则表空 = 每封新邮件都播报。用户说"这种以后别推"时，Agent 调
mail_mute 落一条规则，此后命中的信 mail_watch 直接丢（不播报、游标照常前进）。
判断在代码里按规则做，不让 LLM 每封信现想 —— 便宜、可解释、可撤销（mail_rules 删）。

规则 data/<成员>/mail/rules.json（学来的偏好，入备份，跟人走）：
    [{"kind": sender|domain|subject|label, "value": str, "note": str, "added_at": ts}]

四种 kind 覆盖实际会说的话：
    sender   某个地址（"这个发件人别推了"）
    domain   整个域含子域（"这家公司的都别推"；shop.example 也挡 news@mail.shop.example）
    subject  主题含某词（不分大小写；"带 newsletter 的别推"）
    label    Gmail 自己的分类标签，如 CATEGORY_PROMOTIONS / CATEGORY_SOCIAL
             （"广告类的都别推"）—— 标签随 history 一起来，命中时连信头都不用取
"""

from __future__ import annotations

import re
import time
from email.utils import parseaddr
from pathlib import Path

import jsonfile
import members as _members      # noqa: F401  测试打桩 load_members 走这里
import paths as _paths

KINDS = ("sender", "domain", "subject", "label")
# 口语 → Gmail 分类标签（用户不会说 CATEGORY_PROMOTIONS）
LABEL_ALIASES = {
    "promotions": "CATEGORY_PROMOTIONS", "promotion": "CATEGORY_PROMOTIONS",
    "广告": "CATEGORY_PROMOTIONS", "促销": "CATEGORY_PROMOTIONS",
    "social": "CATEGORY_SOCIAL", "社交": "CATEGORY_SOCIAL",
    "updates": "CATEGORY_UPDATES", "通知": "CATEGORY_UPDATES",
    "forums": "CATEGORY_FORUMS", "论坛": "CATEGORY_FORUMS",
}


def store_path(member: str) -> Path:
    return _paths.member_mail_rules(member)


def load(member: str) -> list[dict]:
    rules = jsonfile.load_dict(store_path(member)).get("rules")
    if not isinstance(rules, list):       # 文件被手改坏也不炸
        return []
    return [r for r in rules if isinstance(r, dict)]


def _save(member: str, rules: list[dict]) -> None:
    jsonfile.save(store_path(member), {"rules": rules}, indent=1)


def normalise(kind: str, value: str) -> tuple[str, str]:
    """规范化一条规则的 (kind, value)；kind 不认识则抛 ValueError。"""
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {KINDS}")
    v = (value or "").strip()
    if not v:
        raise ValueError("规则值不能为空")
    if kind == "label":
        v = LABEL_ALIASES.get(v.lower(), v.upper())
    elif kind == "domain":           # LLM 可能给整个地址或网址：只留主机名
        v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v.lower()).split("/")[0]
        v = v.rsplit("@", 1)[-1].strip(".")
        if not v:
            raise ValueError("domain 要给域名，如 shop.example")
    elif kind == "sender":
        v = (parseaddr(v)[1] or v).lower()
    return kind, v


def add(member: str, *, kind: str, value: str, note: str = "") -> list[dict]:
    """加一条规则（同 kind+value 已存在则只更新备注）。返回规则表。"""
    kind, value = normalise(kind, value)
    rules = load(member)
    for r in rules:
        if r.get("kind") == kind and str(r.get("value", "")).lower() == value.lower():
            if note:
                r["note"] = note
            _save(member, rules)
            return rules
    rules.append({"kind": kind, "value": value, "note": note, "added_at": time.time()})
    _save(member, rules)
    return rules


def remove(member: str, index: int) -> dict | None:
    """删第 index 条（1 起，编号同 describe 的显示）。越界返回 None。"""
    rules = load(member)
    if not 1 <= int(index) <= len(rules):
        return None
    gone = rules.pop(int(index) - 1)
    _save(member, rules)
    return gone


def match(meta: dict, rules: list[dict]) -> dict | None:
    """这封信命中哪条规则（命中即不播报）；都不中返回 None。

    meta 用 gmail_provider.message_meta 的形状；label 规则只需要其中的 labels，
    故 mail_watch 可在取信头之前先用 history 带回的标签过一遍。
    """
    if not rules:                     # 规则表空 = 全推，不必解析这封信
        return None
    addr = (parseaddr(meta.get("from") or "")[1] or "").lower()
    host = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    subject = (meta.get("subject") or "").lower()
    labels = {str(x).upper() for x in (meta.get("labels") or [])}
    for r in rules:
        kind = r.get("kind")
        val = str(r.get("value") or "")
        if not val:
            continue
        if kind == "sender" and addr and addr == val.lower():
            return r
        if kind == "domain" and (host == val.lower() or host.endswith("." + val.lower())):
            return r
        if kind == "subject" and val.lower() in subject:
            return r
        if kind == "label" and val.upper() in labels:
            return r
    return None


def describe(rules: list[dict]) -> str:
    """给用户看的规则表（编号即 remove 用的序号）。"""
    if not rules:
        return "目前没有忽略规则：每封新邮件都会播报。"
    names = {"sender": "发件人", "domain": "域名", "subject": "主题含", "label": "Gmail 分类"}
    lines = ["当前不播报的邮件（说\"恢复第 N 条\"可撤销）："]
    for i, r in enumerate(rules, 1):
        note = f"（{r['note']}）" if r.get("note") else ""
        lines.append(f"{i}. {names.get(r.get('kind'), r.get('kind'))}：{r.get('value')}{note}")
    return "\n".join(lines)
