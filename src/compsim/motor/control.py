"""전류 벡터 제어 — MTPA / 약계자 / 동작점 판별 (research.md §2.6, plan.md §3.3).

핵심 아이디어 (plan.md §3.2)
---------------------------
토크 등식이 미지수 2개에 등식 1개를 주므로 해집합은 **1-자유도 곡선**이다.
이를 i_q 로 파라미터화하면 2차원 탐색이 1차원 문제로 축소되어
수치 안정성과 속도를 동시에 얻는다.

    i_d(i_q) = ( T/((3/2)*p*i_q) - lambda_pm ) / (Ld - Lq)

판정은 research.md §2.6(c) 의 집합론적 정의를 그대로 구현한다.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from ..models import MotorSpec, OperatingPoint, ViolationCode
from ..numerics import brentq, golden_min, solve_scalar
from ..units import TORQUE_DQ_COEFF
from .limits import inside_current_limit, inside_voltage_limit
from .pmsm import torque, voltage, voltage_magnitude

#: i_q -> 0 근방 0-나눗셈 방지
_IQ_EPS = 1.0e-12
#: 토크를 0 으로 간주하는 임계
_T_EPS = 1.0e-9


@dataclass(frozen=True)
class OperatingSolution:
    """동작점 탐색 결과. 성공이면 op != None 이고 code is None."""

    op: OperatingPoint | None
    code: ViolationCode | None
    T_requested: float
    omega_e: float

    @property
    def ok(self) -> bool:
        return self.op is not None


# ===========================================================================
# 궤적 정의
# ===========================================================================
def mtpa_id(motor: MotorSpec, i_q: float) -> float:
    """MTPA 궤적의 i_d (research.md §2.6a).

        i_d = A - sqrt(A^2 + i_q^2),   A = lambda_pm / (2*(Lq - Ld))

    SPMSM (Lq == Ld) 은 특이점이므로 i_d = 0 으로 분기한다.
    """
    if motor.is_spmsm:
        return 0.0
    A = motor.lambda_pm / (2.0 * motor.saliency)
    return A - math.hypot(A, i_q)


def torque_on_mtpa(motor: MotorSpec, i_q: float) -> float:
    return torque(motor, mtpa_id(motor, i_q), i_q)


def id_on_constant_torque(motor: MotorSpec, T: float, i_q: float) -> float | None:
    """상수 토크 곡선의 i_d. SPMSM 이거나 i_q~0 이면 None (i_d 가 자유/발산)."""
    if motor.is_spmsm or abs(i_q) < _IQ_EPS:
        return None
    return (T / (TORQUE_DQ_COEFF * motor.p * i_q) - motor.lambda_pm) / (motor.Ld - motor.Lq)


def iq_for_torque_spmsm(motor: MotorSpec, T: float) -> float:
    """SPMSM 은 릴럭턴스 항이 없어 i_q 가 토크로 완전히 결정된다."""
    return T / (TORQUE_DQ_COEFF * motor.p * motor.lambda_pm)


# ===========================================================================
# 공용 탐색 유틸
# ===========================================================================
def _make_op(motor: MotorSpec, i_d: float, i_q: float, omega_e: float, mode: str) -> OperatingPoint:
    v_d, v_q = voltage(motor, i_d, i_q, omega_e)
    return OperatingPoint(
        i_d=i_d,
        i_q=i_q,
        v_d=v_d,
        v_q=v_q,
        i_mag=math.hypot(i_d, i_q),
        v_mag=math.hypot(v_d, v_q),
        mode=mode,  # type: ignore[arg-type]
        torque=torque(motor, i_d, i_q),
    )


def _search_min_current(
    motor: MotorSpec,
    omega_e: float,
    point_of: Callable[[float], tuple[float, float] | None],
    lo: float,
    hi: float,
    n: int,
    t_star: float | None = None,
) -> tuple[float, float, float] | None:
    """[lo, hi] 를 스캔해 두 제약을 모두 만족하는 점 중 |i| 최소를 찾는다.

    반환: (i_mag, i_d, i_q) 또는 None

    |i| 는 곡선 위에서 무제약 최적점(MTPA)을 최소로 하는 단봉 함수이므로,
    제약 하 최적점은 항상 '무제약 최적점' 또는 '전압 경계' 위에 있다.
    이산 격자만으로는 두 지점 모두 정확히 포착하지 못하므로 세 단계를 쓴다.

      (1) t_star — 해석적 무제약 최적점을 먼저 정확히 검사
      (2) 격자 스캔으로 대략의 최적 구간 확보
      (3) 전압 경계(brentq) 또는 내부 최소(황금분할)로 정밀화
    """
    if hi <= lo or n < 3:
        return None

    # (1) 해석적 최적점이 제약을 만족하면 그것이 전역 최소다
    if t_star is not None and lo <= t_star <= hi:
        p = point_of(t_star)
        if (
            p is not None
            and inside_current_limit(motor, p[0], p[1])
            and inside_voltage_limit(motor, p[0], p[1], omega_e)
        ):
            return (math.hypot(p[0], p[1]), p[0], p[1])

    ts = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
    pts: list[tuple[float, float] | None] = [point_of(t) for t in ts]

    def fv(t: float) -> float:
        p = point_of(t)
        if p is None:
            return math.inf
        return voltage_magnitude(motor, p[0], p[1], omega_e) - motor.v_max

    best: tuple[float, float, float] | None = None
    best_k = -1
    for k, p in enumerate(pts):
        if p is None:
            continue
        i_d, i_q = p
        if not inside_current_limit(motor, i_d, i_q):
            continue
        if not inside_voltage_limit(motor, i_d, i_q, omega_e):
            continue
        m = math.hypot(i_d, i_q)
        if best is None or m < best[0]:
            best, best_k = (m, i_d, i_q), k

    if best is None:
        return None

    def _accept(t: float, cur: tuple[float, float, float]) -> tuple[float, float, float]:
        p = point_of(t)
        if p is None:
            return cur
        i_d, i_q = p
        if not inside_current_limit(motor, i_d, i_q, tol=1e-7):
            return cur
        if not inside_voltage_limit(motor, i_d, i_q, omega_e, tol=1e-7):
            return cur
        m = math.hypot(i_d, i_q)
        return (m, i_d, i_q) if m < cur[0] else cur

    # (3a) 전압 경계 정밀화: 인접 표본이 전압 위반이면 그 사이에서 |v|=v_max 를 푼다
    neighbours_feasible = True
    for nb in (best_k - 1, best_k + 1):
        if not (0 <= nb < n):
            continue
        p = pts[nb]
        if p is None or inside_voltage_limit(motor, p[0], p[1], omega_e):
            continue
        neighbours_feasible = False
        a, b = ts[min(best_k, nb)], ts[max(best_k, nb)]
        try:
            best = _accept(brentq(fv, a, b), best)
        except Exception:  # noqa: BLE001 - 경계 정밀화 실패는 치명적이지 않다
            continue

    # (3b) 내부 최소 정밀화: 양 이웃이 모두 가능하면 최적점이 격자 사이에 있다
    if neighbours_feasible:
        a = ts[max(best_k - 1, 0)]
        b = ts[min(best_k + 1, n - 1)]
        if b > a:
            def cost(t: float) -> float:
                p = point_of(t)
                if p is None:
                    return math.inf
                return math.hypot(p[0], p[1])

            t_opt, _ = golden_min(cost, a, b)
            best = _accept(t_opt, best)

    return best


def _voltage_feasible_anywhere(
    motor: MotorSpec,
    omega_e: float,
    point_of: Callable[[float], tuple[float, float] | None],
    lo: float,
    hi: float,
    n: int,
) -> bool:
    """전류 한계를 무시했을 때 전압 제약을 만족하는 점이 곡선 위에 존재하는가."""
    for k in range(n):
        t = lo + (hi - lo) * k / (n - 1)
        p = point_of(t)
        if p is None:
            continue
        if inside_voltage_limit(motor, p[0], p[1], omega_e):
            return True
    return False


def _classify_failure(
    motor: MotorSpec,
    omega_e: float,
    point_of: Callable[[float], tuple[float, float] | None],
    lo_wide: float,
    hi_wide: float,
    n: int,
) -> ViolationCode:
    """research.md §2.6(c) 원인 분해.

    여기 도달했다는 것은 '상수 토크 곡선 ∩ 전류 원' 이 비어있지 않다는 뜻이다.
    따라서 전압만 확인하면 된다.
    """
    if _voltage_feasible_anywhere(motor, omega_e, point_of, lo_wide, hi_wide, n):
        # 전압 만족 구간은 있으나 전류 원과 겹치지 않음
        return ViolationCode.BOTH_LIMIT
    return ViolationCode.VOLTAGE_LIMIT


# ===========================================================================
# 메인 해법
# ===========================================================================
def solve_operating_point(
    motor: MotorSpec,
    T_load: float,
    omega_e: float,
    *,
    n_scan: int = 400,
) -> OperatingSolution:
    """토크 T_load 를 omega_e 에서 낼 수 있는지 판별하고 최소 전류 동작점을 반환."""
    if T_load < -_T_EPS:
        raise ValueError(f"압축기 부하 토크는 음수일 수 없습니다: {T_load}")
    T = max(T_load, 0.0)

    if T <= _T_EPS:
        return _solve_zero_torque(motor, omega_e, n_scan)
    if motor.is_spmsm:
        return _solve_spmsm(motor, T, omega_e, n_scan)
    return _solve_ipmsm(motor, T, omega_e, n_scan)


def _solve_zero_torque(motor: MotorSpec, omega_e: float, n_scan: int) -> OperatingSolution:
    """무부하. i_q=0 이고 i_d 만으로 전압 제약을 만족시켜야 한다."""
    lo, hi = -motor.i_max, motor.i_max

    def point_of(t: float) -> tuple[float, float]:
        return (t, 0.0)

    # 무부하의 무제약 최적점은 원점 (i_d=0)
    best = _search_min_current(motor, omega_e, point_of, lo, hi, n_scan, t_star=0.0)
    if best is not None:
        return OperatingSolution(
            _make_op(motor, best[1], best[2], omega_e, "MTPA" if abs(best[1]) < 1e-9
                     else "FLUX_WEAKENING"),
            None, 0.0, omega_e,
        )
    code = _classify_failure(motor, omega_e, point_of, -50.0 * motor.i_max,
                             50.0 * motor.i_max, n_scan)
    return OperatingSolution(None, code, 0.0, omega_e)


def _solve_spmsm(motor: MotorSpec, T: float, omega_e: float, n_scan: int) -> OperatingSolution:
    """SPMSM: i_q 가 토크로 고정되고 i_d 는 약계자 자유도."""
    i_q = iq_for_torque_spmsm(motor, T)
    if abs(i_q) > motor.i_max:
        return OperatingSolution(None, ViolationCode.CURRENT_LIMIT, T, omega_e)

    id_span = math.sqrt(max(motor.i_max**2 - i_q**2, 0.0))

    def point_of(t: float) -> tuple[float, float]:
        return (t, i_q)

    # SPMSM 의 MTPA 는 i_d = 0 (릴럭턴스 토크가 없으므로)
    best = _search_min_current(motor, omega_e, point_of, -id_span, id_span, n_scan, t_star=0.0)
    if best is not None:
        mode = "MTPA" if abs(best[1]) < 1e-6 else "FLUX_WEAKENING"
        return OperatingSolution(_make_op(motor, best[1], best[2], omega_e, mode), None, T, omega_e)

    code = _classify_failure(motor, omega_e, point_of, -50.0 * motor.i_max,
                             50.0 * motor.i_max, n_scan)
    return OperatingSolution(None, code, T, omega_e)


def _solve_ipmsm(motor: MotorSpec, T: float, omega_e: float, n_scan: int) -> OperatingSolution:
    imax = motor.i_max

    # --- S1: MTPA 궤적에서 낼 수 있는 최대 토크 (전류 한계까지) --------------
    def f_i_mtpa(iq: float) -> float:
        return mtpa_id(motor, iq) ** 2 + iq * iq - imax * imax

    iq_at_imax = solve_scalar(f_i_mtpa, 0.0, imax, n_scan=200)
    if iq_at_imax is None:  # pragma: no cover - f_i(0)<0<f_i(imax) 이므로 항상 존재
        return OperatingSolution(None, ViolationCode.SOLVER_NOT_CONVERGED, T, omega_e)
    T_mtpa_max = torque_on_mtpa(motor, iq_at_imax)

    # --- S2: 전류 한계 검사 -------------------------------------------------
    if T > T_mtpa_max * (1.0 + 1e-12):
        return OperatingSolution(None, ViolationCode.CURRENT_LIMIT, T, omega_e)

    iq_mtpa = solve_scalar(lambda iq: torque_on_mtpa(motor, iq) - T, 0.0, iq_at_imax, n_scan=200)
    if iq_mtpa is None or iq_mtpa <= 0.0:
        return OperatingSolution(None, ViolationCode.SOLVER_NOT_CONVERGED, T, omega_e)
    id_mtpa_pt = mtpa_id(motor, iq_mtpa)

    # --- S3: MTPA 점이 전압 한계 내부면 종료 --------------------------------
    if inside_voltage_limit(motor, id_mtpa_pt, iq_mtpa, omega_e):
        return OperatingSolution(
            _make_op(motor, id_mtpa_pt, iq_mtpa, omega_e, "MTPA"), None, T, omega_e
        )

    # --- S4: 약계자 — 상수 토크 곡선 위에서 탐색 ----------------------------
    def point_of(iq: float) -> tuple[float, float] | None:
        i_d = id_on_constant_torque(motor, T, iq)
        if i_d is None or not math.isfinite(i_d):
            return None
        return (i_d, iq)

    def f_i_ct(iq: float) -> float:
        p = point_of(iq)
        if p is None:
            return math.inf
        return p[0] ** 2 + p[1] ** 2 - imax * imax

    # |i| 는 iq_mtpa 에서 최소인 단봉 함수 → 전류 가능 구간은 [iq_lo, iq_hi]
    iq_tiny = max(iq_mtpa * 1e-9, _IQ_EPS * 10.0)
    iq_lo = solve_scalar(f_i_ct, iq_tiny, iq_mtpa, n_scan=300)
    iq_hi = solve_scalar(f_i_ct, iq_mtpa, imax, n_scan=300)
    if iq_lo is None:
        iq_lo = iq_tiny
    if iq_hi is None:
        iq_hi = imax

    best = _search_min_current(motor, omega_e, point_of, iq_lo, iq_hi, n_scan, t_star=iq_mtpa)
    if best is not None:
        return OperatingSolution(
            _make_op(motor, best[1], best[2], omega_e, "FLUX_WEAKENING"), None, T, omega_e
        )

    code = _classify_failure(motor, omega_e, point_of, iq_tiny, 20.0 * imax, n_scan * 2)
    return OperatingSolution(None, code, T, omega_e)


# ===========================================================================
# 최대 가용 토크 (여유율 표시용, plan.md §3.3 S5)
# ===========================================================================
def max_available_torque(
    motor: MotorSpec,
    omega_e: float,
    *,
    n_scan: int = 200,
    iters: int = 40,
    rel_tol: float = 1e-6,
) -> float:
    """전류 원 ∩ 전압 타원 영역에서 낼 수 있는 최대 토크.

    `solve_operating_point` 의 가부 판정을 그대로 이분 탐색한다.
    → T_max_avail 이 판정기와 **정의상 일치**하므로 여유율이 모순되지 않는다.
    """
    def feasible(T: float) -> bool:
        return solve_operating_point(motor, T, omega_e, n_scan=n_scan).ok

    # 상한: 전압을 무시했을 때의 MTPA 최대 토크
    if motor.is_spmsm:
        T_hi = torque(motor, 0.0, motor.i_max)
    else:
        f_i = lambda iq: mtpa_id(motor, iq) ** 2 + iq * iq - motor.i_max**2  # noqa: E731
        iq_at_imax = solve_scalar(f_i, 0.0, motor.i_max, n_scan=200)
        if iq_at_imax is None:  # pragma: no cover
            return 0.0
        T_hi = torque_on_mtpa(motor, iq_at_imax)

    if T_hi <= 0.0:
        return 0.0
    if feasible(T_hi):
        return T_hi

    T_lo = T_hi * 1e-9
    if not feasible(T_lo):
        return 0.0

    for _ in range(iters):
        if (T_hi - T_lo) <= rel_tol * max(T_hi, 1e-12):
            break
        mid = 0.5 * (T_lo + T_hi)
        if feasible(mid):
            T_lo = mid
        else:
            T_hi = mid
    return T_lo


__all__ = [
    "OperatingSolution",
    "id_on_constant_torque",
    "iq_for_torque_spmsm",
    "max_available_torque",
    "mtpa_id",
    "solve_operating_point",
    "torque_on_mtpa",
]
