"""
Family Assistant — Document Keeper 数据模型定义

家庭重要文档（租约/保险/证件等）的 SQLite 表结构。
文档类型 / 存档目录 / 提醒提前天数 全部来自项目根 config.json（单一事实来源）。
本模块导入时读取一次 config.json 并暴露为常量；改这些值只改 config.json
（改后重启进程生效）。config.json 缺失/损坏时用下方应急回退值。

模块名带 doc_ 前缀（不叫 models/db）：Expense_Tracker 已在共享进程
（pytest、传输层 import reminder）占用这两个模块名，避免冲突。
"""

import json
from pathlib import Path

# 文档状态（结构性，固定，不放 config.json）
DOC_STATUSES = ("active", "expired", "archived", "superseded")

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.json"


def _load_config_from(path: Path) -> dict:
    """解析指定 config.json；缺失或损坏时返回空 dict（回退到应急默认）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_config() -> dict:
    return _load_config_from(_CONFIG_PATH)


_cfg = _load_config()

# 应急回退（仅 config.json 缺失/损坏时使用；正常运行值来自 config.json）
_FALLBACK_DOC_TYPES = ["other"]

DOC_TYPES = list(_cfg.get("doc_types") or _FALLBACK_DOC_TYPES)
REMINDER_LEAD_DAYS = int(_cfg.get("reminder_lead_days") or 30)

_ROOT = _CONFIG_PATH.parent
DOCUMENTS_DIR = _ROOT / (_cfg.get("documents_dir") or "documents")
DB_PATH = _ROOT / (_cfg.get("db_path") or "data/ledger.db")

# ---------- SQL DDL ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type      TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    member        TEXT    NOT NULL DEFAULT '',
    issuer        TEXT    DEFAULT '',
    doc_number    TEXT    DEFAULT '',
    issue_date    TEXT    DEFAULT '',
    expiry_date   TEXT    DEFAULT '',
    action_note   TEXT    DEFAULT '',
    remind_days   INTEGER DEFAULT NULL,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    file_path     TEXT    DEFAULT '',
    ocr_text      TEXT    DEFAULT '',
    data          TEXT    NOT NULL DEFAULT '{}',
    status        TEXT    NOT NULL DEFAULT 'active'
                  CHECK(status IN ('active','expired','archived','superseded')),
    notes         TEXT    DEFAULT '',
    created_at    TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_doc_type   ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_doc_expiry ON documents(expiry_date);
CREATE INDEX IF NOT EXISTS idx_doc_member ON documents(member);
CREATE INDEX IF NOT EXISTS idx_doc_status ON documents(status);
"""
