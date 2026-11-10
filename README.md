# hwp3 — HWP 3.x(한글 3.0/96/97) 문서 파서와 변환기

한국어 | [English](README.en.md)

hwp3는 HWP 3.x(한글 3.0/96/97) 문서를 읽어 JSON·텍스트·HTML·CSV·엑셀로 변환하는 파이썬 라이브러리입니다. 한글과컴퓨터가 공개한 「한글 문서 파일 형식 3.0」 규격을 기준으로 구현했습니다.

- 문단, 표, 글상자, 각주, 머리말·꼬리말, 그리기 개체 안의 글을 문서 구조와 같은 형태로 추출합니다.
- 표의 병합과 테두리를 엑셀로 옮깁니다. 괘선 문자로 그린 표도 칸 단위로 나눕니다.
- 한글 97이 저장한 줄·쪽 배치에 맞춰 쪽 모양을 SVG로 그립니다.
- 해석과 JSON·텍스트·HTML·CSV 변환에는 파이썬 표준 라이브러리만 사용합니다.

HWP5(한글 2002 이후의 `.hwp`)와 HWPX는 지원하지 않습니다.

## 예시

`examples/sample.hwp`는 규격에 맞춰 바이트를 조립한 예시 문서입니다. `examples/make_sample.py`로 만들었고 내용은 가상입니다. 병합 칸이 있는 표 1개, 각주 1개, 괘선 문자로 그린 표 1개를 담았습니다.

```
hwp3 convert examples/sample.hwp -o out --xlsx --numbers
```

`out/text.txt`

```
과일 가게 견적서 (예시)
아래 숫자는 hwp3 예시를 위해 지어낸 것이다.[각주: 실제 가격이 아니다.]

[표 T0 5x3 쪽=None]
품목	1분기	
	수량	금액
사과	120	360,000
배	45	225,000
합계		585,000
[/표 T0]
부대 비용
┌────┬────────┐
│구분│금액    │
├────┼────────┤
│배송│   3,000│
│포장│   1,500│
└────┴────────┘
끝.
```

`out/tables/T000.csv`

```
품목,1분기,
,수량,금액
사과,120,"360,000"
배,45,"225,000"
합계,,"585,000"
```

`out/document.xlsx`에는 시트 4개(`본문`, `목록`, `T001`, `X001`)가 생성됩니다. 표의 병합·음영·굵은 선이 엑셀에 반영되고, 괘선 문자 표는 칸으로 나뉩니다.

| 표 시트 `T001` | 괘선 문자 표 시트 `X001` |
|---|---|
| ![예시 표를 옮긴 엑셀 시트](docs/img/sample_table_xlsx.png) | ![괘선 문자 표를 옮긴 엑셀 시트](docs/img/sample_text_table_xlsx.png) |

다음은 실제 문서를 변환한 결과입니다. 왼쪽은 쪽 보기(`--pages`), 오른쪽은 같은 표의 엑셀 시트(`--xlsx --numbers`)이며 윗부분만 실었습니다.

| 쪽 보기 | 엑셀 |
|---|---|
| ![쪽 보기로 그린 보고서 한 쪽의 윗부분](docs/img/report_page.png) | ![같은 표를 옮긴 엑셀 시트의 윗부분](docs/img/report_xlsx.png) |

- 문서 출처: 대한건설협회, 「2004년 상반기 적용 건설업 임금실태 조사 보고서」 8쪽 일부. 원본 파일은 저장소에 포함하지 않습니다.
- 엑셀 그림은 생성한 `.xlsx`를 macOS 훑어보기로 연 화면입니다.

## 검증 범위

hwp3는 특정 문서 종류가 아닌 규격을 기준으로 구현한 범용 파서입니다. 문서 54개로 검증했습니다.

| 대상 | 결과 |
|---|---|
| 공개 HWP3 시험 파일 33개(rhwp, java-hwp, LibreOffice 시험 자료 등. 양식, 2만 문단이 넘는 문서, 수식·그리기 개체 포함) | 32개 해석, 암호 문서 1개 거부 |
| 1998~2004년 건설업 임금실태조사 보고서 22개 | 22개 해석, 경고 0건 |
| 손상시킨 파일 48개 | 48개 오류로 거부, 처리되지 않은 예외 0건 |
| 한컴오피스가 HWPX로 변환한 문서 13개 | 표 131개의 격자 일치 |
| 한컴 변환본 또는 PDF가 있는 문서 23개 | 한컴 쪽 한글 누락 0건 |

