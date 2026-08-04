"""전류 벡터 제어 — MTPA / 약계자 / 동작점 판별 (plan.md Phase 2 게이트)."""

from __future__ import annotations

import math

from _helpers import assert_abs, assert_close, standard_motor, standard_spmsm

from compsim.models import ViolationCode
from compsim.motor.control import (
    id_on_constant_torque,
    iq_for_torque_spmsm,
    max_available_torque,
    mtpa_id,
    solve_operating_point,
    torque_on_mtpa,
)
from compsim.motor.limits import inside_current_limit, inside_voltage_limit
from compsim.motor.pmsm import torque
from compsim.units import mech_to_elec_speed, rpm_to_rad_s

T_STD = 5.172  # 표준 R32 사이클의 부하 토크 (참조 백엔드)


def _we(rpm: float, p: int = 3) -> float:
    return mech_to_elec_speed(rpm_to_rad_s(rpm), p)


# ===========================================================================
# MTPA 궤적
# ===========================================================================
def test_mtpa_id_satisfies_derived_relation():
    """delta*u^2 - lambda*u - delta*iq^2 = 0 (research.md §2.6a 유도식)."""
    m = standard_motor()
    d = m.saliency
    for i_q in (1.0, 5.0, 12.0, 20.0):
        u = mtpa_id(m, i_q)
        assert_abs(d * u * u - m.lambda_pm * u - d * i_q * i_q, 0.0, tol=1e-12)
        assert u < 0.0


def test_mtpa_is_zero_at_zero_iq():
    assert_close(mtpa_id(standard_motor(), 0.0), 0.0, rel=1e-14) if False else None
    assert abs(mtpa_id(standard_motor(), 0.0)) < 1e-15


def test_mtpa_spmsm_branch_avoids_singularity():
    m = standard_spmsm()
    for i_q in (0.0, 5.0, 20.0):
        assert mtpa_id(m, i_q) == 0.0


def test_mtpa_minimizes_current_for_given_torque():
    """P2 불변식 — 동일 토크의 임의 해보다 MTPA 의 |i| 가 작거나 같아야 한다."""
    m = standard_motor()
    i_q_mtpa = 12.0
    i_d_mtpa = mtpa_id(m, i_q_mtpa)
    T = torque(m, i_d_mtpa, i_q_mtpa)
    i_mtpa = math.hypot(i_d_mtpa, i_q_mtpa)

    for i_q in [i_q_mtpa * f for f in (0.5, 0.7, 0.9, 1.1, 1.4, 2.0)]:
        i_d = id_on_constant_torque(m, T, i_q)
        assert i_d is not None
        assert_close(torque(m, i_d, i_q), T, rel=1e-9)
        assert math.hypot(i_d, i_q) >= i_mtpa - 1e-9, f"MTPA 가 최소가 아님 (i_q={i_q})"


def test_torque_on_mtpa_is_monotone_increasing():
    m = standard_motor()
    vals = [torque_on_mtpa(m, iq) for iq in (0.0, 2.0, 5.0, 10.0, 15.0, 20.0)]
    assert all(b > a for a, b in zip(vals, vals[1:])), f"{vals}"


def test_id_on_constant_torque_inverts_torque():
    m = standard_motor()
    for i_q in (1.0, 5.0, 12.0):
        i_d = id_on_constant_torque(m, 5.0, i_q)
        assert i_d is not None
        assert_close(torque(m, i_d, i_q), 5.0, rel=1e-12)


def test_id_on_constant_torque_none_at_singular_inputs():
    m = standard_motor()
    assert id_on_constant_torque(m, 5.0, 0.0) is None
    assert id_on_constant_torque(standard_spmsm(), 5.0, 5.0) is None


def test_iq_for_torque_spmsm():
    m = standard_spmsm()
    T = 5.0
    i_q = iq_for_torque_spmsm(m, T)
    assert_close(torque(m, 0.0, i_q), T, rel=1e-12)


