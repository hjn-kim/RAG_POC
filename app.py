"""문서 추출 비교 데모 — PDF/DOCX를 3가지 방식으로 추출해 결과를 비교한다.

실행: streamlit run app.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from extractors import EXTRACTORS

BASE_DIR = Path(__file__).parent
DEFAULT_FILES = ["ATLAS___EACL027.pdf", "논문번역.docx"]

st.set_page_config(page_title="문서 추출 비교", page_icon="📄", layout="wide")


# ------------------------------------------------------------------ helpers


@st.cache_data(show_spinner=False, persist="disk")
def run_extract(path: str, method: str, mtime: float):
    """추출 실행 (파일 경로 + 수정시각 + 방식 기준으로 캐시).

    Docling PDF는 수 분이 걸리므로 디스크에 캐시해 앱 재시작 후에도 재사용한다.
    """
    return EXTRACTORS[method]["fn"](path)


def file_options() -> dict[str, Path]:
    opts = {}
    for name in DEFAULT_FILES:
        p = BASE_DIR / name
        if p.exists():
            opts[name] = p
    for p in sorted(BASE_DIR.glob("*")):
        if p.suffix.lower() in (".pdf", ".docx") and p.name not in opts:
            opts[p.name] = p
    return opts


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def render_result(res, key: str, view: str):
    if not res.ok:
        st.error(f"**{res.backend}** 추출 실패")
        st.code(res.error, language="text")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("소요 시간", f"{res.elapsed:.2f} s")
    c2.metric("문자 수", f"{res.n_chars:,}")
    c3.metric("단어 수", f"{res.n_words:,}")
    c4.metric("줄 수", f"{res.n_lines:,}")

    st.caption(f"백엔드: `{res.backend}`" + ("  ·  " + "  ·  ".join(f"{k}: {v}" for k, v in res.meta.items()) if res.meta else ""))

    if not res.text.strip():
        st.warning("추출된 텍스트가 없습니다.")
        return

    if view == "렌더링" and res.is_markdown:
        with st.container(height=520, border=True):
            st.markdown(res.text)
    else:
        st.text_area("추출 결과", res.text, height=520, key=f"ta_{key}", label_visibility="collapsed")

    st.download_button(
        "⬇ 결과 다운로드",
        res.text,
        file_name=f"{key}.md" if res.is_markdown else f"{key}.txt",
        mime="text/plain",
        key=f"dl_{key}",
    )


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

if not opts:
    st.error(f"`{BASE_DIR}` 안에 PDF/DOCX 파일이 없습니다.")
    st.stop()

selected_names = st.sidebar.multiselect(
    "대상 문서", list(opts), default=list(opts)[: len(DEFAULT_FILES)]
)
selected_methods = st.sidebar.multiselect(
    "추출 방식", list(EXTRACTORS), default=list(EXTRACTORS)
)
view = st.sidebar.radio("보기 모드", ["렌더링", "원본 텍스트"], horizontal=True)
layout = st.sidebar.radio("배치", ["나란히 비교", "탭"], horizontal=True)

st.sidebar.divider()
if st.sidebar.button("🔄 캐시 비우고 다시 추출", width="stretch"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(
    "Docling은 최초 실행 시 레이아웃 모델을 내려받아 수십 초~수 분이 걸릴 수 있습니다."
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

if not selected_names or not selected_methods:
    st.info("사이드바에서 문서와 추출 방식을 하나 이상 선택하세요.")
    st.stop()

# 1단계: 결과를 기다리지 않고 문서/방식별 자리표시자부터 모두 그린다.
slots: dict[tuple[str, str], "st.delta_generator.DeltaGenerator"] = {}
summary_slots: dict[str, "st.delta_generator.DeltaGenerator"] = {}

for name in selected_names:
    path = opts[name]
    st.divider()
    st.header(f"📘 {name}")
    st.caption(f"`{path}` · {human_size(path.stat().st_size)}")

    st.markdown("**요약 비교**")
    summary_slots[name] = st.empty()

    if layout == "나란히 비교":
        for col, method in zip(st.columns(len(selected_methods)), selected_methods):
            with col:
                st.subheader(method, anchor=False)
                slots[(name, method)] = st.empty()
    else:
        for tab, method in zip(st.tabs(selected_methods), selected_methods):
            with tab:
                slots[(name, method)] = st.empty()

results: dict[tuple[str, str], object] = {}


def refresh_summary(doc: str) -> None:
    """해당 문서의 요약 표를 현재까지 끝난 결과만으로 다시 그린다."""
    rows = []
    for m in selected_methods:
        r = results.get((doc, m))
        if r is None:
            rows.append({"추출 방식": m, "상태": "⏳ 대기 중", "백엔드": "-"})
        elif not r.ok:
            rows.append({"추출 방식": m, "상태": "❌ 실패", "백엔드": r.backend})
        else:
            rows.append(
                {
                    "추출 방식": m,
                    "상태": "✅ 완료",
                    "백엔드": r.backend,
                    "소요(s)": round(r.elapsed, 2),
                    "문자 수": r.n_chars,
                    "단어 수": r.n_words,
                    "줄 수": r.n_lines,
                }
            )
    summary_slots[doc].dataframe(rows, hide_index=True, width="stretch")


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
    with slot.container():
        with st.spinner(f"{method} 추출 중…"):
            res = run_extract(str(path), method, path.stat().st_mtime)

    results[(doc, method)] = res
    slug = "".join(ch if ch.isalnum() else "_" for ch in doc)
    with slot.container():
        render_result(res, f"{slug}_{method}", view)
    refresh_summary(doc)
    progress.progress(i / len(tasks), text=f"{i} / {len(tasks)} 완료")

progress.empty()
