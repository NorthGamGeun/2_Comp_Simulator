"""압축기 효율 상관식 (research.md §1.4, plan.md D2).

    eta_isen(PR) = a0 + a1*PR + a2*PR^2
    eta_vol(PR)  = b0 - b1*(PR^(1/kappa) - 1)     # 클리어런스 재팽창 물리에서 유도
    eta_mech(N)  = c0 + c1*N + c2*N^2             # N [rev/s]

가드레일 (research.md §1.4):
  - 모든 효율을 (0, 1] 로 클램프
  - 유효 PR 구간 밖은 외삽 플래그를 세운다 (조용히 틀리는 것보다 시끄럽게 경고)
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import EfficiencyCoeffs
from ..numerics import clamp

#: 효율 하한 — 0 으로 나누기 방지 및 비물리 값 차단
ETA_MIN = 1.0e-3
ETA_MAX = 1.0


@dataclass(frozen=True)
class EfficiencyResult:
    eta_isen: float
    eta_vol: float
    eta_mech: float
    extrapolated: bool
    clamped: bool


def _clamp_eta(x: float) -> tuple[float, bool]:
    y = clamp(x, ETA_MIN, ETA_MAX)
    return y, (y != x)


def eta_isentropic(coeffs: EfficiencyCoeffs, PR: float) -> float:
    a0, a1, a2 = coeffs.isen
    return a0 + a1 * PR + a2 * PR * PR


def eta_volumetric(coeffs: EfficiencyCoeffs, PR: float) -> float:
    """b0 - b1*(PR^(1/kappa) - 1).

    b1 은 클리어런스 체적비 C 에 해당한다 (스크롤 0.01~0.03, 로터리 0.02~0.05).
    """
    b0, b1, kappa = coeffs.vol
    if kappa <= 0.0:
        raise ValueError(f"kappa 는 양수여야 합니다: {kappa}")
    return b0 - b1 * (PR ** (1.0 / kappa) - 1.0)


def eta_mechanical(coeffs: EfficiencyCoeffs, N: float) -> float:
    """N 은 [rev/s]."""
    c0, c1, c2 = coeffs.mech
    return c0 + c1 * N + c2 * N * N


def evaluate_efficiency(coeffs: EfficiencyCoeffs, PR: float, N: float) -> EfficiencyResult:
    """세 효율을 한 번에 평가하고 클램프/외삽 상태를 함께 보고한다."""
    if PR <= 0.0:
        raise ValueError(f"PR 은 양수여야 합니다: {PR}")

    pr_lo, pr_hi = coeffs.pr_valid
    extrapolated = not (pr_lo <= PR <= pr_hi)

    ei, c1_ = _clamp_eta(eta_isentropic(coeffs, PR))
    ev, c2_ = _clamp_eta(eta_volumetric(coeffs, PR))
    em, c3_ = _clamp_eta(eta_mechanical(coeffs, N))

    return EfficiencyResult(
        eta_isen=ei,
        eta_vol=ev,
        eta_mech=em,
        extrapolated=extrapolated,
        clamped=c1_ or c2_ or c3_,
    )


__all__ = [
    "ETA_MAX",
    "ETA_MIN",
    "EfficiencyResult",
    "eta_isentropic",
    "eta_mechanical",
    "eta_volumetric",
    "evaluate_efficiency",
]
