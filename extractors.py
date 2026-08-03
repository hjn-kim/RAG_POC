"""문서(PDF/DOCX) 텍스트 추출기 3종.

각 추출기는 `extract(path) -> ExtractResult` 형태로 동작하며,
PDF와 DOCX를 각각 다른 백엔드로 처리한다.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractResult:
    method: str
    backend: str
    text: str = ""
    elapsed: float = 0.0
    is_markdown: bool = False
    meta: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def n_chars(self) -> int:
        return len(self.text)

    @property
    def n_words(self) -> int:
        return len(self.text.split())

    @property
    def n_lines(self) -> int:
        return self.text.count("\n") + 1 if self.text else 0


def _ext(path: str | Path) -> str:
    return Path(path).suffix.lower()


# ---------------------------------------------------------------- 1. Docling

_DOCLING_CONVERTER = None


def _docling_converter():
    global _DOCLING_CONVERTER
    if _DOCLING_CONVERTER is None:
        from docling.document_converter import DocumentConverter

        _DOCLING_CONVERTER = DocumentConverter()
    return _DOCLING_CONVERTER


def extract_docling(path: str | Path) -> ExtractResult:
    """Docling: 레이아웃/표 구조를 인식해 Markdown으로 변환."""
    res = ExtractResult(method="Docling", backend="docling.DocumentConverter", is_markdown=True)
    t0 = time.perf_counter()
    try:
        conv = _docling_converter().convert(str(path))
        doc = conv.document
        res.text = doc.export_to_markdown()
        res.meta = {
            "페이지 수": getattr(doc, "num_pages", lambda: "-")()
            if callable(getattr(doc, "num_pages", None))
            else "-",
            "표 개수": len(getattr(doc, "tables", []) or []),
            "그림 개수": len(getattr(doc, "pictures", []) or []),
        }
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=3)}"
    res.elapsed = time.perf_counter() - t0
    return res


# ------------------------------------------- 2. PyMuPDF / python-docx (네이티브)


def _extract_pymupdf(path: str | Path, res: ExtractResult) -> None:
    import pymupdf

    parts = []
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, 1):
            parts.append(f"--- [page {i}] ---\n{page.get_text('text')}")
        res.meta = {"페이지 수": doc.page_count, "메타데이터 제목": doc.metadata.get("title") or "-"}
    res.text = "\n".join(parts)


def _extract_python_docx(path: str | Path, res: ExtractResult) -> None:
    import docx

    d = docx.Document(str(path))
    parts = []
    for p in d.paragraphs:
        if not p.text.strip():
            continue
        style = (p.style.name or "").lower()
        if style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            parts.append(f"{'#' * min(int(level), 6)} {p.text}")
        else:
            parts.append(p.text)
    for ti, table in enumerate(d.tables, 1):
        parts.append(f"\n[표 {ti}]")
        for row in table.rows:
            parts.append(" | ".join(c.text.replace("\n", " ").strip() for c in row.cells))
    res.text = "\n\n".join(parts)
    res.meta = {"문단 수": len(d.paragraphs), "표 개수": len(d.tables), "섹션 수": len(d.sections)}


def extract_native(path: str | Path) -> ExtractResult:
    """PDF는 PyMuPDF, DOCX는 python-docx로 원문 텍스트를 그대로 추출."""
    ext = _ext(path)
    backend = "PyMuPDF (fitz)" if ext == ".pdf" else "python-docx"
    res = ExtractResult(method="PyMuPDF / python-docx", backend=backend)
    t0 = time.perf_counter()
    try:
        if ext == ".pdf":
            _extract_pymupdf(path, res)
        elif ext == ".docx":
            _extract_python_docx(path, res)
            res.is_markdown = True
        else:
            raise ValueError(f"지원하지 않는 확장자: {ext}")
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=3)}"
    res.elapsed = time.perf_counter() - t0
    return res


# ------------------------------------------------ 3. pdfplumber / mammoth


def _extract_pdfplumber(path: str | Path, res: ExtractResult) -> None:
    import pdfplumber

    parts, n_tables = [], 0
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            parts.append(f"--- [page {i}] ---")
            parts.append(page.extract_text(layout=False) or "")
            for table in page.extract_tables() or []:
                n_tables += 1
                parts.append(f"\n[표 {n_tables}]")
                for row in table:
                    parts.append(" | ".join((c or "").replace("\n", " ").strip() for c in row))
        res.meta = {"페이지 수": len(pdf.pages), "추출된 표": n_tables}
    res.text = "\n".join(parts)


def _extract_mammoth(path: str | Path, res: ExtractResult) -> None:
    import mammoth

    with open(path, "rb") as f:
        out = mammoth.convert_to_markdown(f)
    res.text = out.value
    res.is_markdown = True
    res.meta = {"변환 경고": len(out.messages)}
    if out.messages:
        res.meta["경고 예시"] = str(out.messages[0])[:120]


def extract_layout(path: str | Path) -> ExtractResult:
    """PDF는 pdfplumber(표 검출), DOCX는 mammoth(스타일→Markdown)."""
    ext = _ext(path)
    backend = "pdfplumber" if ext == ".pdf" else "mammoth"
    res = ExtractResult(method="pdfplumber / mammoth", backend=backend)
    t0 = time.perf_counter()
    try:
        if ext == ".pdf":
            _extract_pdfplumber(path, res)
        elif ext == ".docx":
            _extract_mammoth(path, res)
        else:
            raise ValueError(f"지원하지 않는 확장자: {ext}")
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=3)}"
    res.elapsed = time.perf_counter() - t0
    return res


# ------------------------------------------------------- 4. unstructured

# unstructured 요소 카테고리 → Markdown 표현
_UNS_MARKUP = {
    "Title": "## {}",
    "Header": "### {}",
    "ListItem": "- {}",
    "Footer": "_{}_",
    "PageBreak": "\n---\n",
}


def extract_unstructured(path: str | Path) -> ExtractResult:
    """unstructured: 문서를 의미 단위 요소(Title/NarrativeText/Table…)로 분해."""
    res = ExtractResult(
        method="unstructured", backend="unstructured.partition", is_markdown=True
    )
    t0 = time.perf_counter()
    try:
        from unstructured.partition.auto import partition

        kwargs = {}
        if _ext(path) == ".pdf":
            # hi_res 는 poppler/tesseract 외부 바이너리를 요구한다. 미설치 환경이라
            # pdfminer 기반 fast 전략을 쓴다 (OCR 없음, 텍스트 레이어만).
            kwargs["strategy"] = "fast"
            res.backend = "unstructured (strategy=fast)"

        elements = partition(filename=str(path), **kwargs)

        parts, counts = [], {}
        for el in elements:
            cat = el.category
            counts[cat] = counts.get(cat, 0) + 1
            text = (el.text or "").strip()
            if cat == "Table":
                html = getattr(el.metadata, "text_as_html", None)
                parts.append(html or text)
                continue
            if not text:
                continue
            parts.append(_UNS_MARKUP.get(cat, "{}").format(text))

        res.text = "\n\n".join(parts)
        res.meta = {"요소 수": len(elements)}
        res.meta.update(
            {k: v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:5]}
        )
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=3)}"
    res.elapsed = time.perf_counter() - t0
    return res


EXTRACTORS = {
    "Docling": {
        "fn": extract_docling,
        "desc": "AI 레이아웃 분석 기반. 읽기 순서·표·수식을 인식해 Markdown으로 구조화.",
        "backends": "PDF/DOCX 모두 `docling`",
        # cost: 실행 순서 결정용 예상 비용. 낮을수록 먼저 실행해 먼저 화면에 그린다.
        "cost": 2,
    },
    "PyMuPDF / python-docx": {
        "fn": extract_native,
        "desc": "포맷 네이티브 파서. 가장 빠르고 원문에 충실하지만 구조 복원은 최소.",
        "backends": "PDF → `pymupdf`, DOCX → `python-docx`",
        "cost": 0,
    },
    "pdfplumber / mammoth": {
        "fn": extract_layout,
        "desc": "좌표 기반 표 검출(pdfplumber) / 스타일 기반 Markdown 변환(mammoth).",
        "backends": "PDF → `pdfplumber`, DOCX → `mammoth`",
        "cost": 1,
    },
    "unstructured": {
        "fn": extract_unstructured,
        "desc": "문서를 Title/NarrativeText/Table 등 의미 단위 요소로 분해. RAG 청킹에 유리.",
        "backends": "PDF/DOCX 모두 `unstructured` (PDF는 strategy=fast)",
        "cost": 1,
    },
}
