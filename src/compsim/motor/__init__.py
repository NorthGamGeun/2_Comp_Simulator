"""전자기 도메인 — PMSM dq 모델, 구속 조건 기하, 전류 벡터 제어."""

from __future__ import annotations

from .control import (
    OperatingSolution,
    id_on_constant_torque,
    max_available_torque,
    mtpa_id,
    solve_operating_point,
    torque_on_mtpa,
)
from .limits import (
    constant_torque_curve,
    current_circle,
    inside_current_limit,
    inside_voltage_limit,
    voltage_ellipse,
    voltage_ellipse_geometry,
)
from .pmsm import back_emf, torque, voltage, voltage_magnitude

__all__ = [
    "OperatingSolution",
    "back_emf",
    "constant_torque_curve",
    "current_circle",
    "id_on_constant_torque",
    "inside_current_limit",
    "inside_voltage_limit",
    "max_available_torque",
    "mtpa_id",
    "solve_operating_point",
    "torque",
    "torque_on_mtpa",
    "voltage",
    "voltage_ellipse",
    "voltage_ellipse_geometry",
    "voltage_magnitude",
]
