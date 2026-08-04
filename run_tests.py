#!/usr/bin/env python3
"""pytest 미설치 환경용 최소 테스트 러너.

정상 환경에서는 `pytest` 를 쓰십시오. 이 러너는 개발 샌드박스처럼
PyPI 접근이 불가한 환경에서도 검증 루프(plan.md §5)를 돌리기 위한 폴백입니다.

테스트는 fixture 없이 평범한 `test_*` 함수로 작성하므로 두 러너 모두 호환됩니다.
스킵은 `unittest.SkipTest` 를 던집니다 (pytest 도 동일하게 인식).
"""

from __future__ import annotations

import importlib.util
import sys
import time
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(TESTS))

    pattern = argv[1] if len(argv) > 1 else ""
    files = sorted(TESTS.glob("test_*.py"))

    passed = failed = skipped = 0
    failures: list[tuple[str, str]] = []
    skips: list[tuple[str, str]] = []
    t0 = time.time()

    for f in files:
        try:
            mod = _load_module(f)
        except Exception:
            failed += 1
            failures.append((f"{f.name}::<import>", traceback.format_exc()))
            print("E", end="", flush=True)
            continue

        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            full = f"{f.stem}::{name}"
            if pattern and pattern not in full:
                continue
            try:
                fn()
                passed += 1
                print(".", end="", flush=True)
            except unittest.SkipTest as e:
                skipped += 1
                skips.append((full, str(e)))
                print("s", end="", flush=True)
            except Exception:
                failed += 1
                failures.append((full, traceback.format_exc()))
                print("F", end="", flush=True)

    dt = time.time() - t0
    print("\n")
    for name, tb in failures:
        print("=" * 78)
        print(f"FAIL  {name}")
        print("-" * 78)
        print(tb)
    if skips:
        print("-" * 78)
        for name, why in skips:
            print(f"SKIP  {name}  ({why})")
    print("=" * 78)
    print(f"passed={passed}  failed={failed}  skipped={skipped}   ({dt:.2f}s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
