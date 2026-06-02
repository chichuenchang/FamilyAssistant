# OCR

> 光学字符识别 skill。对图片进行文字识别，提取结构化信息。

## 适用场景

- 票据/发票/收据 OCR → 提取金额、日期、商家
- 截图文字提取
- 名片识别
- 任何需要"看图识字"的场景

## 调用方式

```python
from scripts.ocr import ocr_image, ocr_extract, is_available

if not is_available():
    # 提示用户配置腾讯云密钥

text = ocr_image("path/to/image.jpg")
info = ocr_extract("path/to/receipt.jpg")
# → {"amount": 45.0, "currency": "CNY", "date": "2026-06-01", "category": "餐饮", "desc": "午餐"}
```

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
