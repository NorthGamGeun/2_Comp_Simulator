"""★ 검증 조건 2 — 전압 제한 트리거 및 판정 오케스트레이션 (plan.md §5.2).

설계 논리 (research.md §1.7):
  용적형 압축기의 T_load 는 회전수에 거의 무관하지만 전압 타원 반축은 1/omega_e
  로 축소된다. 따라서 속도만 극단적으로 올리면 반드시 VOLTAGE_LIMIT 이 발생한다.
  매직 넘버가 아니라 해석적으로 보장된 결과다.
"""

from __future__ import annotations

from _helpers import (
    assert_abs,
    standard_compressor,
    standard_cycle,
    standard_limits,
    standard_motor,
)

from compsim.feasibility.evaluator import (
    EvaluationRequest,
    evaluate,
    max_feasible_speed,
    sweep_speed,
)
from compsim.models import CycleSpec, ViolationCode
from compsim.motor.pmsm import back_emf


def _req(*, rpm: float = 3600.0, cond_c: float = 45.0, motor=None, **cyc_kw) -> EvaluationRequest:
    return EvaluationRequest(
        cycle=standard_cycle("R32", cond_c=cond_c, **cyc_kw),
        compressor=standard_compressor(rpm=rpm),
        motor=motor if motor is not None else standard_motor(),
        limits=standard_limits(),
        prefer_backend="reference",
    )


# ===========================================================================
# 2-A — 정상 속도 Feasible
# ===========================================================================
def test_2a_feasible_at_normal_speed():
    v = evaluate(_req(rpm=3600.0))
    assert v.status in ("FEASIBLE", "FEASIBLE_WITH_WARNING"), (
        f"{v.status}: {[str(x) for x in v.violations]}"
    )
    assert ViolationCode.VOLTAGE_LIMIT not in v.violation_codes
    assert ViolationCode.CURRENT_LIMIT not in v.violation_codes
    assert v.op is not None
    assert v.op.i_mag < standard_motor().i_max
    assert v.fails == (), "FAIL 이 없어야 한다"


def test_2a_operating_point_reproduces_load_torque():
    v = evaluate(_req(rpm=3600.0))
    assert v.op is not None and v.load is not None
    assert abs(v.op.torque - v.load.T_load) / v.load.T_load < 1e-6


def test_2a_torque_margin_is_positive_and_sensible():
    v = evaluate(_req(rpm=3600.0))
    assert v.T_max_avail is not None and v.T_max_avail > v.load.T_load
    assert v.torque_margin is not None and 0.0 < v.torque_margin < 1.0


# ===========================================================================
# 2-B — 극단 속도 → 전압 제한
# ===========================================================================
def test_2b_voltage_limit_at_extreme_speed():
    m = standard_motor()
    v = evaluate(_req(rpm=36000.0))
    assert v.status == "INFEASIBLE"
    assert v.violation_codes & {ViolationCode.VOLTAGE_LIMIT, ViolationCode.BOTH_LIMIT}, (
        f"전압 관련 위반이 있어야 함: {v.violation_codes}"
    )
    # 사전 검사도 함께 걸려야 한다 (원인 규명 정보)
    assert ViolationCode.BACK_EMF_EXCEEDS_VMAX in v.violation_codes
    assert back_emf(m, v.load.omega_e) > m.v_max * 5.0
    assert_abs(v.T_max_avail, 0.0, tol=1e-9)


def test_2b_current_limit_not_falsely_reported_at_extreme_speed():
    """고속 실패를 전류 제한으로 오분류하면 안 된다."""
    v = evaluate(_req(rpm=36000.0))
    assert ViolationCode.CURRENT_LIMIT not in v.violation_codes


def test_2b_no_operating_point_when_infeasible():
    v = evaluate(_req(rpm=36000.0))
    assert v.op is None


