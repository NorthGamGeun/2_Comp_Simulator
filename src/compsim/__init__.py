"""에어컨 압축기 가동 타당성 판별 시뮬레이터.

파이프라인 (plan.md §3):
    CycleSpec + CompressorSpec  --[thermo]-->  LoadPoint(T_load, omega_e)
    LoadPoint + MotorSpec       --[motor]-->   OperatingPoint
    전부                        --[feasibility]--> Verdict
"""

from __future__ import annotations

__version__ = "0.1.0"

from .models import (
    CompressorSpec,
    CycleSpec,
    EfficiencyCoeffs,
    LoadPoint,
    MotorSpec,
    OperatingPoint,
    ThermalLimits,
    Verdict,
    Violation,
    ViolationCode,
)

__all__ = [
    "CompressorSpec",
    "CycleSpec",
    "EfficiencyCoeffs",
    "LoadPoint",
    "MotorSpec",
    "OperatingPoint",
    "ThermalLimits",
    "Verdict",
    "Violation",
    "ViolationCode",
    "__version__",
]
