"""구속 조건 기하 (research.md §2.3~2.4, plan.md §6.1).

전류 한계 : 원점 중심, 반경 i_max 의 **원**
전압 한계 : 중심 (-lambda_pm/Ld, 0), 반축 v_max/(w_e*Ld), v_max/(w_e*Lq) 의 **타원**
            (Rs 무시 근사 — 시각화 전용)

판정용 `inside_voltage_limit` 은 Rs 를 포함한 정확식을 쓴다 (plan.md D6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import MotorSpec
from ..units import TORQUE_DQ_COEFF
from .pmsm import voltage_magnitude


# ===========================================================================
# 포함 판정
# ===========================================================================
def inside_current_limit(motor: MotorSpec, i_d: float, i_q: float, *, tol: float = 1e-9) -> bool:
    return i_d * i_d + i_q * i_q <= motor.i_max * motor.i_max * (1.0 + tol)


def inside_voltage_limit(
    motor: MotorSpec, i_d: float, i_q: float, omega_e: float, *, tol: float = 1e-9
) -> bool:
    """Rs 포함 정확식으로 판정한다."""
    return voltage_magnitude(motor, i_d, i_q, omega_e) <= motor.v_max * (1.0 + tol)


# ===========================================================================
# 플롯용 좌표 생성
# ===========================================================================
def current_circle(i_max: float, n: int = 361) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for k in range(n):
        th = 2.0 * math.pi * k / (n - 1)
        xs.append(i_max * math.cos(th))
        ys.append(i_max * math.sin(th))
    return xs, ys


@dataclass(frozen=True)
class EllipseGeometry:
    center_id: float
    center_iq: float
    semi_d: float  # i_d 방향 반축
    semi_q: float  # i_q 방향 반축
    degenerate: bool  # omega_e ~ 0 이면 타원이 무한대로 발산


def voltage_ellipse_geometry(motor: MotorSpec, omega_e: float) -> EllipseGeometry:
    """Rs 무시 근사 타원의 기하 파라미터.

    omega_e 가 커지면 반축이 1/omega_e 로 축소되어 중심으로 수축한다.
    이것이 '동작 불가(Voltage Limit)' 의 물리적 실체다 (research.md §2.4).
    """
    center = -motor.lambda_pm / motor.Ld if motor.Ld > 0.0 else 0.0
    if omega_e <= 0.0 or motor.Ld <= 0.0 or motor.Lq <= 0.0:
        return EllipseGeometry(center, 0.0, math.inf, math.inf, True)
    return EllipseGeometry(
        center_id=center,
        center_iq=0.0,
        semi_d=motor.v_max / (omega_e * motor.Ld),
        semi_q=motor.v_max / (omega_e * motor.Lq),
        degenerate=False,
    )


def voltage_ellipse(
    motor: MotorSpec, omega_e: float, n: int = 361
) -> tuple[list[float], list[float]]:
    """플롯용 전압 한계 타원 좌표 (Rs 무시 근사)."""
    g = voltage_ellipse_geometry(motor, omega_e)
    if g.degenerate:
        return [], []
    xs, ys = [], []
    for k in range(n):
        th = 2.0 * math.pi * k / (n - 1)
        xs.append(g.center_id + g.semi_d * math.cos(th))
        ys.append(g.center_iq + g.semi_q * math.sin(th))
    return xs, ys


def voltage_boundary_exact(
    motor: MotorSpec, omega_e: float, n: int = 361
) -> tuple[list[float], list[float]]:
    """Rs 포함 정확식의 전압 한계 경계 |v|=v_max.

    v = M*i + e 형태의 아핀 사상이므로 경계는 여전히 타원이지만 회전한다.
        [v_d]   [ Rs      -w_e*Lq ] [i_d]   [ 0             ]
        [v_q] = [ w_e*Ld   Rs     ] [i_q] + [ w_e*lambda_pm ]
    원 |v|=v_max 를 역사상하여 얻는다.
    """
    a, b = motor.Rs, -omega_e * motor.Lq
    c, d = omega_e * motor.Ld, motor.Rs
    e_q = omega_e * motor.lambda_pm
    det = a * d - b * c
    if abs(det) < 1e-15:
        return [], []
    # 역행렬
    ia, ib = d / det, -b / det
    ic, id_ = -c / det, a / det
    xs, ys = [], []
    for k in range(n):
        th = 2.0 * math.pi * k / (n - 1)
        vd = motor.v_max * math.cos(th)
        vq = motor.v_max * math.sin(th) - e_q
        xs.append(ia * vd + ib * vq)
        ys.append(ic * vd + id_ * vq)
    return xs, ys


def constant_torque_curve(
    motor: MotorSpec, T: float, iq_lo: float, iq_hi: float, n: int = 200
) -> tuple[list[float], list[float]]:
    """T_em = T 를 만족하는 (i_d, i_q) 궤적. i_q 로 파라미터화 (plan.md §3.2)."""
    from .control import id_on_constant_torque

    xs, ys = [], []
    if iq_hi <= iq_lo or n < 2:
        return xs, ys
    for k in range(n):
        iq = iq_lo + (iq_hi - iq_lo) * k / (n - 1)
        i_d = id_on_constant_torque(motor, T, iq)
        if i_d is None or not math.isfinite(i_d):
            continue
        xs.append(i_d)
        ys.append(iq)
    return xs, ys


def mtpa_curve(motor: MotorSpec, iq_max: float, n: int = 200) -> tuple[list[float], list[float]]:
    """MTPA 궤적 (참조선)."""
    from .control import mtpa_id

    xs, ys = [], []
    for k in range(n):
        iq = iq_max * k / (n - 1)
        xs.append(mtpa_id(motor, iq))
        ys.append(iq)
    return xs, ys


def iq_for_torque_at_id(motor: MotorSpec, T: float, i_d: float) -> float | None:
    """주어진 i_d 에서 토크 T 를 내는 i_q. 분모가 0 이면 None."""
    denom = TORQUE_DQ_COEFF * motor.p * (motor.lambda_pm + (motor.Ld - motor.Lq) * i_d)
    if abs(denom) < 1e-15:
        return None
    return T / denom


__all__ = [
    "EllipseGeometry",
    "constant_torque_curve",
    "current_circle",
    "inside_current_limit",
    "inside_voltage_limit",
    "iq_for_torque_at_id",
    "mtpa_curve",
    "voltage_boundary_exact",
    "voltage_ellipse",
    "voltage_ellipse_geometry",
]