# ===========================================================================
# 2-C — 전이 속도의 단조성 (경계값 하드코딩 없는 물리 일관성 검증)
# ===========================================================================
def test_2c_feasibility_transitions_at_most_once():
    req = _req()
    rpms = [1000.0 + (30000.0 - 1000.0) * i / 39.0 for i in range(40)]
    pts = sweep_speed(req, rpms, n_scan=200)
    flags = [p.feasible for p in pts]
    transitions = sum(1 for a, b in zip(flags, flags[1:]) if a != b)
    assert transitions <= 1, (
        f"가능/불가 전이가 {transitions}회 — 오판정 의심\n"
        + "\n".join(f"  {p.rpm:7.0f} {p.feasible} {sorted(c.value for c in p.codes)}"
                    for p in pts)
    )
    assert flags[0] is True, "최저 속도는 가능해야 한다"
    assert flags[-1] is False, "최고 속도는 불가해야 한다"


def test_2c_max_available_torque_is_non_increasing_over_sweep():
    req = _req()
    rpms = [1000.0 * i for i in range(1, 25)]
    pts = sweep_speed(req, rpms, n_scan=200)
    vals = [p.T_max_avail for p in pts]
    for a, b in zip(vals, vals[1:]):
        assert b <= a + 1e-6, f"가용 토크가 증가함: {vals}"


def test_2c_load_torque_nearly_constant_over_sweep():
    """research.md §1.7 — 용적형 압축기의 부하 토크는 회전수에 거의 무관."""
    req = _req()
    pts = sweep_speed(req, [1200.0, 3600.0, 7200.0, 12000.0], n_scan=100)
    loads = [p.T_load for p in pts]
    assert max(loads) / min(loads) < 1.0 + 1e-9, f"상수 효율에서는 정확히 일정해야: {loads}"


def test_2c_max_feasible_speed_is_a_true_boundary():
    req = _req()
    n_max = max_feasible_speed(req, rpm_lo=600.0, rpm_hi=30000.0, tol_rpm=5.0)
    assert 3600.0 < n_max < 30000.0, f"최대 가동 속도가 비현실적: {n_max}"
    assert evaluate(_req(rpm=n_max * 0.98)).is_feasible
    assert not evaluate(_req(rpm=n_max * 1.05)).is_feasible


# ===========================================================================
# 2-D — 전류 제한 분리 트리거 (원인 분해의 MECE성)
# ===========================================================================
def test_2d_current_limit_triggers_independently():
    v = evaluate(_req(rpm=3600.0, motor=standard_motor(i_max=2.0)))
    assert v.status == "INFEASIBLE"
    assert ViolationCode.CURRENT_LIMIT in v.violation_codes
    assert ViolationCode.VOLTAGE_LIMIT not in v.violation_codes
    assert ViolationCode.BOTH_LIMIT not in v.violation_codes


def test_2d_raising_imax_resolves_current_limit():
    """P7 불변식의 통합 수준 확인."""
    assert not evaluate(_req(rpm=3600.0, motor=standard_motor(i_max=2.0))).is_feasible
    assert evaluate(_req(rpm=3600.0, motor=standard_motor(i_max=25.0))).is_feasible


def test_2d_raising_vdc_extends_feasible_speed():
    """P6 불변식의 통합 수준 확인 — 전압 여유가 늘면 더 빨리 돌 수 있다."""
    lo = max_feasible_speed(_req(motor=standard_motor(V_dc=310.0)), tol_rpm=20.0)
    hi = max_feasible_speed(_req(motor=standard_motor(V_dc=450.0)), tol_rpm=20.0)
    assert hi > lo, f"V_dc 310V:{lo:.0f} -> 450V:{hi:.0f} rpm"


# ===========================================================================
# 2-E — 열적 게이트 독립 트리거
# ===========================================================================
def test_2e_discharge_temperature_triggers_at_high_condensing():
    v = evaluate(_req(rpm=3600.0, cond_c=65.0))
    assert ViolationCode.DISCHARGE_TEMP_HIGH in v.violation_codes