# ===========================================================================
# 동작점 탐색
# ===========================================================================
def test_p1_solution_reproduces_requested_torque():
    """P1 불변식 — 토크 등식은 반드시 만족되어야 한다."""
    m = standard_motor()
    for rpm in (900.0, 3600.0, 6000.0, 7200.0):
        s = solve_operating_point(m, T_STD, _we(rpm))
        assert s.ok, f"{rpm}rpm 에서 해가 있어야 한다 ({s.code})"
        assert_close(s.op.torque, T_STD, rel=1e-6, msg=f"@{rpm}rpm")


def test_solution_respects_both_constraints():
    m = standard_motor()
    for rpm in (900.0, 3600.0, 7200.0):
        we = _we(rpm)
        s = solve_operating_point(m, T_STD, we)
        assert s.ok
        assert inside_current_limit(m, s.op.i_d, s.op.i_q, tol=1e-6)
        assert inside_voltage_limit(m, s.op.i_d, s.op.i_q, we, tol=1e-6)


def test_low_speed_uses_mtpa_and_matches_hand_calc():
    """3600rpm 손계산: id≈-4.7, iq≈12.2, |i|≈13.1, |v|≈117 (plan.md §5.2)."""
    m = standard_motor()
    s = solve_operating_point(m, T_STD, _we(3600.0))
    assert s.ok and s.op.mode == "MTPA"
    assert_abs(s.op.i_d, -4.74, tol=0.15)
    assert_abs(s.op.i_q, 12.20, tol=0.15)
    assert_abs(s.op.i_mag, 13.09, tol=0.2)
    assert_abs(s.op.v_mag, 117.2, tol=1.5)
    assert s.op.v_mag < m.v_max


def test_high_speed_switches_to_flux_weakening():
    m = standard_motor()
    s = solve_operating_point(m, T_STD, _we(7200.0))
    assert s.ok and s.op.mode == "FLUX_WEAKENING"
    assert s.op.i_d < -10.0, "약계자는 더 큰 음의 i_d 를 요구한다"
    # 전압 한계에 붙어서 동작한다
    assert_close(s.op.v_mag, m.v_max, rel=1e-4)
    # 같은 토크인데 전류는 MTPA 보다 커야 한다
    s_low = solve_operating_point(m, T_STD, _we(3600.0))
    assert s.op.i_mag > s_low.op.i_mag


def test_mtpa_to_flux_weakening_transition_is_single():
    """속도를 올리며 모드가 MTPA -> FLUX_WEAKENING 으로 한 번만 바뀌어야 한다."""
    m = standard_motor()
    modes = []
    for rpm in range(600, 7800, 200):
        s = solve_operating_point(m, T_STD, _we(float(rpm)))
        if s.ok:
            modes.append(s.op.mode)
    transitions = sum(1 for a, b in zip(modes, modes[1:]) if a != b)
    assert transitions <= 1, f"모드 전이가 {transitions}회: {modes}"


def test_current_limit_triggers_when_i_max_too_small():
    """2-D 분리 검증 — 전류와 전압 제한이 혼동되지 않아야 한다."""
    m = standard_motor(i_max=2.0)
    s = solve_operating_point(m, T_STD, _we(3600.0))
    assert not s.ok
    assert s.code == ViolationCode.CURRENT_LIMIT
    assert s.code != ViolationCode.VOLTAGE_LIMIT


def test_voltage_limit_triggers_at_extreme_speed():
    m = standard_motor()
    s = solve_operating_point(m, T_STD, _we(36000.0))
    assert not s.ok
    assert s.code in (ViolationCode.VOLTAGE_LIMIT, ViolationCode.BOTH_LIMIT)


