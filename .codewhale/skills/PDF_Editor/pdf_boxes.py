"""
Family Assistant — 表格空格 / 勾选框定位（PDF_Editor）。

页渲染成灰度图 → 亮像素连通域（numpy 行程 + 并查集，不靠 scipy）→ 四边都是亮像素的
闭合矩形 = 格子；勾选框另取文字层的方框字形（⬜ ❑ 是字，不是线）。矢量表格、扫描件同一套。
坐标同 pdf_layout：视觉空间像素。

目标三类：check（小方框）/ blank（空格）/ cell（格内有标签，free = 剩余空白）。
排版 LLM 只挑 id，坐标由这里给（为何见 SKILL.md 踩过的坑）。
"""

from __future__ import annotations

LIGHT = 200            # 灰度 > 此值算亮（浅灰底色的表头格仍算亮）
MIN_SIDE = 10          # 像素；更小的是字内空洞
CHECK_MAX = 48         # 两边都 ≤ 此值且近方形 = 勾选框
EDGE_FILL = 0.9        # 内缩 EDGE_INSET 的四条边上属本连通域的比例下限 = 矩形
EDGE_INSET = 2         # 像素；避开抗锯齿的边线
MIN_FREE = (60, 20)    # 标签格剩余空白至少 w×h 才可写
RIGHT_PREF = 150       # 标签右侧空白够这么宽就写右侧（下方常只剩一条缝）
NEAR = {"above": 160, "below": 60, "left": 420, "right": 420}   # 邻近标签最大距离
BLOCK_GAP = 14         # 表头多行之间的行距上限
LABEL_MAX = 80


def _runs(mask) -> list:
    """每行的亮像素行程 → [(y, x0, x1)]，x1 不含。"""
    import numpy as np
    out = []
    pad = np.zeros((mask.shape[0], 1), dtype=bool)
    edges = np.diff(np.hstack([pad, mask, pad]).astype(np.int8), axis=1)
    for y in range(mask.shape[0]):
        starts = np.flatnonzero(edges[y] == 1)
        ends = np.flatnonzero(edges[y] == -1)
        out.extend((y, int(a), int(b)) for a, b in zip(starts, ends))
    return out


def components(mask) -> tuple:
    """亮像素 4 连通标记 → (labels 数组, {id: [x0, y0, x1, y1]})，id 从 1 起。"""
    import numpy as np
    runs = _runs(mask)
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    prev, cur, cur_y = [], [], -1
    for i, (y, a, b) in enumerate(runs):
        if y != cur_y:
            prev, cur, cur_y = (cur if y == cur_y + 1 else []), [], y
        for j in prev:                      # 上一行与本行列区间重叠 → 并
            _, pa, pb = runs[j]
            if pa < b and a < pb:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
        cur.append(i)
    labels = np.zeros(mask.shape, dtype=np.int32)
    ids, stats = {}, {}
    for i, (y, a, b) in enumerate(runs):
        root = find(i)
        k = ids.setdefault(root, len(ids) + 1)
        labels[y, a:b] = k
        s = stats.setdefault(k, [a, y, b, y + 1])      # runs 按 y 递增：y0 定于首段，y1 = 当前行
        s[0], s[2], s[3] = min(s[0], a), max(s[2], b), y + 1
    return labels, stats


def _is_rect(labels, k, x0, y0, x1, y1) -> bool:
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        return False
    i = EDGE_INSET
    edges = (labels[y0 + i, x0 + i:x1 - i], labels[y1 - 1 - i, x0 + i:x1 - i],
             labels[y0 + i:y1 - i, x0 + i], labels[y0 + i:y1 - i, x1 - 1 - i])
    return all(e.size and (e == k).mean() >= EDGE_FILL for e in edges)


def rects(gray) -> list:
    """灰度图（numpy uint8）→ 闭合矩形 [{x,y,w,h}]，碰页边的（页面底色）除外。"""
    labels, stats = components(gray > LIGHT)
    hgt, wid = gray.shape
    out = []
    for k, (x0, y0, x1, y1) in stats.items():
        if x0 == 0 or y0 == 0 or x1 == wid or y1 == hgt:
            continue
        if _is_rect(labels, k, x0, y0, x1, y1):
            out.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})
    return out


def _inside(line, r) -> bool:
    cx, cy = line["x"] + line["w"] / 2, line["y"] + line["h"] / 2
    return r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"]


def _contains(outer, inner, pad=0) -> bool:
    """outer 四周放大 pad 后整个包住 inner。"""
    return (outer is not inner and outer["x"] - pad <= inner["x"] and outer["y"] - pad <= inner["y"]
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + pad
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + pad)


def _free(r, texts) -> dict | None:
    """标签格里能写字的最大空白：标签右侧（同高）或标签下方（同宽），取面积大者。"""
    right = max(l["x"] + l["w"] for l in texts) + 8
    bottom = max(l["y"] + l["h"] for l in texts) + 4
    cands = [{"x": right, "y": r["y"], "w": r["x"] + r["w"] - 4 - right, "h": r["h"]},
             {"x": r["x"] + 6, "y": bottom, "w": r["w"] - 12, "h": r["y"] + r["h"] - 2 - bottom}]
    ok = [c for c in cands if c["w"] >= MIN_FREE[0] and c["h"] >= MIN_FREE[1]]
    if ok and ok[0] is cands[0] and cands[0]["w"] >= RIGHT_PREF:
        return cands[0]
    return max(ok, key=lambda c: c["w"] * c["h"]) if ok else None


