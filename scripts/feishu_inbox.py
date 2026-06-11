"""
飞书收件箱 — 从飞书机器人拉取未处理的图片消息

用法:
    python scripts/feishu_inbox.py              # 拉取新图片到 receipts/inbox/
    python scripts/feishu_inbox.py --chat-id oc_xxx  # 指定群聊

前置条件:
    1. 飞书开放平台创建自建应用，开通机器人能力
    2. 权限: im:message, im:message:read_as_bot, im:image
    3. 环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET
    4. 把机器人加入一个群，获取 chat_id（见下方说明）

获取 chat_id:
    首次运行时不带 --chat-id，脚本会列出机器人所在的所有群聊，
    找到目标群的 chat_id，后续传入即可。
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import urllib.error
import urllib.parse
import urllib.request

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent


def _receipts_dir() -> Path:
    """票据目录来自 config.json receipts_dir（单一事实来源）；缺失回退 receipts。"""
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return ROOT / (cfg.get("receipts_dir") or "receipts")
    except Exception:
        return ROOT / "receipts"


INBOX_DIR = _receipts_dir() / "inbox"
LAST_ID_FILE = ROOT / "data" / ".feishu_last_id"
CONFIG_FILE = ROOT / "data" / ".feishu_config.json"

FEISHU_HOST = "open.feishu.cn"
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")


def load_config() -> dict:
    """加载本地配置（含 chat_id）。"""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def get_tenant_token() -> str:
    """获取飞书 tenant_access_token。"""
    url = f"https://{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read())
    if resp.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {resp}")
    return resp["tenant_access_token"]


def api_get(token: str, path: str, params: dict = None) -> dict:
    """GET 飞书 API。"""
    url = f"https://{FEISHU_HOST}{path}"
    if params:
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url += "?" + query
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = json.loads(urllib.request.urlopen(req).read())
    if resp.get("code") != 0:
        raise RuntimeError(f"API {path} 失败: {resp}")
    return resp.get("data", {})


def list_chats(token: str) -> list[dict]:
    """列出机器人所在的群聊。"""
    data = api_get(token, "/open-apis/im/v1/chats", {"page_size": 100})
    return data.get("items", [])


def list_messages(token: str, chat_id: str, page_token: str = "") -> dict:
    """获取群聊消息列表。"""
    params = {
        "receive_id_type": "chat_id",
        "receive_id": chat_id,
        "page_size": 50,
    }
    if page_token:
        params["page_token"] = page_token
    return api_get(token, "/open-apis/im/v1/messages", params)


def download_image(token: str, image_key: str, save_path: Path) -> bool:
    """下载图片到本地。"""
    url = f"https://{FEISHU_HOST}/open-apis/im/v1/images/{image_key}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"  下载失败: {e}")
        return False


def get_last_id() -> str:
    """读取上次处理到的消息 ID。"""
    if LAST_ID_FILE.exists():
        return LAST_ID_FILE.read_text(encoding="utf-8").strip()
    return ""


def save_last_id(msg_id: str) -> None:
    LAST_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_ID_FILE.write_text(msg_id, encoding="utf-8")


def process_messages(token: str, chat_id: str) -> int:
    """拉取新消息中的图片，返回下载数量。"""
    last_id = get_last_id()
    count = 0
    page_token = ""
    new_last_id = last_id

    while True:
        data = list_messages(token, chat_id, page_token)
        items = data.get("items", [])
        if not items:
            break

        # 第一次遍历时记录最新消息 ID
        if not new_last_id or new_last_id == last_id:
            new_last_id = items[0].get("message_id", "")

        for msg in items:
            msg_id = msg.get("message_id", "")
            # 遇到已处理过的消息就停
            if last_id and msg_id == last_id:
                items.clear()
                break

            # 只处理图片消息
            if msg.get("msg_type") != "image":
                continue

            image_key = msg.get("content", "{}")
            try:
                content = json.loads(image_key)
                image_key = content.get("image_key", "")
            except json.JSONDecodeError:
                continue

            if not image_key:
                continue

            # 用消息时间戳生成文件名
            ts = int(msg.get("create_time", "0"))
            dt = datetime.fromtimestamp(int(ts) / 1000) if ts else datetime.now()
            fname = dt.strftime("%Y%m%d_%H%M%S") + "_feishu.jpg"
            dest = INBOX_DIR / fname

            print(f"  下载: {fname}")
            if download_image(token, image_key, dest):
                count += 1

        # 检查是否有下一页
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
        if not page_token:
            break

    # 保存最新的消息 ID 作为下次的起点
    if new_last_id:
        save_last_id(new_last_id)

    return count


def main():
    import argparse
    parser = argparse.ArgumentParser(description="飞书收件箱 — 拉取未处理图片")
    parser.add_argument("--chat-id", help="目标群聊 ID")
    parser.add_argument("--list-chats", action="store_true", help="列出机器人所在群聊")
    args = parser.parse_args()

    if not APP_ID or not APP_SECRET:
        print("错误: 请设置环境变量 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        print("  set FEISHU_APP_ID=cli_xxx")
        print("  set FEISHU_APP_SECRET=xxx")
        sys.exit(1)

    token = get_tenant_token()

    # 列出群聊模式
    if args.list_chats:
        chats = list_chats(token)
        if not chats:
            print("机器人不在任何群聊中。请先将机器人加入群。")
            return
        print("机器人所在群聊:")
        for c in chats:
            print(f"  {c['name']:20s}  chat_id: {c['chat_id']}")
        return

    # 拉取消息模式
    cfg = load_config()
    chat_id = args.chat_id or cfg.get("chat_id", "")
    if not chat_id:
        print("未设置 chat_id。请先用 --list-chats 查看群聊，然后:")
        print("  python scripts/feishu_inbox.py --chat-id oc_xxx")
        print("首次使用后会自动保存到 data/.feishu_config.json")
        sys.exit(1)

    # 自动保存 chat_id
    if not cfg.get("chat_id"):
        save_config({"chat_id": chat_id})

    print(f"飞书收件箱 — 检查群聊 {chat_id}")
    count = process_messages(token, chat_id)
    if count == 0:
        print("没有新图片。")
    else:
        print(f"完成，下载 {count} 张图片到 {INBOX_DIR}")
        print("运行 .codewhale/skills/Expense_Tracker/cli.py 处理这些票据。")


if __name__ == "__main__":
    main()