def test_zero_torque_feasible_at_low_speed_infeasible_at_extreme_speed():
    m = standard_motor()
    s_lo = solve_operating_point(m, 0.0, _we(1800.0))
    assert s_lo.ok
    assert abs(s_lo.op.i_q) < 1e-9, "P8 불변식 — 무부하면 i_q=0"
    assert abs(s_lo.op.i_d) < 1e-6, "저속 무부하는 전류가 필요 없다"

    s_hi = solve_operating_point(m, 0.0, _we(36000.0))
    assert not s_hi.ok, "역기전력이 v_max 를 크게 넘으면 무부하도 불가"


def test_negative_torque_rejected():
    m = standard_motor()
    try:
        solve_operating_point(m, -1.0, _we(3600.0))
    except ValueError:
        pass
    else:
        raise AssertionError("음의 부하 토크는 거부되어야 한다")


def test_spmsm_solver_works():
    m = standard_spmsm()
    s = solve_operating_point(m, 3.0, _we(3600.0))
    assert s.ok
    assert_close(s.op.torque, 3.0, rel=1e-6)
    assert abs(s.op.i_d) < 1e-6, "저속 SPMSM 은 MTPA 가 i_d=0"

    s_fw = solve_operating_point(m, 3.0, _we(9000.0))
    if s_fw.ok:
        assert s_fw.op.i_d < 0.0, "약계자는 i_d<0"


def test_spmsm_current_limit():
    m = standard_spmsm(i_max=2.0)
    s = solve_operating_point(m, 5.0, _we(3600.0))
    assert not s.ok and s.code == ViolationCode.CURRENT_LIMIT


# ===========================================================================
# 최대 가용 토크
# ===========================================================================
def test_p3_max_torque_is_non_increasing_in_speed():
    """P3 불변식 — 전압 타원이 축소되므로 가용 토크는 비증가."""
    m = standard_motor()
    vals = [max_available_torque(m, _we(float(rpm))) for rpm in range(1000, 26000, 2500)]
    for a, b in zip(vals, vals[1:]):
        assert b <= a + 1e-6, f"가용 토크가 증가함: {vals}"


def test_max_torque_is_zero_when_regions_disjoint():
    m = standard_motor()
    assert_abs(max_available_torque(m, _we(36000.0)), 0.0, tol=1e-9)


def test_max_torque_consistent_with_solver():
    """T_max_avail 은 판정기의 경계와 정의상 일치해야 한다."""
    m = standard_motor()
    for rpm in (3600.0, 9000.0, 15000.0):
        we = _we(rpm)
        tmax = max_available_torque(m, we)
        if tmax <= 0.0:
            continue
        assert solve_operating_point(m, tmax * 0.98, we).ok, f"@{rpm}rpm T<Tmax 는 가능해야"
        assert not solve_operating_point(m, tmax * 1.05, we).ok, f"@{rpm}rpm T>Tmax 는 불가해야"


def test_p6_higher_vdc_expands_feasible_torque():
    """P6 불변식 — 직류단 전압을 올리면 가용 토크가 늘어난다."""
    we = _we(12000.0)
    t_low = max_available_torque(standard_motor(V_dc=310.0), we)
    t_high = max_available_torque(standard_motor(V_dc=450.0), we)
    assert t_high > t_low, f"{t_low} -> {t_high}"


def test_p7_higher_imax_expands_feasible_torque():
    """P7 불변식 — 전류 한계를 올리면 가용 토크가 늘어난다."""
    we = _we(3600.0)
    t_low = max_available_torque(standard_motor(i_max=10.0), we)
    t_high = max_available_torque(standard_motor(i_max=25.0), we)
    assert t_high > t_low, f"{t_low} -> {t_high}"


def test_max_torque_at_low_speed_equals_mtpa_current_limit():
    """전압이 구속하지 않는 저속에서는 전류 한계가 최대 토크를 결정한다."""
    m = standard_motor()
    t_900 = max_available_torque(m, _we(900.0))
    t_1800 = max_available_torque(m, _we(1800.0))
    assert_close(t_900, t_1800, rel=1e-6)
    assert t_900 > T_STD, "표준 부하보다는 여유가 있어야 한다"
