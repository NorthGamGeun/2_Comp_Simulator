"""냉동 사이클 → 부하 토크 (plan.md §3.1).

이 모듈이 열역학 도메인의 최종 출력인 `LoadPoint` 를 생산한다.
전자기 도메인은 여기서 나온 (T_load, omega_e) 만 소비한다 — 그 외 정보는 넘기지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import CompressorSpec, CycleSpec, LoadPoint
from ..units import TWO_PI, mech_to_elec_speed
from .efficiency import EfficiencyResult, evaluate_efficiency
from .refrigerant import RefrigerantBackend, get_backend


class CycleError(ValueError):
    """사이클 조건 자체가 물리적으로 성립하지 않음."""


@dataclass(frozen=True)
class StatePoints:
    """압축기 주변 상태점 (research.md §1.2)."""

    P1: float
    T1: float
    h1: float
    s1: float
    rho1: float
    phase1: str
    P2: float
    h2s: float
    T2s: float
    h2: float
    T2: float
    T3: float


def compute_state_points(
    cycle: CycleSpec, eta_isen: float, backend: RefrigerantBackend
) -> StatePoints:
    """상태점 1 / 2s / 2 / 3 을 계산한다."""
    P1 = cycle.P_suction
    P2 = cycle.P_discharge
    if P1 <= 0.0:
        raise CycleError(f"흡입 압력이 0 이하입니다: {P1} Pa")
    if P2 <= P1:
        raise CycleError(f"압력 역전: P_dis={P2:.4g} <= P_suc={P1:.4g}")

    # 1: 흡입 (포화온도 + 과열도)
    T1 = backend.t_sat(P1) + cycle.dT_superheat
    h1 = backend.h_pt(P1, T1)
    s1 = backend.s_pt(P1, T1)
    rho1 = backend.rho_pt(P1, T1)
    phase1 = backend.phase_pt(P1, T1)

    # 2s: 등엔트로피 토출
    h2s = backend.h_ps(P2, s1)
    T2s = backend.t_ps(P2, s1)

    # 2: 실제 토출
    h2 = h1 + (h2s - h1) / eta_isen
    T2 = backend.t_ph(P2, h2)

    # 3: 응축기 출구 (포화온도 - 과냉도)
    T3 = backend.t_sat(P2) - cycle.dT_subcool

    return StatePoints(
        P1=P1, T1=T1, h1=h1, s1=s1, rho1=rho1, phase1=phase1,
        P2=P2, h2s=h2s, T2s=T2s, h2=h2, T2=T2, T3=T3,
    )


def solve_flow_speed(
    *,
    drive_mode: str,
    rho_suc: float,
    V_disp: float,
    eta_vol: float,
    N: float | None,
    m_dot: float | None,
) -> tuple[float, float]:
    """단일 관계식 m_dot = rho * V_disp * N * eta_vol 의 미지수만 바꿔 푼다.

    반환: (N [rev/s], m_dot [kg/s])

    두 모드가 같은 식을 공유하므로 로직 중복이 없다 (research.md §1.5).
    eta_vol 이 PR 만의 함수라 반복이 불필요하지만, 향후 eta_vol(PR, N) 확장 시
    이 함수 내부만 고정점 반복으로 바꾸면 되도록 인터페이스를 격리해 두었다.
    """
    denom = rho_suc * V_disp * eta_vol
    if denom <= 0.0:
        raise CycleError(f"rho*V_disp*eta_vol 이 0 이하입니다: {denom!r}")

    if drive_mode == "SPEED_DRIVEN":
        if N is None:
            raise CycleError("SPEED_DRIVEN 인데 N 이 없습니다.")
        return N, denom * N
    if drive_mode == "FLOW_DRIVEN":
        if m_dot is None:
            raise CycleError("FLOW_DRIVEN 인데 m_dot 이 없습니다.")
        return m_dot / denom, m_dot
    raise CycleError(f"알 수 없는 drive_mode: {drive_mode!r}")


def _resolve_efficiency(
    comp: CompressorSpec, PR: float, rho_suc: float, V_disp: float
) -> tuple[EfficiencyResult, float, float]:
    """효율 평가 + 모드 해석.

    eta_mech 가 N 의 함수이므로 FLOW_DRIVEN 에서는 N 을 먼저 구한 뒤
    eta_mech 를 재평가해야 한다 (plan.md §3.1 주의사항).
    eta_vol 은 PR 만의 함수라 1회 평가로 확정된다.
    """
    # 1차 평가: eta_vol 확정 (N 무관), eta_mech 는 잠정값
    N_guess = comp.N if comp.N is not None else 60.0
    eff0 = evaluate_efficiency(comp.eff, PR, N_guess)

    N, m_dot = solve_flow_speed(
        drive_mode=comp.drive_mode,
        rho_suc=rho_suc,
        V_disp=V_disp,
        eta_vol=eff0.eta_vol,
        N=comp.N,
        m_dot=comp.m_dot,
    )

    # 2차 평가: 확정된 N 으로 eta_mech 재평가
    eff = evaluate_efficiency(comp.eff, PR, N)
    return eff, N, m_dot


def compute_load_point(
    cycle: CycleSpec,
    comp: CompressorSpec,
    pole_pairs: int,
    *,
    backend: RefrigerantBackend | None = None,
    prefer_backend: str = "auto",
) -> LoadPoint:
    """열역학 파이프라인 전체 실행 → 도메인 경계 DTO 반환.

    T_load = P_shaft / omega_m,  P_shaft = m_dot*w_isen/(eta_isen*eta_mech)
    """
    be = backend if backend is not None else get_backend(cycle.refrigerant, prefer=prefer_backend)

    PR = cycle.pressure_ratio
    if PR <= 0.0:
        raise CycleError(f"압력비가 0 이하입니다: {PR}")

    # eta_vol 을 얻으려면 rho_suc 가 필요하고, rho_suc 는 상태점 1 에서 나온다.
    # 상태점 1 은 eta_isen 과 무관하므로 순환이 없다 — 흡입 상태를 먼저 확정한다.
    P1 = cycle.P_suction
    if P1 <= 0.0:
        raise CycleError(f"흡입 압력이 0 이하입니다: {P1} Pa")
    T1 = be.t_sat(P1) + cycle.dT_superheat
    rho_suc = be.rho_pt(P1, T1)

    eff, N, m_dot = _resolve_efficiency(comp, PR, rho_suc, comp.V_disp)
    sp = compute_state_points(cycle, eff.eta_isen, be)

    w_isen = sp.h2s - sp.h1
    if w_isen <= 0.0:
        raise CycleError(f"등엔트로피 압축 일이 0 이하입니다: {w_isen:.6g} J/kg")

    P_indicated = m_dot * w_isen / eff.eta_isen
    P_shaft = P_indicated / eff.eta_mech

    omega_m = TWO_PI * N
    if omega_m <= 0.0:
        raise CycleError(f"기계 각속도가 0 이하입니다 (N={N} rev/s)")
    T_load = P_shaft / omega_m

    return LoadPoint(
        T_load=T_load,
        omega_m=omega_m,
        omega_e=mech_to_elec_speed(omega_m, pole_pairs),
        N=N,
        m_dot=m_dot,
        T_dis=sp.T2,
        T_suc=sp.T1,
        PR=PR,
        w_isen=w_isen,
        P_shaft=P_shaft,
        eta_isen=eff.eta_isen,
        eta_vol=eff.eta_vol,
        eta_mech=eff.eta_mech,
        rho_suc=sp.rho1,
        h1=sp.h1,
        h2s=sp.h2s,
        h2=sp.h2,
        s1=sp.s1,
        suction_phase=sp.phase1,
        backend_name=be.name,
        extrapolated=eff.extrapolated,
    )


__all__ = [
    "CycleError",
    "StatePoints",
    "compute_load_point",
    "compute_state_points",
    "solve_flow_speed",
]
