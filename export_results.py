"""추출 결과를 results.json 으로 굳힌다 (로컬 전용).

배포 환경(Streamlit Community Cloud 등)은 Docling·unstructured 의 레이아웃 모델을
돌릴 CPU/메모리가 없다. 그래서 무거운 계산은 여기 로컬에서 한 번만 하고, 그 결과를
JSON 으로 떨어뜨린 뒤 `app.py` 는 그 값을 읽어 그리기만 한다.

    python export_results.py                # 폴더의 PDF/DOCX 전부
    python export_results.py 고소장_예시_사기.docx   # 지정한 파일만

`app-local.py` 사이드바의 "결과 JSON 내보내기" 버튼도 여기 함수를 그대로 쓴다.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from extractors import EXTRACTORS, MAX_TABLES, image_support, method_label

BASE_DIR = Path(__file__).parent
OUT_PATH = BASE_DIR / "results.json"
SUPPORTED_EXTS = (".pdf", ".docx")
BASELINE_METHOD = "PyMuPDF / python-docx"
# app.py 가 읽는 JSON 구조 버전. 필드가 바뀌면 올린다.
PAYLOAD_SCHEMA = 1


def _jsonable(v):
    """meta 값은 방식마다 타입이 제각각이라 JSON 이 삼킬 수 있는 형태로 낮춘다."""
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)


def result_payload(res, method: str, path: Path) -> dict:
    """ExtractResult 를 화면에 필요한 값만 남긴 dict 로 평탄화한다.

    n_chars 같은 파생 지표는 property 라 JSON 에 안 실린다. 뷰어에서 다시 계산하려면
    extractors 를 임포트해야 하므로(= 배포에 무거운 의존성이 따라온다) 여기서 미리
    값으로 굳혀 넣는다. image_mark/note 도 같은 이유로 여기서 확정한다.
    """
    label = method_label(method, path)
    if not res.ok:
        return {"label": label, "ok": False, "backend": res.backend, "error": res.error}

    mark, note = image_support(method, res)
    return {
        "label": label,
        "ok": True,
        "backend": res.backend,
        "elapsed": round(res.elapsed, 3),
        "n_chars": res.n_chars,
        "n_raw_chars": res.n_raw_chars,
        "n_words": res.n_words,
        "n_lines": res.n_lines,
        "meta": {str(k): _jsonable(v) for k, v in res.meta.items()},
        "text": res.text,
        "is_markdown": res.is_markdown,
        "tables": res.tables,
        "tables_note": res.tables_note,
        "image_mark": mark,
        "image_note": note,
    }


def build_payload(
    docs: dict[str, Path],
    methods: list[str],
    results: dict[tuple[str, str], object],
    generated_at: str | None = None,
) -> dict:
    """문서 × 방식 결과를 app.py 가 그대로 그릴 수 있는 한 덩어리로 묶는다."""
    return {
        "schema": PAYLOAD_SCHEMA,
        "generated_at": generated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "baseline_method": BASELINE_METHOD,
        "max_tables": MAX_TABLES,
        "methods": {
            m: {
                "desc": EXTRACTORS[m]["desc"],
                "backends": EXTRACTORS[m]["backends"],
                "cells": EXTRACTORS[m]["cells"],
            }
            for m in methods
        },
        "documents": [
            {
                "name": name,
                "ext": path.suffix.lower(),
                "size": path.stat().st_size,
                "results": {
                    m: result_payload(res, m, path)
                    for m in methods
                    if (res := results.get((name, m))) is not None
                },
            }
            for name, path in docs.items()
        ],
    }


def write_payload(payload: dict, out: Path = OUT_PATH) -> Path:
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


# ------------------------------------------------------------------ CLI


def main(argv: list[str]) -> int:
    if argv:
        docs = {}
        for arg in argv:
            p = (BASE_DIR / arg) if not Path(arg).is_absolute() else Path(arg)
            if not p.exists():
                print(f"파일을 찾을 수 없습니다: {p}")
                return 1
            docs[p.name] = p
    else:
        docs = {
            p.name: p
            for p in sorted(BASE_DIR.glob("*"))
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
        }

    if not docs:
        print(f"{BASE_DIR} 안에 PDF/DOCX 가 없습니다.")
        return 1

    methods = list(EXTRACTORS)
    results: dict[tuple[str, str], object] = {}

    # 싼 방식부터 돌린다. 앞에서 죽어도 이미 나온 결과는 로그로 남는다.
    tasks = sorted(
        ((n, m) for n in docs for m in methods),
        key=lambda t: (EXTRACTORS[t[1]]["cost"], list(docs).index(t[0])),
    )
    for i, (name, method) in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {name} · {method} …", flush=True)
        res = EXTRACTORS[method]["fn"](docs[name])
        results[(name, method)] = res
        state = "실패" if not res.ok else f"{res.n_chars:,}자"
        print(f"        {res.elapsed:6.2f}s · {state}", flush=True)

    out = write_payload(build_payload(docs, methods, results))
    print(f"\n{out}  ({out.stat().st_size / 1024:.0f} KB) 저장 완료")
    print("이 파일을 app.py 와 함께 커밋하면 배포 환경에서 그대로 보입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
