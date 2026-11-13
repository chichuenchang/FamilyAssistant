# Vision

> `view_image(paths, question)`：图 + 问题发识图模型，回文字。实现 + 选工具规则见 `agent_tools.py`。

- 模型：`llm_client.vision_models()`（顺序见其 docstring），一个无回答换下一个。现仅 `deepseek-flash`（V4.1-Flash，2026-09-10 起原生识图）。
- 实测：400×300 PNG 一张 + 一句问题 = 237 prompt token。DeepSeek 文档：每图 ≤1024 token；>~1300×1300 缩小，<~544×544 放大。
- 格式见 `llm_providers.image_part`。HEIC 拒收。
- OCR/看图互为后备（规则见 `PROMPT_RULES`）：实测两向都切。
- 结果在 `UNTRUSTED_TOOLS`：图中文字可藏注入。
