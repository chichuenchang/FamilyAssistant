# PDF Editor — 设计

> 取代 Form_Filler。用户用一句话说要改什么，Agent 一次调用改完 PDF 发回。
> Form_Filler 的逐字段问答太僵：一条消息一个空，二十个字段二十轮，改一个值要重走会话。

## 取代关系

同一分支内删除 `.codewhale/skills/Form_Filler/`（7 个工具 + prompt 规则 + image route）
与 `tests/test_form_agent.py`。`data/<成员>/forms/*.json` 留在盘上不迁移（无人再读）。

沿用 Form_Filler 已验证的部分：AcroForm 读填（pypdf + NeedAppearances）、
渲染 2x 后 OCR 取坐标、CJK 字体候选链、会话落盘断点续用、路径闸门规则。

## 交互模型

一次调用（one-shot）：Agent 把用户说的全部信息塞进一句 `instruction`，工具内部
用 `llm_client.chat` 把 instruction + 版面坐标编译成 ops，落盘为 plan，应用输出 PDF。

改错了：再调一次，带上同一 `session` 与更正指令（"名字太低了；出生年改 1991"）。
LLM 拿到旧 ops + 新指令，**返回完整新 ops 列表**（不是 diff），重渲染一律从原始 PDF 起。
故修正不叠加伪影，也不需要 undo。

Agent 侧不做逐字段问答：一条消息可以问多项，已知的不问，缺的、含糊的才问。绝不编造值。

## 三档版面来源（自动判定，不问用户）

| kind | 判定 | 坐标来源 | 成本 |
|------|------|---------|------|
| `acroform` | `pypdf` 读出字段 | 字段名 + `/Rect` | 免费、精确 |
| `digital` | 无字段，但 pypdfium2 抽到文字 | pypdfium2 文字 + 字符框 | 免费、精确 |
| `scanned` | 无字段、抽不到文字 | 渲染 2x PNG + 腾讯 OCR 逐行框 | OCR 额度 |

`digital` 档是新增的：Form_Filler 无字段就一律 OCR，明明文字层能直接给精确坐标。

## 输出方式

reportlab 画透明覆盖页 → pypdf `merge_page` 合到**原始页**上。原页字节不动：
无画质损失、文字可选、体积不涨、扫描件同样适用。渲染 PNG 只用于找坐标，不进输出。

`acroform` 的字段值走 pypdf 原生填写（输出仍是可填表单，用户可在任意阅读器里改），
同一次调用里非字段的需求（签名、白底覆盖、页边批注）走覆盖层。

## 目录与 plan

`data/<成员>/pdf_edits/<id>/`，`id` = `YYYYMMDD_HHMMSS_xxxx`：
`plan.json`、`pages/page_N.png`（digital/scanned）、`out.pdf`。

```json
{"id","member","created","source_pdf","kind",
 "pages":[{"page","width","height","scale"}],
 "ops":[...],"history":["指令原文",...],"out":"<data 相对路径>"}
```

## ops 词汇

坐标一律**版面像素、左上原点**：即 `scale=2.0` 的渲染空间。`acroform` 档不渲染 PNG，
其版面空间定义为 point x 2.0、y 自顶（`/Rect` 按此换算）。应用时换回 PDF point 并翻 y。

```json
{"op":"field","name":"Pt1Line1_FamilyName","value":"张"}
{"op":"text","page":0,"x":210,"y":340,"text":"张三","size":null}
{"op":"check","page":0,"x":88,"y":410,"size":18}
{"op":"erase","page":0,"x":200,"y":330,"w":180,"h":24}
{"op":"image","page":5,"x":120,"y":700,"w":160,"h":50,"src":"爸爸/images/sig.png"}
{"op":"page_delete","pages":[2]}
{"op":"page_rotate","page":1,"deg":90}
{"op":"page_reorder","order":[0,2,1]}
{"op":"page_insert","src":"爸爸/documents/x.pdf","after":0}
```

- `size` 省略：有框取框高 0.7，无框取 10pt。放不下逐级缩到 8pt，仍放不下截断加 `…` 并警告。
- `check`：画 `X`（部分字体缺 ✓ 字形）。
- `erase`：不透明白矩形，**不是脱敏 —— 原内容仍在其下**。用到就出警告，Agent 必须转述。
- 未知 `op`：跳过 + `警告: 不支持的操作 <op>`，不崩。新增动词 = 一个 apply 函数 + 一行 schema。

**应用顺序**：内容 ops（先 field 填写，再覆盖层合并）作用于原始页集 → 页级 ops 最后做，
用原始页号。故坐标不会因删页而漂。

