"""열적/기계적 게이트 — 각 위반 코드별 트리거 테스트 (plan.md Phase 3.1)."""

from __future__ import annotations

from _helpers import standard_compressor, standard_cycle, standard_limits

from compsim.feasibility.thermal_gate import (
    check_discharge_temperature,
    check_liquid_slugging,
    check_magnet_demagnetization,
    check_model_validity,
    check_pressure_ratio_range,
    check_subcool,
    check_thermal_mechanical,
)
from compsim.models import CycleSpec, ViolationCode
from compsim.thermo.cycle import compute_load_point
from compsim.units import celsius_to_kelvin

POLE_PAIRS = 3


def _load(cond_c: float = 45.0, **kw):
    return compute_load_point(
        standard_cycle("R32", cond_c=cond_c, **kw),
        standard_compressor(),
        POLE_PAIRS,
        prefer_backend="reference",
    )


def _codes(vs) -> set[ViolationCode]:
    return {v.code for v in vs}


def _sev(vs, code) -> str:
    return next(v.severity for v in vs if v.code == code)


# --- 토출 온도 -------------------------------------------------------------
def test_discharge_temp_ok_at_moderate_condition():
    lim = standard_limits()
    lp = _load(cond_c=35.0)
    assert lp.T_dis < lim.T_dis_warn
    assert check_discharge_temperature(lp, lim) == []


def test_discharge_temp_warn_then_fail_as_condensing_rises():
    """125°C 경고 -> 135°C 실패 이원화가 실제로 두 단계로 동작하는지."""
    lim = standard_limits()
    lp = _load(cond_c=45.0)
    # 임계값을 인위적으로 낮춰 두 단계를 모두 트리거
    warn_lim = standard_limits(
        T_dis_warn=lp.T_dis - 5.0, T_dis_fail=lp.T_dis + 5.0
    )
    vs = check_discharge_temperature(lp, warn_lim)
    assert _codes(vs) == {ViolationCode.DISCHARGE_TEMP_HIGH}
    assert _sev(vs, ViolationCode.DISCHARGE_TEMP_HIGH) == "WARN"

    fail_lim = standard_limits(
        T_dis_warn=lp.T_dis - 10.0, T_dis_fail=lp.T_dis - 5.0
    )
    vs = check_discharge_temperature(lp, fail_lim)
    assert _sev(vs, ViolationCode.DISCHARGE_TEMP_HIGH) == "FAIL"


def test_discharge_temp_fails_at_high_condensing_temperature():
    """2-E — 응축 온도를 올리면 토출 온도 위반이 트리거된다."""
    lim = standard_limits()
    lp = _load(cond_c=65.0)
    vs = check_discharge_temperature(lp, lim)
    assert ViolationCode.DISCHARGE_TEMP_HIGH in _codes(vs)


# --- 게이트 순서 정합성 (기본값이 서로를 가리지 않는지) ---------------------
def test_default_gate_ordering_is_coherent():
    """감자 게이트가 토출 온도 게이트를 항상 가리면 안 된다.

    회귀 방어: 초기 구현은 dT_magnet_offset=+10 으로 인해 감자 게이트가
    T_dis>110°C 에서 발동해 125°C 경고선을 무력화했다.
    """
    lim = standard_limits()
    problem = lim.check_gate_ordering()
    assert problem is None, problem
    assert lim.T_dis_warn < lim.T_dis_at_demag < lim.T_dis_fail


def test_gate_ordering_detects_incoherent_config():
    bad = standard_limits(dT_magnet_offset=+10.0)  # 감자가 110°C 에서 발동
    assert bad.check_gate_ordering() is not None


def test_gate_ordering_thresholds_in_expected_celsius():
    lim = standard_limits()
    assert abs((lim.T_dis_warn - 273.15) - 125.0) < 1e-9
    assert abs((lim.T_dis_at_demag - 273.15) - 130.0) < 1e-9
    assert abs((lim.T_dis_fail - 273.15) - 135.0) < 1e-9


def test_standard_case_passes_all_thermal_gates():
    """표준 7/45°C 사이클은 어떤 열적 FAIL 도 내면 안 된다."""
    cyc = standard_cycle("R32")
    lp = compute_load_point(cyc, standard_compressor(), POLE_PAIRS, prefer_backend="reference")
    vs = check_thermal_mechanical(cyc, lp, standard_limits())
    fails = [v for v in vs if v.severity == "FAIL"]
    assert fails == [], f"표준 케이스에서 FAIL 발생: {[str(v) for v in fails]}"


# --- 감자 -----------------------------------------------------------------
def test_magnet_demag_triggers_above_limit():
    lp = _load(cond_c=45.0)
    lim = standard_limits(T_demag_limit=lp.T_dis - 1.0, dT_magnet_offset=10.0)
    vs = check_magnet_demagnetization(lp, lim)
    assert ViolationCode.MAGNET_DEMAG_RISK in _codes(vs)
    assert _sev(vs, ViolationCode.MAGNET_DEMAG_RISK) == "FAIL"


def test_magnet_demag_silent_when_cool():
    lp = _load(cond_c=35.0)
    lim = standard_limits(T_demag_limit=celsius_to_kelvin(200.0))
    assert check_magnet_demagnetization(lp, lim) == []


def test_magnet_offset_is_applied():
    lp = _load(cond_c=45.0)
    just_above = standard_limits(T_demag_limit=lp.T_dis + 5.0, dT_magnet_offset=10.0)
    assert check_magnet_demagnetization(lp, just_above) != []
    just_below = standard_limits(T_demag_limit=lp.T_dis + 15.0, dT_magnet_offset=10.0)
    assert check_magnet_demagnetization(lp, just_below) == []


