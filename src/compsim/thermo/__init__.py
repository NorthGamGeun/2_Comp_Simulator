"""열역학 도메인 — 냉매 물성, 압축기 효율, 사이클 및 부하 토크 산출."""

from __future__ import annotations

from .cycle import compute_load_point, solve_flow_speed
from .efficiency import EfficiencyResult, evaluate_efficiency
from .refrigerant import (
    BackendUnavailableError,
    CoolPropBackend,
    ReferenceGasBackend,
    RefrigerantBackend,
    coolprop_available,
    get_backend,
)

__all__ = [
    "BackendUnavailableError",
    "CoolPropBackend",
    "EfficiencyResult",
    "ReferenceGasBackend",
    "RefrigerantBackend",
    "compute_load_point",
    "coolprop_available",
    "evaluate_efficiency",
    "get_backend",
    "solve_flow_speed",
]
