"""HWP3 문서 모델 → JSON / 텍스트 / HTML / CSV.

- JSON: 바이트 구조 정보(문서 정보·글꼴·스타일·추가 정보 블록)와 본문 블록(문단, 표 셀 격자)을 모두 담는다.
- 텍스트: 최상위 문단 순서대로. 표는 [표 Tn r×c] … [/표] 사이에 행마다 탭으로 칸을 나눈다.
- HTML: 사람이 원문과 눈으로 대조하기 위한 단순 렌더링(rowspan/colspan 유지).
- CSV: 표마다 행×열 행렬. 병합 셀 값은 왼쪽 위 칸에만 둔다.
해석하지 못한 글자는 모델 안에서 U+F0000+코드로 보존되고, 내보낼 때 〔U+xxxx〕 대신
'□'(LibreOffice와 같은 표시)로 바꾸고 따로 집계한다(json에는 코드 목록이 남는다).
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
from pathlib import Path

from .charset import PUA_BASE
from .model import Control, Document, Paragraph, Table

_PUA_RE = re.compile("[\U000F0000-\U000FFFFF]")
UNKNOWN_MARK = "□"


def clean(s: str) -> str:
    return _PUA_RE.sub(UNKNOWN_MARK, s)


def unknown_codes_in(s: str) -> list[int]:
    return [ord(c) - PUA_BASE for c in _PUA_RE.findall(s)]


# ---------- 텍스트 ----------

def cell_text(cell, sep: str = "\n") -> str:
    return clean(sep.join(_para_plain(p) for p in cell.paragraphs)).strip("\n")


def _para_plain(p: Paragraph) -> str:
    """문단의 글자. 표·글상자는 셀 글자를 공백으로 이어 붙인다(인라인 근사)."""
    out = []
    for it in p.items:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, Table):
            if it.box_type != "table" or len(it.cells) == 1:
                out.append(" ".join(_para_plain(q) for c in it.cells for q in c.paragraphs))
        else:
            out.append(it.text())
    return "".join(out)


def table_rows_text(t: Table) -> list[list[str]]:
    m = [["" for _ in range(t.n_cols)] for _ in range(t.n_rows)]
    for c in t.cells:
        m[c.row][c.col] = cell_text(c, " / ").replace("\t", " ")
    return m


def render_text(doc: Document) -> str:
    lines: list[str] = []

    def para(p: Paragraph, indent: str = "") -> None:
        buf: list[str] = []
        pending: list[str] = []
        for it in p.items:
            if isinstance(it, str):
                buf.append(it)
            elif isinstance(it, Table) and it.box_type == "table" and len(it.cells) > 1:
                pending.append(f"{indent}[표 T{it.table_id} {it.n_rows}x{it.n_cols} 쪽={it.page}]")
                for row in table_rows_text(it):
                    pending.append(indent + "\t".join(row))
                for q in it.caption:
                    pending.append(indent + "[캡션] " + clean(_para_plain(q)))
                pending.append(f"{indent}[/표 T{it.table_id}]")
            elif isinstance(it, Table):
                inner = " ".join(clean(_para_plain(q)) for c in it.cells for q in c.paragraphs).strip()
                if inner:
                    buf.append(f"[{ {'textbox': '글상자', 'equation': '수식', 'button': '버튼'}.get(it.box_type, it.box_type) }: {inner}]")
            elif isinstance(it, Control):
                if it.code in (15, 16, 17):
                    label = {15: "숨은 설명", 16: "머리말/꼬리말", 17: "각주"}[it.code]
                    inner = " ".join(clean(_para_plain(q)) for ch in it.children for q in ch).strip()
                    if inner:
                        buf.append(f"[{label}: {inner}]")
                elif it.code == 11:
                    inner = " ".join(clean(_para_plain(q)) for ch in it.children for q in ch).strip()
                    label = it.fields.get("path", "") or ("그리기" if it.fields.get("pictype") == 3 else "")
                    buf.append(f"[그림 {label}: {inner}]" if inner else f"[그림 {label}]")
                else:
                    buf.append(it.text())
        lines.append(indent + clean("".join(buf)))
        lines.extend(pending)

    for p in doc.paragraphs:
        para(p)
    return "\n".join(lines) + "\n"


def linearize_like_preview(doc: Document) -> str:
    """한글이 저장한 '미리보기 텍스트'와 비교하기 위한 선형화.

    관찰한 미리보기 형식: 문단마다 CR LF, 표·글상자는 그 자리에 셀마다 <셀 글자>,
    셀 안 여러 문단은 공백으로 잇는다. 비교 시 공백 차이는 무시한다.
    """
    out: list[str] = []

    def para(p: Paragraph) -> str:
        s = []
        for it in p.items:
            if isinstance(it, str):
                s.append(it)
            elif isinstance(it, Table):
                for c in it.cells:
                    s.append("<" + " ".join(para(q) for q in c.paragraphs) + ">")
            else:
                s.append(it.text())
        return "".join(s)

    for p in doc.paragraphs:
        out.append(para(p) + "\r\n")
    return "".join(out)


# ---------- JSON ----------

def _para_json(p: Paragraph) -> dict:
    d = {
        "offset": p.offset,
        "page": p.page,
        "style": p.style,
        "text": clean(_para_plain(p)),
    }
    unk = unknown_codes_in(_para_plain(p))
    if unk:
        d["unknown_codes"] = [f"0x{c:04X}" for c in unk]
    ctrls = [c for c in p.controls() if not isinstance(c, Table)]
    if ctrls:
        d["controls"] = [_ctrl_json(c) for c in ctrls]
    tables = [c for c in p.controls() if isinstance(c, Table)]
    if tables:
        d["boxes"] = [_table_json(t) for t in tables]
    return d


def _ctrl_json(c: Control) -> dict:
    d = {"code": c.code, "name": c.name, "offset": c.offset}
    keep = {k: v for k, v in c.fields.items() if k not in ("data",) and not isinstance(v, bytes)}
    if keep:
        d["fields"] = keep
    if c.children:
        d["paragraph_lists"] = [[_para_json(q) for q in ch] for ch in c.children]
    return d


def _table_json(t: Table) -> dict:
    return {
        "table_id": t.table_id,
        "box_type": t.box_type,
        "offset": t.offset,
        "page": t.page,
        "depth": t.depth,
        "n_rows": t.n_rows,
        "n_cols": t.n_cols,
        "col_edges_hunit": t.col_edges,
        "row_edges_hunit": t.row_edges,
        "grid_problems": t.grid_problems(),
        "box": {k: v for k, v in t.fields.items()},
        "cells": [
            {
                "index": c.index,
                "row": c.row,
                "col": c.col,
                "rowspan": c.rowspan,
                "colspan": c.colspan,
                "x": c.x, "y": c.y, "w": c.w, "h": c.h,
                "text": cell_text(c),
                "paragraphs": [_para_json(q) for q in c.paragraphs],
                "raw": c.raw,
            }
            for c in t.cells
        ],
        "caption": [_para_json(q) for q in t.caption],
    }


def to_json(doc: Document, source_bytes: bytes | None = None) -> dict:
    ctrl_counts: dict[str, int] = {}
    n_all = 0
    for _, p in doc.iter_paragraphs():
        n_all += 1
        for c in p.controls():
            key = c.name if not isinstance(c, Table) else f"box:{c.box_type}"
            ctrl_counts[key] = ctrl_counts.get(key, 0) + 1
    src = {"path": doc.path, "size": doc.size}
    if source_bytes is not None:
        src["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    return {
        "schema": "hwp3-document/1",
        "source": src,
        "format": {
            "signature": "HWP Document File V3.00",
            "compressed": bool(doc.docinfo["compressed"]),
            "body_size": doc.body_size,
            "body_crc_ok": doc.body_crc_ok,
            "has_layout": doc.has_layout,
        },
        "docinfo": doc.docinfo,
        "summary": {k: clean(v) for k, v in doc.summary.items()},
        "fonts": doc.fonts,
        "styles": [s["name"] for s in doc.styles],
        "tags": [{k: (clean(v) if isinstance(v, str) else v) for k, v in t.items()} for t in doc.tags],
        "tail_tags": [{k: (clean(v) if isinstance(v, str) else v) for k, v in t.items()} for t in doc.tail_tags],
        "stats": {
            "paragraphs_top": len(doc.paragraphs),
            "paragraphs_all": n_all,
            "controls": dict(sorted(ctrl_counts.items())),
            "unknown_chars": {f"0x{k:04X}": v for k, v in sorted(doc.unknown_chars.items())},
        },
        "warnings": doc.warnings,
        "body": [_para_json(p) for p in doc.paragraphs],
    }


# ---------- HTML ----------

def render_html(doc: Document, title: str = "") -> str:
    e = html.escape
    out = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        f"<title>{e(title or doc.summary.get('title') or 'HWP3')}</title>",
        (
            "<style>body{font-family:sans-serif;max-width:1100px;margin:1em auto;padding:0 16px}"
            "table{border-collapse:collapse;margin:.5em 0}td{border:1px solid #999;padding:2px 4px;font-size:13px;vertical-align:top}"
            "p{margin:.15em 0;white-space:pre-wrap}.meta{color:#666;font-size:12px}.unk{color:#c00}</style></head><body>"
        ),
        f"<p class='meta'>{e(str(doc.path))} · 문단 {len(doc.paragraphs)} · 표 {sum(1 for _ in doc.iter_tables())}</p>",
    ]

    def esc(s: str) -> str:
        return _PUA_RE.sub(lambda m: f"<span class='unk' title='0x{ord(m.group()) - PUA_BASE:04X}'>{UNKNOWN_MARK}</span>", e(s))

    def para(p: Paragraph) -> str:
        parts = []
        tables = []
        for it in p.items:
            if isinstance(it, str):
                parts.append(esc(it))
            elif isinstance(it, Table):
                tables.append(table(it))
            elif isinstance(it, Control):
                if it.code in (15, 16, 17):
                    inner = " ".join(_para_plain(q) for ch in it.children for q in ch)
                    parts.append(f"<span class='meta'>[{esc(inner)}]</span>")
                else:
                    parts.append(esc(it.text()))
        page = f" data-page='{p.page}'" if p.page is not None else ""
        return f"<p{page}>{''.join(parts)}</p>" + "".join(tables)

    def table(t: Table) -> str:
        rows: dict[int, list] = {}
        for c in sorted(t.cells, key=lambda c: (c.row, c.col)):
            rows.setdefault(c.row, []).append(c)
        h = [f"<table data-table-id='{t.table_id}' data-kind='{t.box_type}'>"]
        for r in range(t.n_rows):
            h.append("<tr>")
            for c in rows.get(r, []):
                span = (f" rowspan='{c.rowspan}'" if c.rowspan > 1 else "") + (f" colspan='{c.colspan}'" if c.colspan > 1 else "")
                h.append(f"<td{span}>" + "".join(para(q) for q in c.paragraphs) + "</td>")
            h.append("</tr>")
        h.append("</table>")
        if t.caption:
            h.append("<div class='meta'>" + "".join(para(q) for q in t.caption) + "</div>")
        return "".join(h)

    for p in doc.paragraphs:
        out.append(para(p))
    out.append("</body></html>")
    return "\n".join(out)


# ---------- CSV ----------

def table_csv(t: Table) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    m = [["" for _ in range(t.n_cols)] for _ in range(t.n_rows)]
    for c in t.cells:
        m[c.row][c.col] = cell_text(c)
    w.writerows(m)
    return buf.getvalue()


def write_all(doc: Document, out_dir: str | Path, source_bytes: bytes | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "document.json").write_text(json.dumps(to_json(doc, source_bytes), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "text.txt").write_text(render_text(doc), encoding="utf-8")
    (out / "document.html").write_text(render_html(doc), encoding="utf-8")
    if doc.embedded_images:
        idir = out / "images"
        idir.mkdir(exist_ok=True)
        for name, blob in doc.embedded_images.items():
            safe = re.sub(r"[^0-9A-Za-z_.$-]", "_", name) or "image"
            (idir / safe).write_bytes(blob)
    tdir = out / "tables"
    tdir.mkdir(exist_ok=True)
    n = 0
    for t in doc.iter_tables():
        if t.box_type == "table" and len(t.cells) > 1:
            (tdir / f"T{t.table_id:03d}.csv").write_text(table_csv(t), encoding="utf-8")
            n += 1
    return {"tables_csv": n}
