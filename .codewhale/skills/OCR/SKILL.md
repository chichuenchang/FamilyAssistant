# OCR

> 光学字符识别 skill。对图片进行文字识别，提取结构化信息。

实现 `ocr.py` 就在本 skill 目录 `.codewhale/skills/OCR/`。两种调用方式，自包含、零外部依赖。

## 命令行调用（user / agent / 任意调用方）

从项目根目录直接跑，无需改 `sys.path`：

```bash
# 纯文字识别
python .codewhale/skills/OCR/ocr.py path/to/image.jpg

# 票据结构化提取（需 DEEPSEEK_API_KEY），输出 JSON
python .codewhale/skills/OCR/ocr.py path/to/receipt.jpg --extract
```

退出码：`0` 成功；`1` 未配置密钥或文件不存在（错误写 stderr）。

## Python 调用（进程内复用，如 Agent）

把本 skill 目录加入 `sys.path`，再 `from ocr import ...`：

```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]   # 调用方在某 skill 目录下时
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "OCR"))

from ocr import ocr_image, ocr_extract, is_available

if not is_available():
    ...  # 提示用户配置腾讯云密钥

text = ocr_image("path/to/image.jpg")
info = ocr_extract("path/to/receipt.jpg")
# → {"amount": 45.0, "currency": "CNY", "date": "2026-06-01", "category": "餐饮", "desc": "午餐"}
```

## API

| 函数 | 返回 | 说明 |
|------|------|------|
| `is_available()` | `bool` | 是否配置了腾讯云密钥 |
| `ocr_image(path)` | `str` / `None` | 通用文字识别；`None` = 不可用或文件不存在 |
| `ocr_extract(path)` | `dict` / `None` | OCR + LLM 结构化票据信息；无 `DEEPSEEK_API_KEY` 时返回 `{"raw_text": ...}` |

## 配置

1. 注册 [腾讯云 OCR](https://console.cloud.tencent.com/ocr/overview)（个人实名认证，1000 次/月免费）
2. 访问 [API 密钥管理](https://console.cloud.tencent.com/cam/capi) 获取 SecretId 和 SecretKey
3. 设置环境变量：
   - `TENCENT_SECRET_ID`
   - `TENCENT_SECRET_KEY`
   - （可选）`DEEPSEEK_API_KEY` — 用于结构化提取

## 依赖

零外部 Python 包（仅用标准库：hashlib、hmac、urllib、base64）。

## 费用

腾讯云通用印刷体识别：1000 次/月免费。超出后 0.15 元/次。