## 工具（3 个，取代 7 个）

| 工具 | 参数 | 返回 |
|------|------|------|
| `inspect_pdf` | `file` | kind、页数、这份 PDF 要填什么：字段标签（acroform）或探到的空白标签行（digital/scanned）。`UNTRUSTED_TOOLS`（套围栏）。Agent 靠它知道该向用户问哪些值 |
| `edit_pdf` | `file` 或 `session`，`instruction` | `DOC_TOOLS`：stdout 首行 = `out.pdf` 的 data 相对路径（自动发给用户），随后 `session=<id>` 行与 `警告: …` 行 |
| `pdf_edit_list` | — | 近期会话（id、源文件、最后一条指令），`/clear` 或重启后接着改用 |

`MEMBER_LOCKED` = 全部三个。`CLI_TIMEOUTS`：`pdf-inspect` 120，`pdf-edit` 180（渲染 + OCR + LLM）。

`ORDER = 95`（原 Form_Filler 位次）。

## 代码文件

```
.codewhale/skills/PDF_Editor/
├── SKILL.md
├── agent_tools.py   manifest
├── cli.py           pdf-inspect / pdf-edit / pdf-list
├── pdf_layout.py    档位判定 + 字段 rect + 文字层框 + 渲染/OCR
├── pdf_plan.py      会话与 plan CRUD + instruction→ops（llm_client.chat）
└── pdf_apply.py     应用 ops：pypdf 填字段 + reportlab 覆盖层 + 页级操作
```

`Agent_Runtime/paths.py` 加 `member_pdf_edits_dir(member)`（磁盘布局单一事实来源，见
`FamilyAssistant.md` 配置原则表）。`requirements.txt` 加 `reportlab`、改 pypdf/pypdfium2/Pillow
注释指向本 skill（Pillow 仅 OCR 前的图像处理仍需时保留）。`FamilyAssistant.md` 技能表换行。

## 安全与隔离

- LLM 给的每个路径（`file`、`image.src`、`page_insert.src`）过 `rt.data_path_guard`，
  且须属本成员目录或 Family 共享目录（与 `send_file` 同规则）。
- 会话路径经 `paths.member_pdf_edits_dir(member)`；member 由 `_apply_member` 注入。
- 输出只写会话目录内。

## instruction → ops

`pdf_plan.py` 给 `llm_client.chat` 的内容：kind、各页尺寸、带坐标的版面行/字段清单、
旧 ops（若续用会话）、用户指令。要求只回 JSON ops 数组。
JSON 不可解 → 重试一次 → `[错误] 排版模型没给出可用编辑计划`。

## 依赖缺席

| 缺 | 行为 |
|----|------|
| pypdf | `[错误] pip install pypdf`（全部功能都要） |
| reportlab | 纯 field 编辑仍可用；text/check/erase/image → `[错误] pip install reportlab` |
| pypdfium2 | acroform 仍可用；digital/scanned → 安装提示 |
| 腾讯 OCR | 仅 scanned 档 → 提示配 `TENCENT_SECRET_ID/KEY` |
| 加密 PDF | `[错误] PDF 已加密，无法读取` |

## prompt

取代 Form_Filler 的逐字段问答条目：

> 用户发来 PDF 要填写/修改 → 不知道表里要什么就先 `inspect_pdf` → **一条消息里把缺的值一起问**
> （已知的不要问，含糊的才问，绝不编造值，签名一律要用户给图）→ 把全部信息写进一句 instruction
> 调 `edit_pdf`，PDF 自动发回。用户说改哪儿 → 带同一 `session` 再调 `edit_pdf`，只说更正。
> 结果里的 `警告:` 必须转述给用户。

`IMAGE_ROUTES`：来件 PDF 且用户想填写/修改 → `edit_pdf`，不归档；拿不准问用户。

## 测试

`tests/test_pdf_editor.py`（取代 `test_form_agent.py`）。测试内用 reportlab 造合成 PDF，
stub `llm_client.chat` 返回固定 ops。断言：

- plan 落盘；带 session 的第二次调用从原始 PDF 重渲染（非叠加）
- 文字落到位：pypdfium2 抽出输出页文字含 `张三`
- acroform 档写入真实字段值 + NeedAppearances
- 删页/旋转/重排后的页数与顺序
- 未知 op → 警告且成功退出
- 路径闸门：`data_root` 外、他成员目录 → 拒绝
- 各依赖缺席 → 对应安装提示，不崩