def test_2e_thermal_and_electromagnetic_reported_together():
    """전자기 불가여도 열적 위반을 함께 보고해야 설계 피드백이 된다."""
    v = evaluate(_req(rpm=36000.0, cond_c=65.0))
    assert v.status == "INFEASIBLE"
    codes = v.violation_codes
    assert codes & {ViolationCode.VOLTAGE_LIMIT, ViolationCode.BOTH_LIMIT}
    assert ViolationCode.DISCHARGE_TEMP_HIGH in codes


def test_2e_liquid_slugging_fails_regardless_of_motor():
    v = evaluate(_req(rpm=3600.0, dT_superheat=0.0))
    assert v.status == "INFEASIBLE"
    assert ViolationCode.LIQUID_SLUGGING in v.violation_codes


# ===========================================================================
# Step 0 — 사전 위생 검사
# ===========================================================================
def test_step0_pressure_inversion_short_circuits():
    req = EvaluationRequest(
        cycle=CycleSpec(refrigerant="R32", P_evap=2.0e6, P_cond=1.0e6),
        compressor=standard_compressor(),
        motor=standard_motor(),
        prefer_backend="reference",
    )
    v = evaluate(req)
    assert v.status == "INFEASIBLE"
    assert ViolationCode.PRESSURE_INVERSION in v.violation_codes
    assert v.load is None, "사전 검사에서 즉시 반환되어야 한다 (물성 계산 불필요)"


def test_step0_supercritical_detected():
    req = EvaluationRequest(
        cycle=CycleSpec(refrigerant="R32", P_evap=1.0e6, P_cond=6.0e6),
        compressor=standard_compressor(),
        motor=standard_motor(),
        prefer_backend="reference",
    )
    v = evaluate(req)
    assert ViolationCode.SUPERCRITICAL in v.violation_codes
    assert v.status == "INFEASIBLE"


# ===========================================================================
# Verdict 조립 규칙 (plan.md §3.5)
# ===========================================================================
def test_verdict_assembly_rules():
    from compsim.models import Verdict, Violation

    warn = Violation(ViolationCode.LOW_SUPERHEAT, "WARN", "w")
    fail = Violation(ViolationCode.CURRENT_LIMIT, "FAIL", "f")
    assert Verdict.assemble([]).status == "FEASIBLE"
    assert Verdict.assemble([warn]).status == "FEASIBLE_WITH_WARNING"
    assert Verdict.assemble([fail]).status == "INFEASIBLE"
    assert Verdict.assemble([warn, fail]).status == "INFEASIBLE"
    assert Verdict.assemble([warn]).is_feasible
    assert not Verdict.assemble([fail]).is_feasible


def test_reference_backend_always_warns():
    """근사 백엔드 사용 사실이 Verdict 에 반드시 드러나야 한다."""
    v = evaluate(_req())
    assert ViolationCode.REFERENCE_BACKEND_IN_USE in v.violation_codes
    assert v.status == "FEASIBLE_WITH_WARNING"


def test_flow_driven_mode_rejects_sweep():
    from compsim.models import CompressorSpec, EfficiencyCoeffs
    from compsim.units import cc_per_rev_to_m3_per_rev

    req = EvaluationRequest(
        cycle=standard_cycle("R32"),
        compressor=CompressorSpec(
            V_disp=cc_per_rev_to_m3_per_rev(20.0),
            eff=EfficiencyCoeffs.constant(0.70, 0.95, 0.95),
            drive_mode="FLOW_DRIVEN",
            m_dot=0.0287,
        ),
        motor=standard_motor(),
        prefer_backend="reference",
    )
    assert evaluate(req).load is not None
    try:
        sweep_speed(req, [1000.0, 2000.0])
    except ValueError:
        pass
    else:
        raise AssertionError("FLOW_DRIVEN 에서 스윕은 거부되어야 한다")
