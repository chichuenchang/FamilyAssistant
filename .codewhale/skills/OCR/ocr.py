"""
OCR 模块 — 腾讯云 OCR 文字识别封装。

免费额度：1000 次/月（个人实名认证即可）
官网申请：https://console.cloud.tencent.com/ocr/overview

环境变量:
    TENCENT_SECRET_ID      — 腾讯云 SecretId
    TENCENT_SECRET_KEY     — 腾讯云 SecretKey

用法:
    from ocr import ocr_image   # 调用方需把本 skill 目录加入 sys.path
    text = ocr_image("data/Family/receipts/2026-06/photo.jpg")
    print(text)  # → "午餐 45元 2026-06-01"

    from ocr import ocr_extract
    info = ocr_extract("data/Family/receipts/2026-06/photo.jpg")
    # → {"currency": "CAD", "transactions": [{"amount": 45.0,
    #     "date": "2026-06-01", "category": "餐饮", "desc": "麦当劳"}, ...]}
    print(info)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Windows 控制台编码容错（命令行直接调用时）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "")
SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "")

OCR_ENDPOINT = "ocr.tencentcloudapi.com"
OCR_SERVICE = "ocr"
OCR_VERSION = "2018-11-19"
OCR_ACTION = "GeneralBasicOCR"
OCR_REGION = "ap-guangzhou"
MAX_PDF_PAGES = 20   # PDF 逐页 OCR 上限（腾讯免费额度 1000 次/月，防超大 PDF 烧额度）


# ── TC3-HMAC-SHA256 签名 ───────────────────────────────────

def _sign(secret_key: str, date: str, service: str, string_to_sign: str) -> bytes:
    """TC3-HMAC-SHA256 签名算法。"""
    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    k_service = _hmac(k_date, service)
    k_signing = _hmac(k_service, "tc3_request")
    return _hmac(k_signing, string_to_sign)


def _call_ocr(payload: dict) -> Optional[dict]:
    """调用腾讯云 OCR API，返回 JSON 响应体。"""
    if not SECRET_ID or not SECRET_KEY:
        return None

    import urllib.request

    body = json.dumps(payload)
    timestamp = int(datetime.now(timezone.utc).timestamp())
    date_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    # 1. 规范请求串
    http_method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    ct = "application/json; charset=utf-8"
    canonical_headers = (
        f"content-type:{ct}\n"
        f"host:{OCR_ENDPOINT}\n"
        f"x-tc-action:{OCR_ACTION.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = (
        f"{http_method}\n{canonical_uri}\n{canonical_querystring}\n"
        f"{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    )

    # 2. 待签名字符串
    algorithm = "TC3-HMAC-SHA256"
    credential_scope = f"{date_str}/{OCR_SERVICE}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"

    # 3. 签名
    signature = _sign(SECRET_KEY, date_str, OCR_SERVICE, string_to_sign).hex()

    # 4. Authorization
    authorization = (
        f"{algorithm} Credential={SECRET_ID}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    url = f"https://{OCR_ENDPOINT}"
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers={
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": OCR_ENDPOINT,
        "X-TC-Action": OCR_ACTION,
        "X-TC-Version": OCR_VERSION,
        "X-TC-Region": OCR_REGION,
        "X-TC-Timestamp": str(timestamp),
    })

    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if "Response" in resp and "Error" not in resp["Response"]:
            return resp["Response"]
        err = resp.get("Response", {}).get("Error", {})
        print(f"[ocr] 腾讯云错误: {err.get('Code')} - {err.get('Message')}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[ocr] API 调用失败: {e}", file=sys.stderr)
        return None


# ── 通用 OCR ────────────────────────────────────────────────

def ocr_image(image_path: str) -> Optional[str]:
    """通用文字识别。图片直接 OCR；.pdf 用腾讯 IsPdf 逐页 OCR 后拼接。

    返回 None = OCR 不可用 / 首页失败；空文档返回 ""。
    """
    p = Path(image_path)
    if not p.exists():
        return None
    b64 = base64.b64encode(p.read_bytes()).decode()

    if p.suffix.lower() == ".pdf":
        pages: list[str] = []
        for n in range(1, MAX_PDF_PAGES + 1):
            data = _call_ocr({"ImageBase64": b64, "IsPdf": True,
                              "PdfPageNumber": n, "LanguageType": "zh"})
            if not data:
                if n == 1:
                    return None          # 首页失败 = OCR 不可用 / PDF 不可读
                break                    # 后续页无数据 = 文档到此结束
            words = [d["DetectedText"] for d in data.get("TextDetections", [])
                     if d.get("DetectedText")]
            if words:
                pages.append("\n".join(words))
        return "\n".join(pages) if pages else ""

    data = _call_ocr({"ImageBase64": b64, "LanguageType": "zh"})
    if not data:
        return None
    words = [d["DetectedText"] for d in data.get("TextDetections", [])
             if d.get("DetectedText")]
    return "\n".join(words) if words else ""


# ── 票据结构化提取 ──────────────────────────────────────────

def ocr_extract(image_path: str) -> Optional[dict]:
    """对票据/账单图片 OCR 后提取结构化「逐笔交易」。

    先调腾讯云 OCR 获取原始文字，再调 LLM 解析为 JSON。
    重点是逐笔消费/收支，而非账单总额/应还款额。
    返回:
        {"currency": "CAD",
         "transactions": [
            {"amount": 65.51, "date": "2026-05-01",
             "category": "汽油", "desc": "CENTEX STRATHCONA"},
            ...
         ]}
    无 DEEPSEEK_API_KEY 时返回 {"raw_text": ...}；OCR 失败返回 None。
    """
    raw = ocr_image(image_path)
    if not raw:
        return None

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {"raw_text": raw}

    prompt = (
        "从以下 OCR 识别结果中提取所有「逐笔交易」。严格返回 JSON，不要额外文字。\n"
        "重点：若是银行/信用卡/支付App账单或流水，请逐笔提取每条交易；"
        "绝不要返回账单总额、应还款额、最低还款额、已还款额等汇总数字——只要明细行。\n"
        "单张小票通常只有一笔，返回单元素数组即可。\n"
        "格式:\n"
        "{\"currency\": \"币种，从单据推断(如 CNY/USD/CAD)\", "
        "\"transactions\": [{\"amount\": 浮点数(正数), "
        "\"date\": \"YYYY-MM-DD\", \"category\": \"分类\", "
        "\"desc\": \"商家或描述\"}]}\n"
        "金额或日期无法确定的行直接跳过，不要瞎填。\n\n"
        f"OCR结果:\n{raw}"
    )

    import urllib.request
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    body = json.dumps({
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "你是逐笔交易提取器，从票据/账单里抽取每一笔消费收支。只输出JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        # deepseek-v4-flash 是推理模型，reasoning 占用 completion 预算。
        # 单张票据约 ~480 token；多页账单逐笔提取（实测 22 笔需 ~5300）。
        # DeepSeek 价格低，预算给足，避免逐笔交易被截断或 content 为空。
        "max_tokens": 10000,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        # 多页账单逐笔推理可能耗时数十秒，超时给足。
        resp = json.loads(urllib.request.urlopen(req, timeout=90).read())
        content = resp["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        print(f"[ocr] LLM 解析失败: {e}", file=sys.stderr)

    return {"raw_text": raw}


# ── 检查可用性 ──────────────────────────────────────────────

def is_available() -> bool:
    """检查 OCR 是否配置了就绪。"""
    return bool(SECRET_ID and SECRET_KEY)


# ── 命令行入口 ──────────────────────────────────────────────
# 让 user / agent / 任意调用方都能直接跑：
#   python .codewhale/skills/OCR/ocr.py <图片路径>            → 纯文字
#   python .codewhale/skills/OCR/ocr.py <图片路径> --extract  → 结构化 JSON

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="OCR — 图片文字识别 / 票据结构化提取")
    parser.add_argument("image", help="图片路径")
    parser.add_argument("--extract", action="store_true",
                        help="结构化提取票据信息（需 DEEPSEEK_API_KEY），输出 JSON")
    args = parser.parse_args()

    if not is_available():
        print("OCR 未配置：请设置环境变量 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY",
              file=sys.stderr)
        return 1

    if not Path(args.image).exists():
        print(f"文件不存在: {args.image}", file=sys.stderr)
        return 1

    if args.extract:
        info = ocr_extract(args.image)
        print(json.dumps(info, ensure_ascii=False, indent=2) if info else "[未识别到文字]")
    else:
        text = ocr_image(args.image)
        print(text if text else "[未识别到文字]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
