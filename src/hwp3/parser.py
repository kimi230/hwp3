"""HWP 3.x 바이너리 파서.

파일 구성(규격 순서):
  1. 파일 인식 정보 30바이트  "HWP Document File V3.00 \\x1a\\x01\\x02\\x03\\x04\\x05"
  2. 문서 정보 128바이트      (압축 여부 @124, 정보 블록 길이 @126)
  3. 문서 요약 1008바이트     (hchar 56자 × 9항목)
  4. 정보 블록               (문서 정보에 적힌 길이)
  5. 본문 — 압축 문서면 raw deflate + gzip 꼬리(CRC32, 원래 크기)
       글꼴 이름(7개 언어) / 스타일 / 문단 리스트 / 추가 정보 블록 #1 (tag 0으로 끝)
  6. 압축 영역 밖 추가 정보 블록 #2 (tag 0x80000000으로 끝): 미리보기 이미지·글 등

문단 리스트는 글자 수(nch)가 0인 문단으로 끝난다. 표·각주·머리말 등은
문단 안의 특수 문자(코드 0~31)가 하위 문단 리스트를 품는 재귀 구조다.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from .charset import decode_hchar, unknown_placeholder
from .model import BOX_TYPES, Cell, Control, Document, Paragraph, Table

SIGNATURE = b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05"
SIG_LEN = 30
DOCINFO_LEN = 128
SUMMARY_LEN = 1008
N_LANG = 7
FONT_NAME_LEN = 40
STYLE_NAME_LEN = 20
CHARSHAPE_LEN = 31
PARASHAPE_LEN = 187
LINEINFO_LEN = 14
CELL_LEN = 27
BOX_INFO_LEN = 84  # 표·선 정보 길이(규격 표 40, 47). 그림은 348 + 추가 정보 길이(표 44)

SUMMARY_FIELDS = ("title", "subject", "author", "date", "keyword1", "keyword2", "etc1", "etc2", "etc3")

# 문단 글자 수(nch)에 더해지는 단위. "hchar, dword, hchar" 머리를 가진 가변 길이
# 특수 문자는 머리 8바이트(=4 hchar)만 센다. 고정 길이는 전체 바이트/2.
WSIZE = {
    0: 1, 1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 42,
    8: 48, 9: 4, 10: 4, 11: 4, 12: 4, 13: 1, 14: 4, 15: 4,
    16: 4, 17: 4, 18: 4, 19: 4, 20: 4, 21: 4, 22: 12, 23: 5,
    24: 3, 25: 3, 26: 123, 27: 4, 28: 32, 29: 4, 30: 2, 31: 2,
}
# 고정 길이 특수 문자의 전체 바이트 수(맨 앞·맨 뒤 hchar가 같은 코드).
FIXED_SIZE = {7: 84, 8: 96, 9: 8, 18: 8, 19: 8, 20: 8, 21: 8, 22: 24, 23: 10, 24: 6, 25: 6, 26: 246, 28: 64, 30: 4, 31: 4}
# "hchar, dword 길이, hchar, 길이만큼 자료" 형식
SKIP_BLOCK = {1, 2, 3, 4, 5, 6, 12, 27, 29}

TAG_END_COMPRESSED = 0
TAG_END_UNCOMPRESSED = 0x80000000
TAG_NAMES = {
    1: "embedded_picture",
    2: "ole_object",
    3: "hypertext",
    4: "presentation",
    5: "reserved5",
    6: "background_image",
    0x100: "table_extension",
    0x101: "field_name",
    0x80000001: "preview_image",
    0x80000002: "preview_text",
}

MAX_DEPTH = 32


class HWP3Error(Exception):
    pass


def is_hwp3(data: bytes) -> bool:
    return data[:SIG_LEN] == SIGNATURE


class _R:
    """경계 검사하는 리틀엔디언 읽개."""

    __slots__ = ("d", "end", "p")

    def __init__(self, data: bytes, pos: int = 0, end: int | None = None):
        self.d = data
        self.p = pos
        self.end = len(data) if end is None else end

    def need(self, n: int) -> None:
        if n < 0 or self.p + n > self.end:
            raise HWP3Error(f"본문 끝을 넘어 읽음: offset={self.p} need={n} end={self.end}")

    def u8(self) -> int:
        self.need(1)
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self) -> int:
        self.need(2)
        v = self.d[self.p] | (self.d[self.p + 1] << 8)
        self.p += 2
        return v

    def i16(self) -> int:
        v = self.u16()
        return v - 0x10000 if v & 0x8000 else v

    def u32(self) -> int:
        self.need(4)
        (v,) = struct.unpack_from("<I", self.d, self.p)
        self.p += 4
        return v

    def raw(self, n: int) -> bytes:
        self.need(n)
        v = self.d[self.p : self.p + n]
        self.p += n
        return v

    def u16s(self, n: int) -> tuple[int, ...]:
        self.need(2 * n)
        v = struct.unpack_from(f"<{n}H", self.d, self.p)
        self.p += 2 * n
        return v


def _cstr_bytes(b: bytes) -> bytes:
    i = b.find(b"\x00")
    return b if i < 0 else b[:i]


def _decode_bytes_name(b: bytes) -> str:
    """글꼴·스타일 이름처럼 바이트로 저장된 이름. 상위 비트 문자는 조합형 2바이트."""
    b = _cstr_bytes(b)
    for enc in ("johab", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1")


def _hchar_str(codes, unknown: dict[int, int]) -> str:
    out = []
    for c in codes:
        if c == 0:
            break
        s = decode_hchar(c)
        if s is None:
            unknown[c] = unknown.get(c, 0) + 1
            s = unknown_placeholder(c)
        out.append(s)
    return "".join(out)


def parse_docinfo(b: bytes) -> dict:
    r = _R(b)
    di = {
        "cursor_line": r.u16(),
        "cursor_col": r.u16(),
        "paper_kind": r.u8(),
        "paper_orientation": r.u8(),
        "paper_height": r.u16(),
        "paper_width": r.u16(),
        "margin_top": r.u16(),
        "margin_bottom": r.u16(),
        "margin_left": r.u16(),
        "margin_right": r.u16(),
        "header_len": r.u16(),
        "footer_len": r.u16(),
        "gutter": r.u16(),
        "readonly": r.u16(),
        "reserved1": r.raw(4).hex(),
        "chain_page_no": r.u8(),
        "chain_footnote_no": r.u8(),
        "chain_filename": _decode_bytes_name(r.raw(40)),
        "annotation": _decode_bytes_name(r.raw(24)),
        "password": r.u16(),
        "begin_page_no": r.u16(),
        "begin_footnote_no": r.u16(),
        "footnote_count": r.u16(),
        "footnote_sep_text": r.u16(),
        "footnote_sep_line": r.u16(),
        "footnote_sep_gap": r.u16(),
        "footnote_char": r.u8(),
        "footnote_line_type": r.u8(),
        "border_margin": r.u16s(4),
        "border_line": r.u16(),
        "hide_empty_line": r.u8(),
        "table_move": r.u8(),
        "compressed": r.u8(),
        "sub_revision": r.u8(),
        "info_block_len": r.u16(),
    }
    assert r.p == DOCINFO_LEN
    return di


class _BodyParser:
    def __init__(self, body: bytes, doc_warnings: list[str], unknown: dict[int, int]):
        self.r = _R(body)
        self.warnings = doc_warnings
        self.unknown = unknown
        self.depth = 0
        self.embedded: dict[str, bytes] = {}

    # ---- 글꼴·스타일 ----
    def fonts(self) -> list[list[str]]:
        r = self.r
        out = []
        for _ in range(N_LANG):
            n = r.u16()
            if n > 1024:
                raise HWP3Error(f"글꼴 수 비정상 {n} @ {r.p - 2}")
            out.append([_decode_bytes_name(r.raw(FONT_NAME_LEN)) for _ in range(n)])
        return out

    def styles(self) -> list[dict]:
        r = self.r
        n = r.u16()
        if n > 1024:
            raise HWP3Error(f"스타일 수 비정상 {n}")
        out = []
        for i in range(n):
            name = _decode_bytes_name(r.raw(STYLE_NAME_LEN))
            cs = r.raw(CHARSHAPE_LEN)
            ps = r.raw(PARASHAPE_LEN)
            out.append({"index": i, "name": name, "char_shape": cs.hex(), "para_shape": ps.hex()})
        return out

    # ---- 문단 리스트 ----
    def para_list(self) -> list[Paragraph]:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise HWP3Error("문단 리스트 중첩이 너무 깊음")
        out: list[Paragraph] = []
        while True:
            p = self.paragraph()
            if p is None:
                break
            out.append(p)
        self.depth -= 1
        return out

    def paragraph(self) -> Paragraph | None:
        r = self.r
        start = r.p
        reuse_shape = r.u8()
        nch = r.u16()
        nline = r.u16()
        contain_cshape = r.u8()
        etcflag = r.u8()
        ctrlflag = r.u32()
        style = r.u8()
        char_shape = r.raw(CHARSHAPE_LEN)
        para_shape = None
        if nch and not reuse_shape:
            para_shape = r.raw(PARASHAPE_LEN)
        lines = [r.u16s(LINEINFO_LEN // 2) for _ in range(nline)]
        if nch == 0:
            if nline:
                self.warnings.append(f"끝 문단에 줄 정보 {nline}개 @ {start}")
            return None
        cshape_runs: list[tuple[int, bytes]] = []  # (hchar 위치, 글자 모양 31바이트): 모양이 바뀌는 지점
        if contain_cshape:
            for i in range(nch):
                flag = r.u8()
                if flag != 1:
                    cshape_runs.append((i, r.raw(CHARSHAPE_LEN)))
        para = Paragraph(
            offset=start,
            nch=nch,
            reuse_shape=reuse_shape,
            contain_cshape=contain_cshape,
            etcflag=etcflag,
            ctrlflag=ctrlflag,
            style=style,
            char_shape=char_shape,
            para_shape=para_shape,
            lines=lines,
            cshape_runs=cshape_runs,
        )
        count = 0
        buf: list[str] = []
        units = para.units  # (hchar 위치, 글자 또는 특수 문자): 줄 정보의 '줄 시작 위치'와 같은 단위
        ended = False
        while count < nch:
            pos = r.p
            ch = r.u16()
            if ch >= 32:
                s = decode_hchar(ch)
                if s is None:
                    self.unknown[ch] = self.unknown.get(ch, 0) + 1
                    s = unknown_placeholder(ch)
                buf.append(s)
                units.append((count, s))
                count += 1
                continue
            if ch == 13:
                count += 1
                ended = True
                break
            if buf:
                para.items.append("".join(buf))
                buf = []
            ctrl = self.control(ch, pos)
            para.items.append(ctrl)
            units.append((count, ctrl))
            count += ctrl.wsize
        if buf:
            para.items.append("".join(buf))
        if not ended:
            self.warnings.append(f"문단 끝 문자 없이 글자 수 소진 @ {start} (nch={nch}, count={count})")
        elif count != nch:
            self.warnings.append(f"글자 수 불일치 @ {start}: nch={nch} 실제={count}")
        return para

    # ---- 특수 문자 ----
    def control(self, ch: int, pos: int) -> Control:
        r = self.r
        if ch in FIXED_SIZE:
            size = FIXED_SIZE[ch]
            body = r.raw(size - 4)
            tail = r.u16()
            if tail != ch:
                raise HWP3Error(f"특수 문자 {ch} 끝 코드 불일치({tail}) @ {pos}")
            return Control(code=ch, offset=pos, size=size, wsize=WSIZE[ch], fields=self._fixed_fields(ch, body))
        if ch in SKIP_BLOCK:
            length = r.u32()
            again = r.u16()
            if again != ch:
                raise HWP3Error(f"특수 문자 {ch} 머리 코드 불일치({again}) @ {pos}")
            data = r.raw(length)
            fields = {"length": length}
            if ch == 5:
                fields.update(self._field_code(data))
            elif ch == 6:
                fields["data"] = data.hex()
            return Control(code=ch, offset=pos, size=r.p - pos, wsize=WSIZE[ch], fields=fields)
        # "hchar, dword, hchar" 머리 + 구조체
        reserved = r.u32()
        again = r.u16()
        if again != ch:
            raise HWP3Error(f"특수 문자 {ch} 머리 코드 불일치({again}) @ {pos}")
        if ch == 10:
            return self.box(pos, reserved)
        if ch == 11:
            return self.picture(pos, reserved)
        if ch == 14:
            return self.line(pos)
        if ch == 15:
            info = r.raw(8)
            plist = self.para_list()
            return Control(code=ch, offset=pos, size=r.p - pos, wsize=WSIZE[ch], fields={"info": info.hex()}, children=[plist])
        if ch == 16:
            info = r.raw(8)
            kind = r.u8()
            where = r.u8()
            plist = self.para_list()
            return Control(
                code=ch, offset=pos, size=r.p - pos, wsize=WSIZE[ch],
                fields={"info": info.hex(), "kind": "header" if kind == 0 else "footer", "kind_raw": kind, "where": where},
                children=[plist],
            )
        if ch == 17:
            info = r.raw(8)
            number = r.u16()
            kind = r.u16()
            width = r.u16()
            plist = self.para_list()
            return Control(
                code=ch, offset=pos, size=r.p - pos, wsize=WSIZE[ch],
                fields={"info": info.hex(), "number": number, "kind": "footnote" if kind == 0 else "endnote", "width": width},
                children=[plist],
            )
        if ch in (0,):
            raise HWP3Error(f"사용하지 않는 특수 문자 0 @ {pos}")
        raise HWP3Error(f"처리하지 않는 특수 문자 {ch} @ {pos}")

    def _fixed_fields(self, ch: int, body: bytes) -> dict:
        r = _R(body)
        if ch == 9:
            return {"width": r.u16(), "leader": r.u16()}
        if ch in (18, 19):
            return {"kind": r.u16(), "number": r.u16()}
        if ch == 20:  # 쪽번호달기(규격 표 55): 위치 0~8, 모양 0 아라비아·1 로마 대문자·2 로마 소문자, 3~5는 '- ## -'
            return {"position": r.u16(), "shape": r.u16()}
        if ch == 21:
            return {"kind": r.u16(), "what": r.u16()}
        if ch == 23:
            codes = r.u16s(3)
            return {"codes": list(codes), "text": _hchar_str(codes, self.unknown)}
        if ch == 24:
            return {"width": r.u16()}
        if ch == 7:
            return {"format": _hchar_str(r.u16s(40), self.unknown)}
        if ch == 8:
            fmt = _hchar_str(r.u16s(40), self.unknown)
            date = r.u16s(6)
            return {"format": fmt, "date": list(date)}
        if ch == 22:
            return {"field": _decode_bytes_name(body)}
        return {"raw": body.hex()}

    def _field_code(self, data: bytes) -> dict:
        try:
            r = _R(data)
            kind = r.raw(2)
            r.raw(4)
            loc = r.u16()
            r.raw(22)
            l1, l2, l3, lb = r.u32(), r.u32(), r.u32(), r.u32()
            s1 = _hchar_str(r.u16s(l1 // 2), self.unknown) if l1 else ""
            s2 = _hchar_str(r.u16s(l2 // 2), self.unknown) if l2 else ""
            s3 = _hchar_str(r.u16s(l3 // 2), self.unknown) if l3 else ""
            return {"field_type": kind.hex(), "location": loc, "str1": s1, "str2": s2, "str3": s3, "bin_len": lb}
        except HWP3Error as e:
            return {"field_parse_error": str(e)}

    def _box_head(self, code: int, pos: int) -> dict:
        """표·그림·선 정보의 공통 앞부분(규격 표 40/44/47의 오프셋 8~69, 62바이트).

        오프셋 0~7(예약 또는 그림의 추가 정보 길이)은 호출자가 이미 읽었다.
        """
        r = self.r
        f: dict = {}
        f["anchor"] = r.u8()  # 0-3 글자/문단/페이지/종이
        f["text_flow"] = r.u8()  # 0-2 자리차지/투명/어울림
        f["xpos"] = r.i16()
        f["ypos"] = r.i16()
        f["option"] = r.u16()
        f["code_check"] = r.u16()
        if f["code_check"] != code:
            raise HWP3Error(f"상자 정보의 특수 문자 코드 불일치({f['code_check']} != {code}) @ {pos}")
        f["margin"] = [list(r.u16s(4)) for _ in range(3)]  # 바깥/안/셀 × 왼/오/위/아래
        f["box_w"], f["box_h"] = r.u16s(2)
        f["cap_w"], f["cap_h"], f["cap_len"] = r.u16s(3)
        f["total_w"], f["total_h"] = r.u16s(2)
        r.raw(4)
        f["protect_linesp"] = r.u8()
        r.raw(1)
        f["box_x"], f["box_y"] = r.u16s(2)
        f["reserved66"] = r.u16s(2)
        return f

    def box(self, pos: int, reserved: int) -> Table:
        """특수 문자 10: 식별 8 → 표 정보 84 → 셀 정보 27×n → 셀 문단 리스트 n개 → 캡션 문단 리스트."""
        r = self.r
        r.raw(8)
        f = self._box_head(10, pos)
        f["cap_pos"], f["box_no"] = r.u16(), r.u16()
        r.raw(2)
        f["chart_id_or_baseline"], btype, ncell, f["protect"] = r.u16s(4)
        f["box_type_raw"] = btype
        if ncell == 0 or ncell > 20000:
            raise HWP3Error(f"상자 셀 수 비정상 {ncell} @ {pos}")
        if btype != 0 and ncell != 1:
            self.warnings.append(f"표가 아닌 상자(type={btype})의 셀 수 {ncell} @ {pos}")
        cells = []
        for i in range(ncell):
            cr = _R(r.raw(CELL_LEN))
            row_no, col_no = cr.u8(), cr.u8()
            color, x, y, w, h, txth, cellh = cr.u16s(7)
            cr.raw(2)
            const1, vcenter = cr.u8(), cr.u8()
            linetype = list(cr.raw(4))
            shade, diagonal, cprotect = cr.u8(), cr.u8(), cr.u8()
            raw = {"sheet_row": row_no, "sheet_col": col_no, "color": color, "text_h": txth, "cell_h": cellh, "const1": const1,
                   "vcenter": vcenter, "linetype": linetype, "shade": shade, "diagonal": diagonal, "protect": cprotect}
            cells.append(Cell(index=i, x=x, y=y, w=w, h=h, raw=raw))
        for c in cells:
            c.paragraphs = self.para_list()
        caption = self.para_list()
        t = Table(
            code=10, offset=pos, size=r.p - pos, wsize=WSIZE[10], fields=f,
            box_type=BOX_TYPES.get(btype, f"other{btype}"), cells=cells, caption=caption,
        )
        t.build_grid()
        return t

    def picture(self, pos: int, reserved: int) -> Control:
        """특수 문자 11: 식별 8 → 그림 정보 348+n → 캡션 문단 리스트. n은 그림 정보 첫 dword."""
        r = self.r
        follow = r.u32()
        r.raw(4)
        f = self._box_head(11, pos)
        f["cap_pos"], f["box_no"] = r.u16(), r.u16()
        f["pictype"] = r.u8()  # 0 외부 파일, 1 OLE, 2 포함 그림, 3 그리기 개체
        f["skip"] = list(r.u16s(2))
        f["scale"] = list(r.u16s(2))
        f["path"] = _decode_bytes_name(r.raw(256))
        f["brightness"], f["contrast"], f["effect"], f["pic_protect"] = r.raw(4)
        r.raw(5)
        f["follow_len"] = follow
        follow_start = r.p
        r.need(follow)
        follow_end = follow_start + follow
        drawing_text: list[list[Paragraph]] = []
        if follow and f["pictype"] == 3:
            f["follow_kind"] = "drawing"
            drawing_text, info = self.drawing(follow_start, follow_end, pos)
            f["drawing"] = info
        elif follow:
            head = r.d[follow_start : follow_start + 4]
            f["follow_kind"] = "hypertext" if head == b"\x69\x02\x00\x00" else "unknown"
        r.p = follow_end
        caption = self.para_list()
        # children[0] = 캡션, 그 뒤 = 그리기 글상자 문단 리스트들
        return Control(code=11, offset=pos, size=r.p - pos, wsize=WSIZE[11], fields=f, children=[caption, *drawing_text])

    # ---- 그리기 개체(규격 11장) ----
    DO_COMMON = 88  # 개체 종류·연결 4 + 위치·크기 24 + 차지 영역 16 + 기본 속성 44 (크기 필드 제외)
    DO_FLAG_GRADATION = 1 << 16
    DO_FLAG_ROTATION = 1 << 17
    DO_FLAG_BITMAP = 1 << 18
    DO_FLAG_AS_TEXTBOX = 1 << 19
    DO_FLAG_WATERMARK = 1 << 20
    DO_TEXTBOX = 6

    def drawing(self, start: int, end: int, pos: int) -> tuple[list[list[Paragraph]], dict]:
        """그림 종류 3의 추가 정보: 틀 헤더 → 개체들(전위 순회, 형제/자식 연결 비트).

        글상자 개체(종류 6)의 세부 정보 2와 '글상자로 만들기' 속성 개체의 공통 헤더 뒤에 들어 있는
        문단 리스트를 꺼낸다. 해석이 깨지면 추가 정보 길이로 이 블록만 건너뛰고 경고를 남긴다.
        """
        r = self.r
        saved_end = r.end
        r.p, r.end = start, end
        texts: list[list[Paragraph]] = []
        info: dict = {"objects": 0, "types": {}, "text_lists": 0}
        try:
            hsize = r.u32()
            info["zorder"], info["member_count"] = r.u32(), r.u32()
            r.raw(16)
            if hsize > 24:
                r.raw(hsize - 24)
            self._drawing_objects(texts, info, 0)
            if r.p != end:
                self.warnings.append(f"그리기 개체 뒤 {end - r.p}바이트 남음 @ {pos}")
            info["consumed_exactly"] = r.p == end
        except HWP3Error as e:
            self.warnings.append(f"그리기 개체 해석 실패 @ {pos}: {e}")
            info["error"] = str(e)
        finally:
            r.end = saved_end
            r.p = end
        info["text_lists"] = len(texts)
        return texts, info

    def _drawing_objects(self, texts: list, info: dict, depth: int) -> None:
        r = self.r
        if depth > MAX_DEPTH:
            raise HWP3Error("그리기 개체 중첩이 너무 깊음")
        while True:
            hstart = r.p
            size = r.u32()
            if size < self.DO_COMMON:
                raise HWP3Error(f"그리기 개체 공통 헤더 길이 {size} < {self.DO_COMMON} @ {hstart}")
            otype = r.u16()
            link = r.u16()
            rel_x, rel_y, w, h, abs_x, abs_y = struct.unpack("<6i", r.raw(24))  # 규격 표 68
            area = struct.unpack("<4i", r.raw(16))
            props = [r.u32() for _ in range(11)]  # 선 모양 3, 선 색, 선 굵기, 면 색, 무늬, 무늬 색, 여백 2, 기타 옵션
            flag = props[10]
            obj = {"type": otype, "depth": depth, "rel": (rel_x, rel_y), "size": (w, h), "abs": (abs_x, abs_y), "area": area,
                   "line_style": props[0], "line_color": props[3], "line_width": props[4], "fill_color": props[5],
                   "pattern": props[6], "pattern_color": props[7], "text_margin": (props[8], props[9]), "option": flag,
                   "text_index": None}
            info.setdefault("items", []).append(obj)
            common = self.DO_COMMON
            if size >= common + 32 and flag & self.DO_FLAG_ROTATION:
                r.raw(32)
                common += 32
            if size >= common + 28 and flag & self.DO_FLAG_GRADATION:
                r.raw(28)
                common += 28
            if size >= common + 278 and flag & self.DO_FLAG_BITMAP:
                r.raw(278)
                common += 278
            if size >= common + 3 and flag & self.DO_FLAG_WATERMARK:
                r.raw(5 if size - common >= 5 else 3)
                common += 5
            if size > common and flag & self.DO_FLAG_AS_TEXTBOX:
                r.raw(8)
                obj["text_index"] = len(texts)
                texts.append(self.para_list())
            else:
                r.p = hstart + 4 + size
            info["objects"] += 1
            info["types"][otype] = info["types"].get(otype, 0) + 1
            # 세부 정보 1, 2
            n1 = r.u32()
            r.raw(n1)
            n2 = r.u32()
            if otype == self.DO_TEXTBOX and n2:
                s2 = r.p
                obj["text_index"] = len(texts)
                texts.append(self.para_list())
                if r.p != s2 + n2:
                    self.warnings.append(f"그리기 글상자 문단 리스트 길이 불일치 {r.p - s2} != {n2} @ {s2}")
                    r.p = s2 + n2
            else:
                r.raw(n2)
            if link & 2:
                self._drawing_objects(texts, info, depth + 1)
            if not link & 1:
                break

    def line(self, pos: int) -> Control:
        """특수 문자 14: 식별 8 → 선 정보 84."""
        r = self.r
        r.raw(8)
        f = self._box_head(14, pos)
        f["line"] = list(r.u16s(4))
        f["width"], f["shade"], f["color"] = r.u16s(3)
        return Control(code=14, offset=pos, size=r.p - pos, wsize=WSIZE[14], fields=f)

    # ---- 추가 정보 블록 ----
    def tags(self, end_tag: int) -> list[dict]:
        r = self.r
        out = []
        while r.p < r.end:
            start = r.p
            if r.end - r.p < 8:
                self.warnings.append(f"추가 정보 블록 꼬리 {r.end - r.p}바이트 @ {start}")
                r.p = r.end
                break
            tag = r.u32()
            size = r.u32()
            if tag == end_tag:
                out.append({"tag": tag, "name": "end", "offset": start, "size": size})
                if size:
                    r.raw(min(size, r.end - r.p))
                break
            data = r.raw(size)
            item = {"tag": tag, "name": TAG_NAMES.get(tag, f"tag{tag:#x}"), "offset": start, "size": size}
            if tag == 1 and size >= 32:  # 규격 8.1: 이름 echar[16], 형식 echar[16], 그림 내용
                item["image_name"] = _cstr_bytes(data[:16]).decode("latin-1")
                item["image_format"] = _cstr_bytes(data[16:32]).decode("latin-1")
                item["image_bytes"] = size - 32
                self.embedded[item["image_name"]] = data[32:]
            if tag == 0x80000002:
                item["text"] = _hchar_str(struct.unpack(f"<{size // 2}H", data[: size // 2 * 2]), {})
            out.append(item)
        return out


def _inflate(data: bytes, start: int) -> tuple[bytes, int, bool | None]:
    """raw deflate를 풀고 (본문, 압축 영역 끝 위치, CRC 일치 여부)를 돌려준다."""
    d = zlib.decompressobj(-15)
    try:
        body = d.decompress(data[start:])
        body += d.flush()
    except zlib.error as ex:
        raise HWP3Error(f"압축 본문이 깨짐: {ex}") from ex
    if not d.eof:
        raise HWP3Error("압축 본문이 끝나지 않음(파일 잘림)")
    end = len(data) - len(d.unused_data)
    crc_ok = None
    if len(d.unused_data) >= 8:
        crc, isize = struct.unpack_from("<II", data, end)
        crc_ok = crc == (zlib.crc32(body) & 0xFFFFFFFF) and isize == (len(body) & 0xFFFFFFFF)
        if crc_ok:
            end += 8
    return body, end, crc_ok


def parse_bytes(data: bytes, path: str | None = None) -> Document:
    if not is_hwp3(data):
        raise HWP3Error("HWP 3.x 서명이 아님")
    if len(data) < SIG_LEN + DOCINFO_LEN + SUMMARY_LEN:
        raise HWP3Error("파일이 머리보다 짧음")
    warnings: list[str] = []
    unknown: dict[int, int] = {}
    di = parse_docinfo(data[SIG_LEN : SIG_LEN + DOCINFO_LEN])
    if di["password"]:
        raise HWP3Error("암호가 걸린 문서")
    sp = SIG_LEN + DOCINFO_LEN
    summary = {}
    for i, name in enumerate(SUMMARY_FIELDS):
        codes = struct.unpack_from("<56H", data, sp + i * 112)
        summary[name] = _hchar_str(codes, unknown)
    ib_start = sp + SUMMARY_LEN
    ib_len = di["info_block_len"]
    info_block = data[ib_start : ib_start + ib_len]
    if len(info_block) != ib_len:
        raise HWP3Error("정보 블록이 잘림")
    body_start = ib_start + ib_len
    if di["compressed"]:
        body, comp_end, crc_ok = _inflate(data, body_start)
        if crc_ok is False:
            warnings.append("압축 본문 CRC32/길이 불일치")
    else:
        body, comp_end, crc_ok = data[body_start:], len(data), None

    bp = _BodyParser(body, warnings, unknown)
    fonts = bp.fonts()
    styles = bp.styles()
    paragraphs = bp.para_list()
    # 추가 정보 블록(#1)은 8바이트 0(tag 0)으로 끝난다(규격 3.8).
    tags = bp.tags(TAG_END_COMPRESSED)
    tail_tags: list[dict] = []
    if di["compressed"]:
        if bp.r.p != len(body):
            warnings.append(f"압축 본문 끝에 해석하지 않은 {len(body) - bp.r.p}바이트")
        if comp_end < len(data):
            tp = _BodyParser(data[comp_end:], warnings, {})
            tail_tags = tp.tags(TAG_END_UNCOMPRESSED)
            rest = len(data) - comp_end - tp.r.p
            if rest:
                warnings.append(f"파일 끝 해석하지 않은 {rest}바이트")
    else:
        # 비압축 문서: 같은 흐름에 추가 정보 블록 #2(0x80000000으로 끝)가 이어진다.
        tail_tags = bp.tags(TAG_END_UNCOMPRESSED)
        if bp.r.p != len(body):
            warnings.append(f"파일 끝 해석하지 않은 {len(body) - bp.r.p}바이트")

    doc = Document(
        path=path, size=len(data), docinfo=di, summary=summary, info_block=info_block,
        body_size=len(body), body_crc_ok=crc_ok, fonts=fonts, styles=styles,
        paragraphs=paragraphs, tags=tags, tail_tags=tail_tags, warnings=warnings, unknown_chars=unknown,
    )
    doc.embedded_images = bp.embedded
    _assign_pages(doc)
    return doc


def _assign_pages(doc: Document) -> None:
    """쪽 번호와 표 ID를 매긴다.

    쪽: 줄 정보 마지막 word의 bit15(유효)·bit0(페이지 경계)로 센다(규격 표 12).
    레이아웃 정보가 저장되지 않은 파일(모든 줄의 bit15가 0)은 쪽을 None으로 둔다.
    표 ID: 문서 순서(바깥 표가 안쪽 표보다 먼저)로 0부터.
    """
    has_layout = any(ln[-1] & 0x8000 for p in doc.paragraphs for ln in p.lines)
    doc.has_layout = has_layout
    page = max(1, doc.docinfo.get("begin_page_no") or 1)
    for i, p in enumerate(doc.paragraphs):
        if not has_layout:
            p.page = None
            continue
        breaks = p.page_breaks()
        if breaks and breaks[0][0] == 0 and breaks[0][1] == "page" and i > 0:
            page += 1
            breaks = breaks[1:]
        p.page = page
        page += sum(1 for _, kind in breaks if kind == "page")

    def walk(paras, depth, page, para_index):
        for j, p in enumerate(paras):
            pi = para_index if para_index is not None else j + 1
            pg = page if depth else p.page
            for c in p.controls():
                if isinstance(c, Table):
                    c.depth = depth
                    c.para_index = pi
                    c.page = pg
                    for cell in c.cells:
                        walk(cell.paragraphs, depth + 1, pg, pi)

    walk(doc.paragraphs, 0, None, None)
    for k, t in enumerate(doc.iter_tables()):
        t.table_id = k


def parse_file(path: str | Path) -> Document:
    p = Path(path)
    return parse_bytes(p.read_bytes(), path=str(p))
