"""UI 계층.

`adapters` 와 `viewmodel` 은 PyQt 무의존 — UI 없이 테스트된다.
`main_window` 만이 PyQt6 / pyqtgraph 를 import 한다.
"""

from __future__ import annotations

from .adapters import InputError, UiInputs
from .viewmodel import build_dq_plot, build_result_panel, build_sweep_plot

__all__ = [
    "InputError",
    "UiInputs",
    "build_dq_plot",
    "build_result_panel",
    "build_sweep_plot",
]
