"""PMSM 정상상태 dq 모델 (research.md §2.1~2.2).

    v_d = Rs*i_d - w_e*Lq*i_q
    v_q = Rs*i_q + w_e*(Ld*i_d + lambda_pm)
    T   = (3/2)*p*[lambda_pm*i_q + (Ld - Lq)*i_d*i_q]

정상상태이므로 L*di/dt 항은 0. 전류/전압은 **peak** 기준 (units.DQ_CONVENTION).
"""

from __future__ import annotations

import math

from ..models import MotorSpec
from ..units import TORQUE_DQ_COEFF


def torque(motor: MotorSpec, i_d: float, i_q: float) -> float:
    """전자기 토크 [N*m].

    제2항 (Ld-Lq)*i_d*i_q 이 릴럭턴스 토크. IPMSM 은 Ld<Lq 이므로
    i_d<0 일 때 양(+)의 기여를 한다 — MTPA 가 i_d<0 을 택하는 이유.
    """
    return TORQUE_DQ_COEFF * motor.p * (
        motor.lambda_pm * i_q + (motor.Ld - motor.Lq) * i_d * i_q
    )


def voltage(motor: MotorSpec, i_d: float, i_q: float, omega_e: float) -> tuple[float, float]:
    """정상상태 dq 전압 [V, peak]. 저항 강하를 포함한 **정확식**."""
    v_d = motor.Rs * i_d - omega_e * motor.Lq * i_q
    v_q = motor.Rs * i_q + omega_e * (motor.Ld * i_d + motor.lambda_pm)
    return v_d, v_q


def voltage_magnitude(motor: MotorSpec, i_d: float, i_q: float, omega_e: float) -> float:
    v_d, v_q = voltage(motor, i_d, i_q, omega_e)
    return math.hypot(v_d, v_q)


def voltage_magnitude_no_rs(
    motor: MotorSpec, i_d: float, i_q: float, omega_e: float
) -> float:
    """Rs 를 무시한 근사 (시각화용 타원과 동일한 기준).

    판정에는 쓰지 않는다 (plan.md D6). 저속·고토크에서 Rs*i 강하가 무시 못 할
    수준이므로, 이 값과 정확식의 차이를 UI 가 드러낼 수 있게 별도 제공한다.
    """
    return omega_e * math.hypot(motor.Ld * i_d + motor.lambda_pm, motor.Lq * i_q)


def current_magnitude(i_d: float, i_q: float) -> float:
    return math.hypot(i_d, i_q)


def back_emf(motor: MotorSpec, omega_e: float) -> float:
    """무부하 역기전력 E0 = w_e * lambda_pm [V, peak].

    E0 > v_max 이면 i_d<0 (약계자) 없이는 원천적으로 구동 불가.
    연산 전 즉시 판별 가능한 저비용 사전 검사다 (research.md §2.7).
    """
    return omega_e * motor.lambda_pm


def base_speed_estimate(motor: MotorSpec, i_d: float, i_q: float) -> float:
    """주어진 전류점에서 전압 한계에 도달하는 전기 각속도 (Rs 무시 근사).

    이 속도 이상에서 약계자 영역으로 진입한다.
    """
    denom = math.hypot(motor.Ld * i_d + motor.lambda_pm, motor.Lq * i_q)
    if denom <= 0.0:
        return math.inf
    return motor.v_max / denom


__all__ = [
    "back_emf",
    "base_speed_estimate",
    "current_magnitude",
    "torque",
    "voltage",
    "voltage_magnitude",
    "voltage_magnitude_no_rs",
]