모든 HWP 3.x 문서의 변환을 보장하지는 않습니다. [제한 사항](#제한-사항)을 참고하세요.

## 기능

| 기능 | 필요 패키지 |
|---|---|
| 문서 구조 읽기(문단, 표·글상자·수식·버튼, 그림, 각주, 머리말·꼬리말, 그리기 개체 글) | 없음 |
| JSON, 텍스트, HTML, 표 CSV 변환 | 없음 |
| 한컴식 보기(한컴오피스의 HWPX 변환 결과와 같은 표 격자) | 없음 |
| 엑셀(`.xlsx`) 변환 | openpyxl |
| 쪽 보기(쪽별 SVG와 단일 파일 HTML 뷰어) | 글꼴 포함에 fontTools·brotli, 그림 변환에 Pillow |

PDF와 HWPX 변환은 지원하지 않습니다(개발 예정).

## 설치

파이썬 3.11 이상이 필요합니다.

```
pip install "hwp3[all] @ git+https://github.com/kimi230/hwp3"    # 엑셀·쪽 보기 포함
pip install "git+https://github.com/kimi230/hwp3"                # 해석과 JSON·텍스트·HTML·CSV 변환
```

저장소를 내려받은 경우 해당 폴더에서 `pip install ".[all]"`로 설치합니다. 설치 없이 사용하려면 `src/`를 경로에 추가합니다: `PYTHONPATH=src python -m hwp3 …`

## 명령행 사용법

```
hwp3 info <파일.hwp>
hwp3 convert <파일.hwp> -o <폴더> [--hancom] [--pages] [--xlsx [--xlsx-view raw] [--numbers]]
hwp3 fonts
```

| 출력 | 내용 |
|---|---|
| `document.json` | 문서 전체 구조(schema `hwp3-document/1`). 대응 유니코드가 없는 글자는 `unknown_codes`에 기록 |
| `text.txt` | 본문 텍스트 |
| `document.html` | 표를 포함한 HTML |
| `tables/T###.csv` | 원본 격자 기준 표 |
| `hancom/`(`--hancom`) | 한컴 HWPX 변환 결과와 같은 형태. 글상자→표, 빈칸 채우기, 각주 ')' 정리, 수식 분리 |
| `pages/`(`--pages`) | 쪽별 SVG와 단일 파일 뷰어 `index.html`. 한글 97이 저장한 줄·쪽 배치 기준 |
| `document.xlsx`(`--xlsx`) | 엑셀. `본문`·`목록` 시트, 표별 시트(`T###`), 괘선 문자 표 시트(`X###`) |

엑셀 옵션

- `--xlsx-view hancom`(기본값): 한컴식 격자를 사용합니다. 시트 이름이 `hancom/tables/T###.csv`와 같습니다.
- `--xlsx-view raw`: 원본 격자를 사용합니다. 시트 이름이 `tables/T###.csv`와 같습니다.
- `--numbers`: 쉼표가 있는 숫자(`56,352`)를 숫자 형식으로 변환합니다. 기본값은 원문 문자열입니다.
- 병합, 테두리, 대각선, 음영, 세로 정렬, 글자 크기, 열 너비, 행 높이를 옮깁니다. 가로 정렬, 글꼴 이름, 굵게, 그림은 옮기지 않습니다.
- 표 위치(쪽·문단 번호)와 표 제목은 `목록` 시트에 기록합니다.

쪽 보기 옵션

- `--soffice <soffice 경로>`: WMF·EMF 그림을 LibreOffice로 PNG로 변환합니다(선택 사항).

## 파이썬 사용법

```python
from hwp3 import parse_file, HWP3Error
from hwp3 import compat                 # 한컴식 보기
from hwp3.render import write_pages     # 쪽 보기
from hwp3.xlsx import write_xlsx        # 엑셀(openpyxl 필요)

doc = parse_file("x.hwp")               # 암호 문서·손상된 파일은 HWP3Error
for t in doc.iter_tables():             # 문서 순서, 표 안의 표 포함
    if t.box_type == "table":
        rows = t.matrix()               # 행×열 문자열(병합 칸은 왼쪽 위 칸에 기록)
        problems = t.grid_problems()    # 빈칸·겹침
text = compat.text(doc)                 # 중복 없는 본문 글(한컴식)
views = list(compat.table_views(doc))   # 한컴 HWPX와 같은 격자
doc.unknown_chars                       # {HNC 코드: 횟수}. 대응 유니코드가 없는 한컴 전용 글자
write_xlsx(doc, "out.xlsx", view="hancom", numbers=False)   # view="raw"는 원본 격자
```

- 본문 글을 이어 붙일 때는 `Paragraph.items`의 문자열을 사용합니다. `export._para_plain`은 글상자 글을 바깥 문단에도 넣으므로 `iter_paragraphs()`와 조합하면 같은 글이 두 번 들어갑니다.
- 쪽 번호는 `Table.page`, `Paragraph.page`로 확인합니다. 배치가 저장되지 않은 문서에서는 `None`입니다.

## 글꼴(쪽 보기)

쪽 보기는 한컴 글꼴 대신 Noto Serif KR과 Noto Sans KR을 사용합니다. 두 글꼴은 `src/hwp3/fonts/`에 포함되어 있어(약 34MB, SIL OFL 1.1) 별도 설치가 필요 없습니다.

다른 글꼴 파일을 쓰려면 아래 탐색 순서를 이용합니다. `hwp3 fonts` 명령으로 탐색 결과를 확인할 수 있습니다.

1. 환경변수 `HWP3_FONT_DIR`
2. 패키지 안 `hwp3/fonts/`
3. 운영체제 글꼴 폴더(macOS `~/Library/Fonts` 등, 리눅스 `~/.local/share/fonts`·`/usr/share/fonts` 등, 윈도 `%WINDIR%\Fonts`)

## 제한 사항

- 암호가 걸린 문서는 지원하지 않습니다.
- 쪽 보기는 한컴 글꼴을 Noto Serif/Sans KR로 대체합니다.
- 위치 정보가 저장되지 않은 HWP3(다른 프로그램이 저장한 파일)의 쪽 보기는 계산한 배치를 사용하므로 원본과 차이가 있습니다.
- 다단, 쪽 테두리, 글맵시, OLE는 그리지 않습니다.
- 한컴 전용 글자 일부는 대응 유니코드를 확인하지 못했습니다(예: 0x3073, 0x3078, 0x25F1). 변환 결과에는 `□`로 표시합니다.
- macOS에서 실행을 확인했습니다. 리눅스와 윈도는 확인 전입니다.
- 엑셀 파일은 openpyxl, LibreOffice, macOS 훑어보기로 확인했습니다. 마이크로소프트 엑셀은 확인 전입니다.

## 개발

```
pip install -e ".[test]"
pytest                      # 81건. openpyxl·fontTools·brotli가 없으면 해당 시험을 건너뜁니다
```

- 글자 보충표 `src/hwp3/hnc_table.tsv`는 스크립트로 생성한 파일이므로 수동으로 편집하지 않습니다. 생성 스크립트는 저장소에 포함되어 있지 않습니다.
- 변환기는 원본 모델(`Document`)을 변경하지 않습니다.

## 라이선스

MIT 라이선스로 배포합니다(`LICENSE`). 아래 두 항목은 조건이 다릅니다.

- `src/hwp3/symbols.py`: LibreOffice hwpfilter의 변환 규칙을 옮긴 파일로, MPL 2.0을 따릅니다(`LICENSES/MPL-2.0.txt`).
- `src/hwp3/fonts/`의 Noto 글꼴: SIL OFL 1.1을 따릅니다.

포함된 글꼴과 글자표의 출처와 조건은 `src/hwp3/THIRD_PARTY.md`에 있습니다. 패키지를 설치하면 이 파일도 설치됩니다.

## 알림

본 제품은 한글과컴퓨터의 한글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.

"한글", "한컴", "HWP"는 주식회사 한글과컴퓨터의 상표입니다. 이 프로젝트는 한글과컴퓨터와 제휴·후원·승인 관계가 없는 독립 오픈소스 프로젝트입니다.
