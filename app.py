"""문서 추출 비교 데모 — 앱 폴더에 있는 PDF/DOCX를 여러 방식으로 추출해 비교한다.

대상 파일은 고정돼 있지 않다. app.py 옆에 놓인 .pdf/.docx 를 그대로 집어오므로,
파일을 갈아끼우면 코드 수정 없이 그 파일들을 비교한다.

실행: streamlit run app.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from extractors import EXTRACTORS, MAX_TABLES, image_support, method_label

BASE_DIR = Path(__file__).parent
SUPPORTED_EXTS = (".pdf", ".docx")
# 속도 비교의 기준이 되는 방식 (PDF면 PyMuPDF, DOCX면 python-docx).
BASELINE_METHOD = "PyMuPDF / python-docx"
# ExtractResult 구조가 바뀌면 올린다 (디스크 캐시 무효화용).
RESULT_SCHEMA = 4

st.set_page_config(page_title="문서 추출 비교", page_icon="📄", layout="wide")

# st.metric 은 값 글꼴이 2.25rem 으로 고정이라 열이 좁으면 "12…" 처럼 잘린다.
# 숫자가 절대 잘리지 않도록 직접 그린다: 글꼴을 줄이고, 좁으면 잘리는 대신
# 다음 줄로 흐르게 하며(flex-wrap), 각 값 내부는 줄바꿈을 막는다(nowrap).
st.markdown(
    """
    <style>
      .stat-row { display:flex; flex-wrap:wrap; gap:.15rem .9rem;
                  margin:.1rem 0 .5rem; }
      .stat-row .stat { display:flex; flex-direction:column; line-height:1.2; }
      .stat-row .k { font-size:.8rem; opacity:.65; white-space:nowrap; }
      .stat-row .v { font-size:1.25rem; font-weight:600; white-space:nowrap;
                     font-variant-numeric:tabular-nums; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ helpers


@st.cache_data(show_spinner=False, persist="disk")
def run_extract(path: str, method: str, mtime: float, schema: int):
    """추출 실행 (파일 경로 + 수정시각 + 방식 + 스키마 버전 기준으로 캐시).

    Docling PDF는 수 분이 걸리므로 디스크에 캐시해 앱 재시작 후에도 재사용한다.
    `schema` 는 ExtractResult 구조가 바뀔 때 올린다. 디스크 캐시에는 예전 구조로
    피클된 객체가 남아 있어, 올리지 않으면 새 필드가 없는 객체가 돌아온다.
    """
    return EXTRACTORS[method]["fn"](path)


def file_options() -> dict[str, Path]:
    """app.py 옆에 있는 PDF/DOCX 를 이름순으로 모은다."""
    return {
        p.name: p
        for p in sorted(BASE_DIR.glob("*"))
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    }


def default_selection(opts: dict[str, Path]) -> list[str]:
    """확장자별 첫 파일 하나씩(PDF 1 + DOCX 1)을 기본 선택으로 삼는다.

    파일을 여러 개 넣어둔 경우 전부 자동 실행하면 Docling 때문에 비싸므로,
    포맷별 대표 하나씩만 켜두고 나머지는 사용자가 고르게 한다.
    """
    picked = []
    for ext in SUPPORTED_EXTS:
        first = next((n for n, p in opts.items() if p.suffix.lower() == ext), None)
        if first:
            picked.append(first)
    return picked or list(opts)[:2]


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def render_tables(res, key: str):
    """추출된 표 중 앞의 몇 개를 표 형태로 보여준다."""
    tables = getattr(res, "tables", [])
    st.markdown(f"**표 추출 (처음 {MAX_TABLES}개)**")
    if not tables:
        # 왜 안 잡혔는지를 추출기가 알려주면 그대로 띄운다. 방식마다 이유가
        # 다르고(전략·괘선·모델), 그 이유가 곧 이 비교 데모의 핵심이다.
        note = getattr(res, "tables_note", None)
        if note:
            st.info(note)
        else:
            st.caption("이 방식으로는 표가 추출되지 않았습니다.")
        return

    for i, rows in enumerate(tables, 1):
        if not rows:
            continue
        header, body = rows[0], rows[1:]
        if body:
            # 중복 헤더명은 Arrow 직렬화에서 문제가 되므로 접미사로 구분한다.
            seen, cols = {}, []
            for c in header:
                c = c or "-"
                seen[c] = seen.get(c, 0) + 1
                cols.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
            width = len(cols)
            df = pd.DataFrame(
                [(r + [""] * width)[:width] for r in body], columns=cols
            )
        else:
            df = pd.DataFrame(rows)
        st.caption(f"표 {i} · {len(rows)}행 × {len(rows[0])}열")
        st.dataframe(df, hide_index=True, width="stretch", key=f"tb_{key}_{i}")


def render_result(res, key: str, view: str, show_tables: bool):
    if not res.ok:
        st.error(f"**{res.backend}** 추출 실패")
        # 리눅스 배포(Streamlit Cloud 등)에서 가장 흔한 실패라 조치법을 같이 띄운다.
        if "libGL.so.1" in (res.error or ""):
            st.info(
                "OpenCV(`opencv-python`)가 쓰는 시스템 라이브러리가 배포 환경에 없습니다.\n\n"
                "레포 **루트**(app.py 와 같은 위치)에 `packages.txt` 파일을 만들고 "
                "`libgl1` 한 줄만 넣어 커밋·push 하세요. "
                "`libglib2.0-0` 은 넣지 마세요 — Streamlit Cloud 는 Debian Bullseye 라 "
                "의존성 문제로 apt 단계 전체가 실패합니다."
            )
        st.code(res.error, language="text")
        return

    # 분량 지표는 모두 마크업을 걷어낸 본문 기준이다 (extractors.clean_text).
    # 원문 그대로 세면 Docling 의 표 정렬 공백이나 unstructured 의 <table> 태그가
    # 그대로 글자 수에 잡혀 방식 간 비교가 불가능해진다.
    stats = [
        ("소요 시간", f"{res.elapsed:.2f}s"),
        ("본문 문자", f"{res.n_chars:,}"),
        ("원문 문자", f"{res.n_raw_chars:,}"),
        ("단어 수", f"{res.n_words:,}"),
        ("줄 수", f"{res.n_lines:,}"),
    ]
    st.markdown(
        '<div class="stat-row">'
        + "".join(
            f'<div class="stat"><span class="k">{k}</span>'
            f'<span class="v">{v}</span></div>'
            for k, v in stats
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    st.caption(f"백엔드: `{res.backend}`" + ("  ·  " + "  ·  ".join(f"{k}: {v}" for k, v in res.meta.items()) if res.meta else ""))

    if not res.text.strip():
        st.warning("추출된 텍스트가 없습니다.")
        return

    if view == "렌더링" and res.is_markdown:
        with st.container(height=520, border=True):
            st.markdown(res.text)
    else:
        st.text_area("추출 결과", res.text, height=520, key=f"ta_{key}", label_visibility="collapsed")

    if show_tables:
        render_tables(res, key)


# ------------------------------------------------------------------ sidebar

st.sidebar.title("⚙️ 설정")

opts = file_options()
uploaded = st.sidebar.file_uploader("직접 업로드 (선택)", type=["pdf", "docx"])

if uploaded is not None:
    tmp = Path(tempfile.gettempdir()) / "doc_extract_demo"
    tmp.mkdir(exist_ok=True)
    target = tmp / uploaded.name
    target.write_bytes(uploaded.getbuffer())
    opts = {uploaded.name: target, **opts}
    default_names = [uploaded.name]
else:
    default_names = default_selection(opts)

if not opts:
    st.error(
        f"`{BASE_DIR}` 안에 PDF/DOCX 파일이 없습니다. "
        "app.py 옆에 파일을 두거나 위에서 직접 업로드하세요."
    )
    st.stop()

selected_names = st.sidebar.multiselect("대상 문서", list(opts), default=default_names)
selected_methods = st.sidebar.multiselect(
    "추출 방식", list(EXTRACTORS), default=list(EXTRACTORS)
)
view = st.sidebar.radio("보기 모드", ["렌더링", "원본 텍스트"], horizontal=True)

st.sidebar.divider()
if st.sidebar.button("🔄 캐시 비우고 다시 추출", width="stretch"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(
    "Docling은 최초 실행 시 레이아웃 모델을 내려받아 수십 초~수 분이 걸릴 수 있습니다. "
    "unstructured도 PDF를 hi_res로 처리할 때는 페이지를 이미지로 렌더링해 OCR까지 돌리므로 느립니다."
)

# --------------------------------------------------------------------- main

st.title("📄 문서 내용 추출 비교")
st.markdown(
    f"동일한 문서를 **{len(EXTRACTORS)}가지 추출 방식**으로 처리한 결과를 비교합니다. "
    "빠른 방식부터 순서대로, 끝나는 즉시 화면에 채워집니다."
)

with st.expander("추출 방식 설명", expanded=False):
    for name, info in EXTRACTORS.items():
        st.markdown(f"**{name}** — {info['desc']}  \n&nbsp;&nbsp;↳ {info['backends']}")

# 소요 시간은 다 돌려봐야 나오므로 자리만 잡아두고 맨 마지막에 채운다.
analysis_slot = st.empty()

if not selected_names or not selected_methods:
    st.info("사이드바에서 문서와 추출 방식을 하나 이상 선택하세요.")
    st.stop()

# 1단계: 결과를 기다리지 않고 문서/방식별 자리표시자부터 모두 그린다.
slots: dict[tuple[str, str], "st.delta_generator.DeltaGenerator"] = {}
summary_slots: dict[str, "st.delta_generator.DeltaGenerator"] = {}
summary_captions: dict[str, "st.delta_generator.DeltaGenerator"] = {}

for name in selected_names:
    path = opts[name]
    st.divider()
    st.header(f"📘 {name}")
    st.caption(f"`{path}` · {human_size(path.stat().st_size)}")

    st.markdown("**요약 비교**")
    summary_slots[name] = st.empty()
    summary_captions[name] = st.empty()

    # 방식 수만큼 열을 만들어 항상 나란히 놓는다. 클릭 없이 전부 한눈에 보이고,
    # 각 칸은 자기 추출이 끝나는 즉시 그 자리에서 채워진다.
    for col, method in zip(st.columns(len(selected_methods)), selected_methods):
        with col:
            st.subheader(method_label(method, path), anchor=False)
            slots[(name, method)] = st.empty()

results: dict[tuple[str, str], object] = {}


def refresh_summary(doc: str) -> None:
    """해당 문서의 요약 표를 현재까지 끝난 결과만으로 다시 그린다."""
    rows = []
    for m in selected_methods:
        label = method_label(m, opts[doc])
        r = results.get((doc, m))
        if r is None:
            rows.append({"추출 방식": label, "상태": "⏳ 대기 중"})
        elif not r.ok:
            rows.append({"추출 방식": label, "상태": "❌ 실패"})
        else:
            rows.append(
                {
                    "추출 방식": label,
                    "상태": "✅ 완료",
                    "소요(s)": round(r.elapsed, 2),
                    "본문 문자": r.n_chars,
                    "원문 문자": r.n_raw_chars,
                    "단어 수": r.n_words,
                    "줄 수": r.n_lines,
                    "표": len(getattr(r, "tables", [])),
                }
            )
    summary_slots[doc].dataframe(rows, hide_index=True, width="stretch")
    summary_captions[doc].caption(
        "**본문 문자** = 표 정렬 공백·Markdown 기호·HTML 태그·페이지 표시를 걷어낸 "
        "실제 내용의 글자 수(공백 제외)로, 방식 간 분량 비교는 이 값으로 합니다. "
        "**원문 문자**는 마크업을 포함한 추출 결과 그대로의 길이입니다."
    )


for doc in selected_names:
    for method in selected_methods:
        slots[(doc, method)].info("⏳ 대기 중…")
    refresh_summary(doc)

# 2단계: 빠른 방식부터 실행해, 하나 끝날 때마다 그 자리표시자를 즉시 채운다.
tasks = sorted(
    ((d, m) for d in selected_names for m in selected_methods),
    key=lambda t: (EXTRACTORS[t[1]]["cost"], selected_names.index(t[0])),
)

progress = st.sidebar.progress(0.0, text=f"0 / {len(tasks)} 완료")

for i, (doc, method) in enumerate(tasks, 1):
    path = opts[doc]
    slot = slots[(doc, method)]
    label = method_label(method, path)
    with slot.container():
        with st.spinner(f"{label} 추출 중…"):
            res = run_extract(
                str(path), method, path.stat().st_mtime, RESULT_SCHEMA
            )

    results[(doc, method)] = res
    slug = "".join(ch if ch.isalnum() else "_" for ch in doc)
    with slot.container():
        # 표 미리보기는 PDF 결과에만 붙인다.
        render_result(
            res, f"{slug}_{method}", view, show_tables=path.suffix.lower() == ".pdf"
        )
    refresh_summary(doc)
    progress.progress(i / len(tasks), text=f"{i} / {len(tasks)} 완료")

progress.empty()


# ----------------------------------------------------------------- 결과 분석


def render_analysis() -> None:
    """방식별 속도 배수·이미지 인식 여부를 모아 권장 조합을 제시한다."""
    # 선택한 문서 전부의 합계로 본다. 문서 하나만 보면 짧은 문서에서 모델 초기화
    # 비용이 과대 반영돼 배수가 널뛴다.
    totals, done = {}, {}
    for m in selected_methods:
        rs = [
            r for d in selected_names if (r := results.get((d, m))) is not None and r.ok
        ]
        if rs:
            totals[m] = sum(r.elapsed for r in rs)
            done[m] = rs

    if not totals:
        return

    base_method = BASELINE_METHOD if BASELINE_METHOD in totals else min(totals, key=totals.get)
    base = totals[base_method] or 1e-9
    # 실제 백엔드 이름 하나만 쓴다. PDF/DOCX 를 섞어 골랐을 때 "PyMuPDF /
    # python-docx" 로 늘어놓으면 열 제목과 문장이 지저분해지므로 PDF 쪽을 대표로 쓴다.
    sample = next(
        (opts[d] for d in selected_names if opts[d].suffix.lower() == ".pdf"),
        opts[selected_names[0]],
    )
    base_name = method_label(base_method, sample)

    rows = []
    for m, secs in sorted(totals.items(), key=lambda kv: kv[1]):
        # 이미지 인식은 그 방식의 결과 중 아무거나로 검출 실적을 대표시킨다.
        mark, _ = image_support(m, done[m][0])
        rows.append(
            {
                "추출 방식": m,
                "총 소요(s)": round(secs, 2),
                f"{base_name} 대비": "기준 (1×)" if m == base_method else f"{secs / base:.1f}×",
                "이미지 인식": mark,
                "표 내부 인식": EXTRACTORS[m]["cells"],
            }
        )

    doc_word = f"선택한 문서 {len(selected_names)}건" if len(selected_names) > 1 else "선택한 문서"
    # Docling 을 안 골랐으면 배수를 지어낼 수 없으니 그렇게 말한다.
    docling_clause = (
        f"**{totals['Docling'] / base:.1f}배**가 걸립니다"
        if "Docling" in totals
        else "훨씬 오래 걸립니다 (이번 실행에서는 Docling 을 선택하지 않아 측정값 없음)"
    )

    with analysis_slot.container():
        with st.expander("결과 분석", expanded=False):
            st.caption(
                f"{doc_word} 기준 합계이며, 캐시된 결과는 최초 추출에 걸린 시간입니다. "
                "Docling 의 첫 실행에는 레이아웃 모델 로딩 시간이 포함됩니다."
            )
            st.dataframe(rows, hide_index=True, width="stretch")
            st.markdown(
                f"""
** 1. 시간 중심 방안 — {base_name} + Docling**
본문은 {base_name} 로 뽑고, 표·그림이 실제로 필요한 문서(또는 페이지)만 Docling 으로 보강합니다.
둘을 나눠 쓰는 게 비용 대비 손실이 가장 적습니다.

** 2. 품질 중심 방안 — Docling**
네 방식 중 그림 영역을 인식하는 유일한 방식이고, 표를 셀 구조 그대로 복원하며 읽기 순서까지 맞춰 줍니다.
시간을 감당할 수 있다면 Docling 단독이 정보 손실이 가장 적습니다.
"""
            )


render_analysis()
