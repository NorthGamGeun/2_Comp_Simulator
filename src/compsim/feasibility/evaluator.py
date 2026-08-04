"""판정 오케스트레이션 — Step 0 ~ 4 (plan.md §3, §4 Phase 3).

이 모듈이 전체 파이프라인의 단일 진입점이다. UI 는 여기까지만 알면 되고,
CLI 만으로도 시뮬레이터가 완전히 동작한다 (UI 무의존 코어 원칙).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..models import (
    CompressorSpec,
    CycleSpec,
    LoadPoint,
    MotorSpec,
    ThermalLimits,
    Verdict,
    Violation,
    ViolationCode,
)
from ..motor.control import max_available_torque, solve_operating_point
from ..motor.pmsm import back_emf
from ..thermo.cycle import CycleError, compute_load_point
from ..thermo.refrigerant import RefrigerantBackend, get_backend
from ..units import TWO_PI, rev_s_to_rpm
from .thermal_gate import check_thermal_mechanical


@dataclass(frozen=True)
class EvaluationRequest:
    cycle: CycleSpec
    compressor: CompressorSpec
    motor: MotorSpec
    limits: ThermalLimits = field(default_factory=ThermalLimits)
    prefer_backend: str = "auto"
    n_scan: int = 400
    compute_margin: bool = True


# ===========================================================================
# Step 0 — 사전 위생 검사 (저비용, 물성 조회 전에 즉시 판별)
# ===========================================================================
def _pre_checks(cycle: CycleSpec, backend: RefrigerantBackend) -> list[Violation]:
    out: list[Violation] = []

    if cycle.P_discharge <= cycle.P_suction:
        out.append(
            Violation(
                ViolationCode.PRESSURE_INVERSION,
                "FAIL",
                f"토출 압력 {cycle.P_discharge/1e3:.1f} kPa 가 흡입 압력 "
                f"{cycle.P_suction/1e3:.1f} kPa 이하 — 물리적으로 불성립",
                actual=cycle.P_discharge,
                limit=cycle.P_suction,
            )
        )

    p_crit = backend.p_crit()
    if cycle.P_cond >= p_crit:
        out.append(
            Violation(
                ViolationCode.SUPERCRITICAL,
                "FAIL",
                f"응축 압력 {cycle.P_cond/1e3:.1f} kPa 가 임계압력 {p_crit/1e3:.1f} kPa 이상 "
                f"— 응축이 성립하지 않음 (본 모델 범위 밖)",
                actual=cycle.P_cond,
                limit=p_crit,
            )
        )
    return out


def _back_emf_check(motor: MotorSpec, load: LoadPoint) -> list[Violation]:
    """E0 > v_max 는 그 자체로 치명적이지 않다 (약계자로 극복 가능).

    다만 원인 규명에 중요한 정보이므로 WARN 으로 남긴다. 실제 불가 판정은
    VOLTAGE_LIMIT 이 담당한다.
    """
    e0 = back_emf(motor, load.omega_e)
    if e0 > motor.v_max:
        return [
            Violation(
                ViolationCode.BACK_EMF_EXCEEDS_VMAX,
                "WARN",
                f"무부하 역기전력 {e0:.1f} V 가 전압 한계 {motor.v_max:.1f} V 초과 "
                f"— 약계자 제어(i_d<0) 없이는 구동 불가",
                actual=e0,
                limit=motor.v_max,
            )
        ]
    return []


def _electromagnetic_message(code: ViolationCode, motor: MotorSpec, load: LoadPoint) -> str:
    rpm = rev_s_to_rpm(load.N)
    if code == ViolationCode.CURRENT_LIMIT:
        return (
            f"요구 부하 토크 {load.T_load:.2f} N·m 를 전류 한계 {motor.i_max:.1f} A 내에서 "
            f"낼 수 없습니다 (@{rpm:.0f} rpm)"
        )
    if code == ViolationCode.VOLTAGE_LIMIT:
        return (
            f"요구 토크 {load.T_load:.2f} N·m 의 상수 토크 곡선이 전압 한계 "
            f"{motor.v_max:.1f} V 타원 밖에 있습니다 (@{rpm:.0f} rpm)"
        )
    if code == ViolationCode.BOTH_LIMIT:
        return (
            f"전류 한계와 전압 한계를 동시에 만족하는 동작점이 없습니다 "
            f"(@{rpm:.0f} rpm, T={load.T_load:.2f} N·m)"
        )
    return f"전류 벡터 해를 구하지 못했습니다 (@{rpm:.0f} rpm)"


# ===========================================================================
# 메인 진입점
# ===========================================================================
def evaluate(req: EvaluationRequest) -> Verdict:
    """Step 0 → 1 → 2/3 → 4 를 순차 실행하고 Verdict 를 조립한다."""
    backend = get_backend(req.cycle.refrigerant, prefer=req.prefer_backend)

    # --- Step 0 -----------------------------------------------------------
    pre = _pre_checks(req.cycle, backend)
    if any(v.severity == "FAIL" for v in pre):
        return Verdict.assemble(pre)

    # --- Step 1: 열역학 ----------------------------------------------------
    try:
        load = compute_load_point(req.cycle, req.compressor, req.motor.p, backend=backend)
    except CycleError as e:
        return Verdict.assemble(
            [*pre, Violation(ViolationCode.PRESSURE_INVERSION, "FAIL", f"사이클 계산 실패: {e}")]
        )

    violations: list[Violation] = list(pre)
    violations += _back_emf_check(req.motor, load)

    # --- Step 2/3: 전자기 + 구속 조건 --------------------------------------
    sol = solve_operating_point(req.motor, load.T_load, load.omega_e, n_scan=req.n_scan)
    if sol.code is not None:
        violations.append(
            Violation(
                sol.code,
                "FAIL",
                _electromagnetic_message(sol.code, req.motor, load),
                actual=load.T_load,
                limit=req.motor.i_max
                if sol.code == ViolationCode.CURRENT_LIMIT
                else req.motor.v_max,
            )
        )

    t_max = (
        max_available_torque(req.motor, load.omega_e, n_scan=max(req.n_scan // 2, 100))
        if req.compute_margin
        else None
    )

    # --- Step 4: 열적 / 기계적 게이트 (전자기 결과와 독립) ------------------
    violations += check_thermal_mechanical(req.cycle, load, req.limits)

    return Verdict.assemble(violations, load=load, op=sol.op, T_max_avail=t_max)


# ===========================================================================
# 속도 스윕 (UI §6.3 및 검증 조건 2-C)
# ===========================================================================
@dataclass(frozen=True)
class SweepPoint:
    rpm: float
    T_load: float
    T_max_avail: float
    feasible: bool
    status: str
    codes: frozenset[ViolationCode]


def sweep_speed(
    req: EvaluationRequest,
    rpm_values: list[float],
    *,
    n_scan: int = 200,
) -> list[SweepPoint]:
    """회전수를 스윕하며 부하 토크와 가용 토크를 비교한다.

    SPEED_DRIVEN 모드에서만 의미가 있다. 설계자에게 가장 실용적인 화면인
    '어디까지 돌릴 수 있는가' 를 제공한다.
    """
    if req.compressor.drive_mode != "SPEED_DRIVEN":
        raise ValueError("속도 스윕은 SPEED_DRIVEN 모드에서만 가능합니다.")

    out: list[SweepPoint] = []
    for rpm in rpm_values:
        comp = replace(req.compressor, N=rpm / 60.0)
        v = evaluate(replace(req, compressor=comp, n_scan=n_scan))
        out.append(
            SweepPoint(
                rpm=rpm,
                T_load=v.load.T_load if v.load else float("nan"),
                T_max_avail=v.T_max_avail if v.T_max_avail is not None else float("nan"),
                feasible=v.is_feasible,
                status=v.status,
                codes=frozenset(v.violation_codes),
            )
        )
    return out


def max_feasible_speed(
    req: EvaluationRequest,
    *,
    rpm_lo: float = 300.0,
    rpm_hi: float = 30000.0,
    tol_rpm: float = 5.0,
) -> float:
    """가동 가능한 최대 회전수를 이분 탐색한다. 전 구간 불가면 0 반환."""

    def ok(rpm: float) -> bool:
        comp = replace(req.compressor, N=rpm / 60.0)
        return evaluate(replace(req, compressor=comp, compute_margin=False)).is_feasible

    if not ok(rpm_lo):
        return 0.0
    if ok(rpm_hi):
        return rpm_hi
    lo, hi = rpm_lo, rpm_hi
    while hi - lo > tol_rpm:
        mid = 0.5 * (lo + hi)
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


__all__ = [
    "EvaluationRequest",
    "SweepPoint",
    "evaluate",
    "max_feasible_speed",
    "sweep_speed",
    "TWO_PI",
]