def _overlap(a, b, axis="x") -> float:
    """a、b 在 x 或 y 轴上的重叠长度；≤ 0 = 不重叠。"""
    size = "w" if axis == "x" else "h"
    return min(a[axis] + a[size], b[axis] + b[size]) - max(a[axis], b[axis])


def _stacked(a, b) -> bool:
    """上下贴邻（≤ 6px）且横向重叠过半：表头/表注与它的空格。"""
    gap = max(b["y"] - (a["y"] + a["h"]), a["y"] - (b["y"] + b["h"]))
    return gap <= 6 and _overlap(a, b) >= 0.5 * min(a["w"], b["w"])


def _above(r, lines) -> str:
    """正上方的连续文字块（多行表头拼成一句）：最近一行起往上，行距 ≤ BLOCK_GAP 才连。"""
    col = sorted((l for l in lines if _overlap(l, r) > 0 and l["y"] + l["h"] <= r["y"] + 2),
                 key=lambda l: -(l["y"] + l["h"]))
    if not col or r["y"] - (col[0]["y"] + col[0]["h"]) > NEAR["above"]:
        return ""
    block = [col[0]]
    for l in col[1:]:
        if block[-1]["y"] - (l["y"] + l["h"]) > BLOCK_GAP:
            break
        block.append(l)
    return " ".join(l["text"] for l in reversed(block))[:LABEL_MAX]


def _near(r, lines) -> dict:
    """邻近标签：上方文字块；下、左、右（行/列重叠）各取最近一行。"""
    best: dict = {}

    def keep(side, dist, text):
        if 0 <= dist <= NEAR[side] and (side not in best or dist < best[side][0]):
            best[side] = (dist, text[:LABEL_MAX])

    for l in lines:
        h_overlap, v_overlap = _overlap(l, r) > 0, _overlap(l, r, "y") > 0
        if h_overlap and l["y"] >= r["y"] + r["h"] - 2:
            keep("below", l["y"] - (r["y"] + r["h"]), l["text"])
        if v_overlap and l["x"] >= r["x"] + r["w"] - 2:
            keep("right", l["x"] - (r["x"] + r["w"]), l["text"])
        if v_overlap and l["x"] + l["w"] <= r["x"] + 2:
            keep("left", r["x"] - (l["x"] + l["w"]), l["text"])
    above = _above(r, lines)
    return {**({"above": above} if above else {}), **{k: t for k, (_, t) in best.items()}}


def _in_text(r, lines) -> bool:
    """字内空洞（O/D/口 里的白）：整个落在某文字行框里。"""
    return any(_contains(l, r, pad=2) for l in lines)


def targets(page: int, gray, lines: list, checks: list = ()) -> list:
    """一页的可填目标。lines = 本页版面文字行，checks = 勾选框字形框（均视觉像素）。
    含别的格子或勾选框的大格是容器（选项区），不当目标。"""
    boxes = [{**c, "glyph": True} for c in checks]
    boxes += [r for r in rects(gray) if not _in_text(r, lines)
              and not any(_inside(r, c) for c in checks)]      # 字形方框渲染出的白芯
    boxes = [b for b in boxes if b.get("glyph") or not any(_contains(b, o) for o in boxes)]
    out = []
    for r in sorted(boxes, key=lambda r: (r["y"], r["x"])):
        r = {k: r[k] for k in ("x", "y", "w", "h")}
        texts = [] if r["w"] <= CHECK_MAX else [l for l in lines if _inside(l, r)]
        t = {"page": page, **r}
        if texts:
            free = _free(r, texts)
            if free is None:
                continue
            t.update(kind="cell", label=" ".join(l["text"] for l in texts)[:LABEL_MAX], free=free)
        else:
            square = max(r["w"], r["h"]) <= CHECK_MAX and 0.7 <= r["w"] / r["h"] <= 1.4
            t.update(kind="check" if square else "blank", near=_near(r, lines))
        out.append(t)
    blanks = [t for t in out if t["kind"] == "blank"]
    out = [t for t in out if t["kind"] != "cell" or not any(_stacked(t, b) for b in blanks)]
    return [{"id": f"p{page}-{i}", **t} for i, t in enumerate(out, 1)]   # 删表头后才编号


def page_targets(pdf_path, geometry, lines: dict, checks: dict) -> dict:
    """各页渲染成灰度 → {页: 目标}。渲染同 pdf_layout.SCALE，已计 /Rotate = 视觉空间。"""
    import numpy as np
    import pypdfium2 as pdfium
    out = {}
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for g in geometry:
            key = str(g["page"])
            img = pdf[g["page"]].render(scale=g["scale"]).to_pil().convert("L")
            found = targets(g["page"], np.asarray(img), lines.get(key, []), checks.get(key, []))
            if found:
                out[key] = found
    finally:
        pdf.close()
    return out
