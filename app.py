"""문서 추출 비교 — 배포용 뷰어.

로컬에서 `python export_results.py` 로 만들어 둔 `results.json` 을 읽어 그리기만 한다.
이 파일은 docling/unstructured/pymupdf 를 임포트하지 않고, 계산도 하지 않는다.
Streamlit Community Cloud 처럼 CPU·메모리가 빠듯한 곳에서도 그대로 뜬다.

표·요약·지표는 전부 미리 계산된 값을 그대로 HTML 로 찍는다 (Arrow 데이터프레임 같은
무거운 컴포넌트를 쓰지 않는다).

로컬에서 실제 추출을 돌려 보려면 `streamlit run app-local.py` 를 쓴다.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).parent
RESULTS_PATH = BASE_DIR / "results.json"
TITLE = "추출 모델 성능 비교"
# 본문 미리보기 박스의 높이(px). 방식마다 같은 값을 써야 아래 "표 추출"이 나란히 선다.
TEXT_BOX_H = 520

st.set_page_config(page_title=TITLE, layout="wide")

st.markdown(
    """
    <style>
      /* 방식별 열은 나란히 놓고 비교하는 화면이라, 본문 박스가 열마다 다른 높이에서
         시작하면 눈이 줄을 못 맞춘다. 지표+백엔드 줄을 한 덩어리로 묶어 높이를
         고정해, 어떤 방식이든 본문 박스가 같은 y 에서 시작하고 같은 y 에서 끝나게 한다. */
      .res-head { height:9.6rem; display:flex; flex-direction:column; gap:.3rem;
                  margin:.1rem 0 .5rem; }
      /* 3열 고정 그리드 = 지표 5개가 항상 2줄. 열 너비에 따라 1줄/2줄로 널뛰지 않는다. */
      .stat-row { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr));
                  gap:.3rem .6rem; }
      .stat-row .stat { display:flex; flex-direction:column; line-height:1.2;
                        min-width:0; }
      .stat-row .k { font-size:.78rem; opacity:.65; white-space:nowrap; }
      .stat-row .v { font-size:1.2rem; font-weight:600; white-space:nowrap;
                     font-variant-numeric:tabular-nums; }
      /* 백엔드 줄만 남은 높이를 먹는다. 길면 잘라내지 않고 이 안에서 스크롤한다. */
      .res-head .backend { flex:1; min-height:0; overflow-y:auto;
                           font-size:.75rem; opacity:.72; line-height:1.45; }
      .res-head .backend code { font-size:.72rem; }

      .tbl-wrap { overflow-x:auto; margin:.2rem 0 .4rem; }
      table.cmp { border-collapse:collapse; width:100%; font-size:.85rem; }
      table.cmp th, table.cmp td {
        border:1px solid rgba(128,128,128,.35); padding:.3rem .55rem;
        text-align:left; vertical-align:top; }
      table.cmp th { background:rgba(128,128,128,.14); font-weight:600;
                     white-space:nowrap; }
      table.cmp td.num { text-align:right; font-variant-numeric:tabular-nums;
                         white-space:nowrap; }
      table.cmp.summary td { white-space:nowrap; }

      /* 높이·테두리는 바깥 컨테이너가 갖는다. 여기선 글꼴과 줄바꿈만 손본다. */
      .text-plain { margin:0;
                    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
                    font-size:.78rem; line-height:1.45;
                    white-space:pre-wrap; word-break:break-word; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ helpers


@st.cache_data(show_spinner=False)
def load_results(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def esc(v) -> str:
    return html.escape(str(v))


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def html_table(header: list[str], rows: list[list], numeric: set[int] = frozenset(),
               extra_class: str = "") -> str:
    """미리 계산된 값을 그대로 찍는 순수 HTML 표.

    st.dataframe 은 값을 Arrow 로 직렬화해 프런트엔드 컴포넌트에 태우는데, 여기서
    보여줄 건 이미 확정된 문자열뿐이라 그 왕복이 통째로 낭비다.
    """
    head = "".join(f"<th>{esc(h)}</th>" for h in header)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{esc(c)}</td>' if i in numeric else f"<td>{esc(c)}</td>"
            for i, c in enumerate(r)
        )
        + "</tr>"
        for r in rows
    )
    cls = f"cmp {extra_class}".strip()
    return (
        f'<div class="tbl-wrap"><table class="{cls}">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


# 백엔드 이름은 실행 조건을 그대로 적어 둔 문자열이라 캡션에서 길다. 화면에선
# 무엇으로 돌았는지만 알면 되므로 `strategy=` 접두사와 OCR 언어 목록을 접는다.
_RE_OCR_LANGS = re.compile(r"OCR\s+[A-Za-z]{2,4}(?:\+[A-Za-z]{2,4})*")


def short_backend(backend: str) -> str:
    return _RE_OCR_LANGS.sub("OCR", backend.replace("strategy=", ""))


# 본문 성격의 요소 카테고리. 개수를 세어 봐야 방식 간 비교에 쓸 데가 없고
# 캡션만 길어져서 접는다 (구조를 말해 주는 Table/Title/Image 는 남긴다).
_META_SKIP = {"UncategorizedText", "NarrativeText"}


def short_meta(meta: dict) -> dict:
    """캡션에 실을 meta 만 남긴다."""
    return {
        k: v
        for k, v in meta.items()
        if k not in _META_SKIP
        # "그림 개수" 와 "Image" 는 같은 값을 두 번 말한다. 둘 다 있으면 하나만.
        and not (k == "그림 개수" and "Image" in meta)
    }


def render_tables(res: dict, max_tables: int) -> None:
    tables = res.get("tables") or []
    st.markdown(f"**표 추출 (처음 {max_tables}개)**")
    if not tables:
        # 왜 안 잡혔는지를 추출기가 남겨 뒀으면 그대로 띄운다. 방식마다 이유가
        # 다르고(전략·괘선·모델), 그 이유가 곧 이 비교 데모의 핵심이다.
        note = res.get("tables_note")
        if note:
            st.info(note)
        else:
            st.caption("이 방식으로는 표가 추출되지 않았습니다.")
        return

    for i, rows in enumerate(tables, 1):
        if not rows:
            continue
        st.caption(f"표 {i} · {len(rows)}행 × {len(rows[0])}열")
        header, body = rows[0], rows[1:]
        if body:
            width = len(header)
            body = [(r + [""] * width)[:width] for r in body]
        else:
            header, body = [f"열 {j + 1}" for j in range(len(rows[0]))], rows
        st.markdown(html_table(header, body), unsafe_allow_html=True)


def render_result(res: dict, view: str, show_tables: bool, max_tables: int) -> None:
    if not res.get("ok"):
        st.error(f"**{res.get('backend', '-')}** 추출 실패")
        st.code(res.get("error") or "-", language="text")
        return

    # 분량 지표는 모두 마크업을 걷어낸 본문 기준이다 (export 시점에 계산된 값).
    stats = [
        ("소요 시간", f"{res['elapsed']:.2f}s"),
        ("본문 문자", f"{res['n_chars']:,}"),
        ("원문 문자", f"{res['n_raw_chars']:,}"),
        ("단어 수", f"{res['n_words']:,}"),
        ("줄 수", f"{res['n_lines']:,}"),
    ]
    meta = short_meta(res.get("meta") or {})
    meta_txt = "  ·  ".join(f"{esc(k)}: {esc(v)}" for k, v in meta.items())
    # 지표와 백엔드 줄을 한 블록으로 낸다. 나눠서 내면 방식마다 캡션 줄 수가 달라져
    # 아래 본문 박스의 시작 높이가 열마다 어긋난다.
    st.markdown(
        '<div class="res-head"><div class="stat-row">'
        + "".join(
            f'<div class="stat"><span class="k">{esc(k)}</span>'
            f'<span class="v">{esc(v)}</span></div>'
            for k, v in stats
        )
        + '</div><div class="backend">백엔드: '
        + f"<code>{esc(short_backend(res['backend']))}</code>"
        + (f"  ·  {meta_txt}" if meta_txt else "")
        + "</div></div>",
        unsafe_allow_html=True,
    )

    text = res.get("text") or ""
    # 텍스트가 없어도 박스는 그대로 둔다. 여기서 빼 버리면 그 열만 위로 당겨져
    # 아래 "표 추출"이 다른 열과 어긋난다.
    with st.container(height=TEXT_BOX_H, border=True):
        if not text.strip():
            st.warning("추출된 텍스트가 없습니다.")
        elif view == "렌더링" and res.get("is_markdown"):
            st.markdown(text)
        else:
            # 렌더링 모드든 원본 모드든 바깥 컨테이너가 같아야 높이가 정확히 맞는다.
            st.markdown(
                f'<div class="text-plain">{esc(text)}</div>', unsafe_allow_html=True
            )

    if show_tables:
        render_tables(res, max_tables)


# ------------------------------------------------------------------ 데이터 적재

if not RESULTS_PATH.exists():
    st.title(TITLE)
    st.error("`results.json` 이 없습니다.")
    st.markdown(
        "이 화면은 미리 계산해 둔 결과만 보여 줍니다. 로컬에서 아래를 실행해 "
        "`results.json` 을 만든 뒤 함께 커밋하세요."
    )
    st.code("python export_results.py", language="bash")
    st.stop()

data = load_results(str(RESULTS_PATH), RESULTS_PATH.stat().st_mtime)
documents = {d["name"]: d for d in data["documents"]}
methods_info = data["methods"]
max_tables = data.get("max_tables", 2)

# ------------------------------------------------------------------ sidebar

st.sidebar.title("⚙️ 설정")
selected_names = st.sidebar.multiselect(
    "대상 문서", list(documents), default=list(documents)
)
selected_methods = st.sidebar.multiselect(
    "추출 방식", list(methods_info), default=list(methods_info)
)
view = st.sidebar.radio("보기 모드", ["렌더링", "원본 텍스트"], horizontal=True)

st.sidebar.divider()
st.sidebar.caption(
    f"미리 계산된 결과입니다 (추출 시각: {data['generated_at']}).\n\n"
    "이 화면은 추출을 실행하지 않습니다. 다른 문서를 넣거나 다시 측정하려면 "
    "로컬에서 `streamlit run app-local.py` 로 돌린 뒤 "
    "`python export_results.py` 로 결과를 갱신하세요."
)

# --------------------------------------------------------------------- main

st.title(TITLE)
st.markdown(
    f"동일한 문서를 **{len(methods_info)}가지 추출 방식**으로 처리한 결과를 비교합니다. "
    f"소요 시간을 포함한 모든 수치는 로컬 실측값이며, 여기서는 그 값을 그대로 보여 줍니다."
)

with st.expander("추출 방식 설명", expanded=False):
    for name, info in methods_info.items():
        st.markdown(f"**{name}** — {info['desc']}  \n&nbsp;&nbsp;↳ {info['backends']}")

if not selected_names or not selected_methods:
    st.info("사이드바에서 문서와 추출 방식을 하나 이상 선택하세요.")
    st.stop()


# ----------------------------------------------------------------- 결과 분석


def render_analysis() -> None:
    """방식별 속도 배수·이미지 인식 여부를 모아 권장 조합을 제시한다."""
    # 선택한 문서 전부의 합계로 본다. 문서 하나만 보면 짧은 문서에서 모델 초기화
    # 비용이 과대 반영돼 배수가 널뛴다.
    totals, samples = {}, {}
    for m in selected_methods:
        rs = [
            r
            for n in selected_names
            if (r := documents[n]["results"].get(m)) and r.get("ok")
        ]
        if rs:
            totals[m] = sum(r["elapsed"] for r in rs)
            samples[m] = rs[0]

    if not totals:
        return

    baseline = data.get("baseline_method")
    base_method = baseline if baseline in totals else min(totals, key=totals.get)
    base = totals[base_method] or 1e-9
    # 실제 백엔드 이름 하나만 쓴다. PDF/DOCX 를 섞어 골랐을 때 "PyMuPDF /
    # python-docx" 로 늘어놓으면 열 제목과 문장이 지저분해지므로 PDF 쪽을 대표로 쓴다.
    pdf_doc = next(
        (n for n in selected_names if documents[n]["ext"] == ".pdf"), selected_names[0]
    )
    base_res = documents[pdf_doc]["results"].get(base_method)
    base_name = (base_res or {}).get("label") or base_method

    rows = []
    for m, secs in sorted(totals.items(), key=lambda kv: kv[1]):
        rows.append(
            [
                m,
                f"{secs:.2f}",
                "기준 (1×)" if m == base_method else f"{secs / base:.1f}×",
                samples[m].get("image_mark", "-"),
                methods_info[m]["cells"],
            ]
        )

    doc_word = (
        f"선택한 문서 {len(selected_names)}건" if len(selected_names) > 1 else "선택한 문서"
    )
    # Docling 을 안 골랐으면 배수를 지어낼 수 없으니 그렇게 말한다.
    docling_clause = (
        f"**{totals['Docling'] / base:.1f}배**가 걸립니다"
        if "Docling" in totals
        else "훨씬 오래 걸립니다 (이번 비교에서는 Docling 이 빠져 측정값 없음)"
    )

    with st.expander("결과 분석", expanded=False):
        st.caption(
            f"{doc_word} 기준 합계이며, 로컬 최초 추출에 걸린 시간입니다. "
            "Docling 의 첫 실행에는 레이아웃 모델 로딩 시간이 포함됩니다."
        )
        st.markdown(
            html_table(
                ["추출 방식", "총 소요(s)", f"{base_name} 대비", "이미지 인식", "표 내부 인식"],
                rows,
                numeric={1},
                extra_class="summary",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
**1. 시간 중심 방안 — {base_name} + Docling**
본문은 {base_name} 로 뽑고, 표·그림이 실제로 필요한 문서(또는 페이지)만 Docling 으로 보강합니다.
Docling 은 {base_name} 대비 {docling_clause}. 둘을 나눠 쓰는 게 비용 대비 손실이 가장 적습니다.

**2. 품질 중심 방안 — Docling**
네 방식 중 그림 영역을 인식하는 유일한 방식이고, 표를 셀 구조 그대로 복원하며 읽기 순서까지 맞춰 줍니다.
시간을 감당할 수 있다면 Docling 단독이 정보 손실이 가장 적습니다.

**3. 선택적 추출 모델**
사용자가 지정한 문서의 중요도에 따라 다른 추출 모델 사용 선택지 
"""
        )


render_analysis()


# ----------------------------------------------------------------- 문서별 결과

SUMMARY_HEADER = [
    "추출 방식",
    "상태",
    "소요(s)",
    "본문 문자",
    "원문 문자",
    "단어 수",
    "줄 수",
    "표",
]


def summary_rows(doc: dict) -> list[list]:
    rows = []
    for m in selected_methods:
        r = doc["results"].get(m)
        label = (r or {}).get("label") or m
        if r is None:
            rows.append([label, "— 미측정", "–", "–", "–", "–", "–", "–"])
        elif not r.get("ok"):
            rows.append([label, "❌ 실패", "–", "–", "–", "–", "–", "–"])
        else:
            rows.append(
                [
                    label,
                    "✅ 완료",
                    f"{r['elapsed']:.2f}",
                    f"{r['n_chars']:,}",
                    f"{r['n_raw_chars']:,}",
                    f"{r['n_words']:,}",
                    f"{r['n_lines']:,}",
                    len(r.get("tables") or []),
                ]
            )
    return rows


for name in selected_names:
    doc = documents[name]
    st.divider()
    st.header(f"📘 {name}")
    st.caption(f"{doc['ext'].lstrip('.').upper()} · {human_size(doc['size'])}")

    st.markdown("**요약 비교**")
    st.markdown(
        html_table(
            SUMMARY_HEADER,
            summary_rows(doc),
            numeric=set(range(2, 8)),
            extra_class="summary",
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "**본문 문자** = 표 정렬 공백·Markdown 기호·HTML 태그·페이지 표시를 걷어낸 "
        "실제 내용의 글자 수(공백 제외)로, 방식 간 분량 비교는 이 값으로 합니다. "
        "**원문 문자**는 마크업을 포함한 추출 결과 그대로의 길이입니다."
    )

    # 방식 수만큼 열을 만들어 항상 나란히 놓는다. 클릭 없이 전부 한눈에 보인다.
    for col, method in zip(st.columns(len(selected_methods)), selected_methods):
        with col:
            res = doc["results"].get(method)
            st.subheader((res or {}).get("label") or method, anchor=False)
            if res is None:
                st.info("이 문서에는 이 방식의 측정 결과가 없습니다.")
                continue
            # 표 미리보기는 PDF 결과에만 붙인다.
            render_result(
                res, view, show_tables=doc["ext"] == ".pdf", max_tables=max_tables
            )
