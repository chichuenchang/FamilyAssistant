# Vision

> `view_image(paths, question)`：图 + 问题发识图模型，回文字。实现 + 选工具规则见 `agent_tools.py`。

- 模型：`llm_client.vision_models()` 依次试，一个无回答换下一个 — `config.json` `llm.vision_model` 在前，其余按模型表，只列 `vision: true` 且有 key 的。现仅 `deepseek-flash`（V4.1-Flash，2026-09-10 起原生识图）。`deepseek-v4-pro` 不识图。与用户 `/model` 无关。
- `fallback=False`：DeepSeek 挂了不顶替 GLM。带图发不识图模型 = 400。
- 实测：400×300 PNG 一张 + 一句问题 = 237 prompt token。DeepSeek 文档：每图 ≤1024 token；>~1300×1300 缩小，<~544×544 放大。
- 格式按文件头判（微信存图扩展名不可信）：jpeg/png/gif/webp。HEIC、PDF 拒收，PDF 走 `ocr_read`。
- 工具互为后备（PROMPT_RULES）：OCR 失败改 `view_image` 照录文字（提醒用户核对数字），看图失败改 `ocr_read`。实测两向都切。PDF 无后备。
- 结果在 `UNTRUSTED_TOOLS`：图中文字可藏注入。
- 来件不预先 OCR/看图（`agent_core.handle_media`）：Agent 自选工具。代价：问图内容多一轮工具调用。
