# 제3자 자료와 고지

이 패키지에 들어 있거나 만들 때 참고한 다른 사람의 자료다. 경로는 이 파일이 있는 `hwp3/` 폴더 기준이다.

## 패키지에 들어 있는 것

### 글꼴 (`fonts/`)

| 파일 | 저작권 표기 | 라이선스 |
|---|---|---|
| `NotoSerifKR[wght].ttf` | (c) 2017-2024 Adobe (http://www.adobe.com/). | SIL Open Font License 1.1. 전문 `fonts/OFL-NotoSerifKR.txt` |
| `NotoSansKR[wght].ttf` | (c) 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'. | SIL Open Font License 1.1. 전문 `fonts/OFL-NotoSansKR.txt` |

- 출처: `google/fonts` 저장소 `ofl/notoserifkr/`, `ofl/notosanskr/`, 커밋 `5bb28733cec313b63fbd415b574b5ae35c8aa8b8`. 고치지 않은 원본이다(해시는 `fonts/README.md`).
- 쪽 보기는 쓰인 글자만 뽑은 글꼴을 HTML에 넣는다. 그 글꼴 이름은 'Noto Serif KR'·'Noto Sans KR'이다.

### 글자 보충표 (`hnc_table.tsv`)

한글 3.x 내부 코드(HNC)를 유니코드로 바꾸는 표다. 아래 자료를 합쳐 만들었고, 각 줄 셋째 칸에 근거가 적혀 있다.

| 근거 표시 | 자료 | 조건 |
|---|---|---|
| `hnc` | `hnc2unicode.rb`, Hodong Kim. `ddoleye/java-hwp` 저장소, 커밋 `a1a799b2c588cbdfd3c270eafe7501c8da16a506` | public domain(파일 표기) |
| `hnc+ktug` | `hypua2jamocomposed.txt`, KTUG Hanyang PUA Table Project. `mete0r/hypua2jamo` 저장소, 커밋 `cf030e6997529a49d67234e2f611f5b9a0af2b34` | public domain(파일 표기) |
| `hnc+rhwp` | rhwp 0.9.0 `src/renderer/hancom_pua.rs`의 표 36줄 중 쓰인 것 | MIT. 고지는 아래 |
| `hancom-pdf` | 한컴오피스가 만든 PDF의 그림으로 직접 확인한 값 2개 | — |

rhwp 고지 전문:

```
MIT License

Copyright (c) 2025-2026 Edward Kim

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 참고해서 다시 쓴 것 (코드나 표를 복사하지 않음)

| 대상 | 참고한 것 |
|---|---|
| 파일 구조 해석(`parser.py`, `model.py`, `render.py`) | 한글과컴퓨터 공개 문서 「한글 문서 파일 형식 3.0」(revision 1.2) |
| 줄 정보의 예약 칸(`render.py`) | rhwp(MIT)의 해석을 참고해 확인했다 |

## MPL 2.0을 따르는 파일

| 파일 | 출처 |
|---|---|
| `symbols.py`(기호·괘선·한자 코드 계산식) | LibreOffice `hwpfilter/source/hcode.cxx`(MPL 2.0. 그 파일은 Apache License 2.0으로 받은 작업을 포함한다)의 변환 규칙을 읽고 파이썬 계산식으로 옮겼다. 상수 이름과 계산 순서가 원본을 따르므로 이 파일만 MPL 2.0으로 배포한다. KS X 1001 → 유니코드는 파이썬 `cp949` 코덱이 맡는다 |

MPL 2.0 전문은 저장소의 `LICENSES/MPL-2.0.txt`와 설치본의 라이선스 폴더에 있다. 패키지의 나머지 코드는 MIT다.

## 선택 의존 패키지 (패키지에 들어 있지 않음)

| 패키지 | 쓰는 곳 | 라이선스 |
|---|---|---|
| openpyxl | 엑셀 내보내기 | MIT |
| fontTools, brotli | 쪽 보기에 글꼴 넣기 | MIT |
| Pillow | 쪽 보기의 BMP·WMF 그림 변환 | MIT-CMU |