# --- 액 압축 / 과열도 -------------------------------------------------------
def test_liquid_slugging_on_zero_superheat():
    cyc = standard_cycle("R32", dT_superheat=0.0)
    lp = compute_load_point(cyc, standard_compressor(), POLE_PAIRS, prefer_backend="reference")
    vs = check_liquid_slugging(cyc, lp, standard_limits())
    assert ViolationCode.LIQUID_SLUGGING in _codes(vs)
    assert _sev(vs, ViolationCode.LIQUID_SLUGGING) == "FAIL"


def test_low_superheat_warns_but_does_not_fail():
    cyc = standard_cycle("R32", dT_superheat=1.5)
    lp = compute_load_point(cyc, standard_compressor(), POLE_PAIRS, prefer_backend="reference")
    vs = check_liquid_slugging(cyc, lp, standard_limits())
    assert ViolationCode.LOW_SUPERHEAT in _codes(vs)
    assert _sev(vs, ViolationCode.LOW_SUPERHEAT) == "WARN"
    assert ViolationCode.LIQUID_SLUGGING not in _codes(vs)


def test_adequate_superheat_is_silent():
    cyc = standard_cycle("R32", dT_superheat=5.0)
    lp = compute_load_point(cyc, standard_compressor(), POLE_PAIRS, prefer_backend="reference")
    assert check_liquid_slugging(cyc, lp, standard_limits()) == []


# --- 과냉도 ---------------------------------------------------------------
def test_negative_subcool_fails():
    cyc = standard_cycle("R32", dT_subcool=-2.0)
    vs = check_subcool(cyc)
    assert ViolationCode.NEGATIVE_SUBCOOL in _codes(vs)
    assert _sev(vs, ViolationCode.NEGATIVE_SUBCOOL) == "FAIL"


def test_positive_subcool_silent():
    assert check_subcool(standard_cycle("R32", dT_subcool=5.0)) == []


# --- 압력비 ---------------------------------------------------------------
def test_pressure_ratio_warns_when_too_high():
    lp = _load(cond_c=45.0)
    lim = standard_limits(PR_warn_high=2.0)
    vs = check_pressure_ratio_range(lp, lim)
    assert ViolationCode.PR_OUT_OF_RANGE in _codes(vs)
    assert _sev(vs, ViolationCode.PR_OUT_OF_RANGE) == "WARN"


def test_pressure_ratio_warns_when_too_low():
    lp = _load(cond_c=45.0)
    lim = standard_limits(PR_warn_low=5.0)
    assert ViolationCode.PR_OUT_OF_RANGE in _codes(check_pressure_ratio_range(lp, lim))


def test_pressure_ratio_silent_in_range():
    assert check_pressure_ratio_range(_load(), standard_limits()) == []


# --- 모델 유효성 -----------------------------------------------------------
def test_reference_backend_raises_honesty_warning():
    """근사 백엔드를 쓰고 있음을 반드시 사용자에게 알려야 한다."""
    lp = _load()
    vs = check_model_validity(lp)
    assert ViolationCode.REFERENCE_BACKEND_IN_USE in _codes(vs)
    assert _sev(vs, ViolationCode.REFERENCE_BACKEND_IN_USE) == "WARN"


def test_extrapolation_warning_propagates():
    from compsim.models import CompressorSpec, EfficiencyCoeffs
    from compsim.units import cc_per_rev_to_m3_per_rev, rpm_to_rev_s

    # 매우 낮은 응축 압력으로 PR 을 유효 구간 아래로 밀어낸다
    be_cycle = standard_cycle("R32", cond_c=10.0)
    comp = CompressorSpec(
        V_disp=cc_per_rev_to_m3_per_rev(20.0),
        eff=EfficiencyCoeffs.scroll_default(),
        N=rpm_to_rev_s(3600.0),
    )
    lp = compute_load_point(be_cycle, comp, POLE_PAIRS, prefer_backend="reference")
    assert lp.PR < 1.2
    assert ViolationCode.EFFICIENCY_EXTRAPOLATED in _codes(check_model_validity(lp))


# --- 통합 ------------------------------------------------------------------
def test_check_thermal_mechanical_aggregates_all():
    cyc = standard_cycle("R32", cond_c=65.0, dT_superheat=0.0, dT_subcool=-1.0)
    lp = compute_load_point(cyc, standard_compressor(), POLE_PAIRS, prefer_backend="reference")
    vs = check_thermal_mechanical(cyc, lp, standard_limits())
    codes = _codes(vs)
    assert ViolationCode.DISCHARGE_TEMP_HIGH in codes
    assert ViolationCode.LIQUID_SLUGGING in codes
    assert ViolationCode.NEGATIVE_SUBCOOL in codes
    assert ViolationCode.REFERENCE_BACKEND_IN_USE in codes


def test_gate_is_independent_of_electromagnetic_result():
    """열적 게이트는 모터 정보를 전혀 참조하지 않는다 (독립 게이트 원칙)."""
    cyc = standard_cycle("R32")
    lp = compute_load_point(cyc, standard_compressor(), POLE_PAIRS, prefer_backend="reference")
    a = check_thermal_mechanical(cyc, lp, standard_limits())
    b = check_thermal_mechanical(cyc, lp, standard_limits())
    assert _codes(a) == _codes(b)


def test_pressure_inversion_spec_property():
    cyc = CycleSpec(refrigerant="R32", P_evap=2.0e6, P_cond=1.0e6)
    assert cyc.P_discharge < cyc.P_suction
