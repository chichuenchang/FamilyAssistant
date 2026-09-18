# PDF Editor

> 一句指令改 PDF：Agent 调一次 `edit_pdf`，工具内部排版、应用、发回。
> 设计与 ops 词汇：`docs/superpowers/specs/2026-09-17-pdf-editor-design.md`。

## 代码

| 文件 | 职责 |
|------|------|
| `pdf_layout.py` | 版面：AcroForm 字段框 / pdfium 文字行框 / 扫描页 OCR → 视觉像素空间 |
| `pdf_plan.py` | 会话存储；instruction + 版面 → 完整 ops（`llm_client.chat`）；ops 校验 |
| `pdf_apply.py` | ops → PDF：页序列重建 → pypdf 填字段 → reportlab 覆盖层 → 旋转 |
| `cli.py` | `pdf-inspect` / `pdf-edit` / `pdf-list` |
| `agent_tools.py` | manifest：`inspect_pdf` / `edit_pdf` / `pdf_edit_list` |

## 踩过的坑

- pdfium 文字框在**未旋转**用户空间；渲染/OCR 坐标在视觉空间。旋转页靠 `pdf_layout.to_visual` 对齐。
- 旋转页叠字前必须 `page.transfer_rotation_to_content()`，否则字是躺着的。它不搬批注 → 旋转页 + 表单会警告。
- `writer.append(reader, pages=[2,0])` 保序且保留 AcroForm；逐页 `add_page` 会丢表单。
- reportlab 白矩形盖不掉文字层：`erase` 后原文仍可复制，工具每次都警告。
- reportlab 只吃 TrueType 轮廓：Noto CJK（CFF）注册失败，候选链里没放。`.ttc` 要 `subfontIndex=0`。
- 排版是纯文本调用：`llm_client.chat` 在 tools 为空时不带 `tools` 键（空数组 API 是否接受未验证，不赌）。
- 排版用 `PLAN_EFFORT = "high"` 而非 max：`llm_client.chat` 单次超时 120s，至多两次，CLI 超时 300s。
- 续改永远从原始 PDF 重渲染，LLM 回完整 ops，不回 diff。
- 实测排版模型会把字段**标签**当 `name` 填：版面里写成 `name="…"`，`validate_ops` 对唯一标签做兜底映射。
- reportlab 空画布 `save()` 不出页（图片全坏时）→ `merge_page` 越界；`_overlay` 一律先 `showPage()`。
- 自动字号 = 框高 × 0.9，夹在 9–12pt：pdfium 行框贴字形（12pt 字框高约 9pt），多行区框又很高，不夹就出蚂蚁字或巨字。
- DeepSeek flash + `high`：合成两页表单实测一次排版 1.5–2.5s。
- LLM 忘/编会话 id 是常态：`pdf-edit` 兜底接续最近会话，提示走 stderr（stdout 首行是哨兵路径）。

## 会话

`data/<成员>/pdf_edits/<id>/`：`plan.json`（ops + 指令历史）、`layout.json`（版面缓存，续改不重跑 OCR）、`<原名>_edited.pdf`。
同一源 PDF 再次传 `file` 会续用最近会话；`fresh=true` 重开。

## 限制

- 版面文字/OCR 只取前 `MAX_LAYOUT_PAGES`（12）页；页级操作不限。
- 扫描页无 OCR 凭据 → 该页无坐标，排版模型不在该页落字，结果里给提示。
- 加密 PDF（空口令解不开）不支持。
