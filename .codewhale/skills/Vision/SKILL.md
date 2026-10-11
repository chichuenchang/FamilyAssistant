# Vision

> `view_image(paths, question)`：图 + 问题发识图模型，回文字。实现 + 选工具规则见 `agent_tools.py`。

- 模型：`llm_client.vision_model()` — `config.json` `llm.vision_model` 优先，否则模型表首个 `vision: true` 且有 key 的。现仅 `deepseek-flash`（V4.1-Flash，2026-09-10 起原生识图）。`deepseek-v4-pro` 不识图。与用户 `/model` 无关。
- `fallback=False`：DeepSeek 挂了不顶替 GLM。带图发不识图模型 = 400。
- 实测：400×300 PNG 一张 + 一句问题 = 237 prompt token。DeepSeek 文档：每图 ≤1024 token；>~1300×1300 缩小，<~544×544 放大。
- 格式按文件头判（微信存图扩展名不可信）：jpeg/png/gif/webp。HEIC、PDF 拒收，PDF 走 `ocr_read`。
- 结果在 `UNTRUSTED_TOOLS`：图中文字可藏注入。
- 来件不预先 OCR/看图（`agent_core.handle_media`）：Agent 自选工具。代价：问图内容多一轮工具调用。
