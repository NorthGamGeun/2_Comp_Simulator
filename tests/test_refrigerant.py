"""물성 백엔드 테스트.

참조 백엔드는 '근사'이므로 절대 정확도를 요구하지 않는다.
대신 (a) 포화압력 적합 잔차, (b) 열역학적 자기무모순성, (c) CoolProp 과의
교차 일치도를 검증한다.
"""

from __future__ import annotations

import math

from _helpers import assert_abs, assert_close, rel_err, require_coolprop

from compsim.thermo.refrigerant import (
    CoolPropBackend,
    ReferenceGasBackend,
    coolprop_available,
    get_backend,
    is_reference_backend,
)
from compsim.units import celsius_to_kelvin

# 참조 백엔드 Wagner 적합에 사용한 문헌 포화압력 [°C, kPa]
_R32_SAT = [
    (-40, 172.9), (-30, 269.6), (-20, 404.8), (-10, 587.2), (0, 813.4),
    (10, 1106.9), (20, 1471.1), (30, 1917.5), (40, 2457.9), (50, 3104.6),
]
_R410A_SAT = [
    (-40, 175.0), (-30, 269.9), (-20, 399.6), (-10, 573.0), (0, 798.7),
    (10, 1087.1), (20, 1448.5), (30, 1894.5), (40, 2437.0), (50, 3089.0),
]


def test_reference_wagner_fit_residuals_within_1pct():
    """적합 잔차가 1 % 를 넘으면 계수가 훼손된 것이다."""
    for fluid, table in (("R32", _R32_SAT), ("R410A", _R410A_SAT)):
        be = ReferenceGasBackend(fluid)
        worst = 0.0
        for t_c, p_kpa in table:
            p = be.p_sat(celsius_to_kelvin(t_c))
            worst = max(worst, rel_err(p, p_kpa * 1e3))
        assert worst < 0.01, f"{fluid} Wagner 적합 최대 잔차 {worst*100:.3f}% > 1%"


def test_reference_sat_roundtrip():
    """t_sat(p_sat(T)) == T — 역함수 수치해가 건전한지 확인."""
    for fluid in ("R32", "R410A"):
        be = ReferenceGasBackend(fluid)
        for t_c in (-20.0, 0.0, 7.0, 25.0, 45.0, 60.0):
            T = celsius_to_kelvin(t_c)
            assert_abs(be.t_sat(be.p_sat(T)), T, tol=1e-6, msg=f"{fluid} @{t_c}C")


def test_reference_sat_is_monotonic():
    be = ReferenceGasBackend("R32")
    ps = [be.p_sat(celsius_to_kelvin(t)) for t in range(-40, 70, 5)]
    assert all(b > a for a, b in zip(ps, ps[1:])), "포화압력은 온도에 대해 단조증가"


def test_reference_isentropic_matches_polytropic_formula():
    """참조 백엔드가 research.md §6 '경로 B' 식과 정확히 일치하는지.

    이것이 참조 백엔드의 존재 이유이므로 machine precision 을 요구한다.
    """
    be = ReferenceGasBackend("R32")
    c = be.c
    P1, T1 = 1.0154e6, celsius_to_kelvin(12.0)
    P2 = 2.7574e6
    PR = P2 / P1

    s1 = be.s_pt(P1, T1)
    w_impl = be.h_ps(P2, s1) - be.h_pt(P1, T1)

    expo = (c.gamma - 1.0) / c.gamma
    w_formula = c.Z * (c.gamma / (c.gamma - 1.0)) * c.R * T1 * (PR**expo - 1.0)

    assert_close(w_impl, w_formula, rel=1e-12)


def test_reference_entropy_has_no_Z():
    """엔트로피 관계에 Z 가 들어가면 §6 참조식과 어긋난다 — 회귀 방어."""
    be = ReferenceGasBackend("R32")
    c = be.c
    P1, T1, P2 = 1.0e6, 285.15, 2.8e6
    T2s = be.t_ps(P2, be.s_pt(P1, T1))
    expected = T1 * (P2 / P1) ** ((c.gamma - 1.0) / c.gamma)
    assert_close(T2s, expected, rel=1e-12)


def test_reference_density_uses_Z():
    be = ReferenceGasBackend("R32")
    c = be.c
    P, T = 1.0154e6, 285.15
    assert_close(be.rho_pt(P, T), P / (c.Z * c.R * T), rel=1e-12)
    # 실기체이므로 이상기체보다 밀도가 크다
    assert be.rho_pt(P, T) > P / (c.R * T)


def test_reference_phase_classification():
    be = ReferenceGasBackend("R32")
    P = be.p_sat(celsius_to_kelvin(7.0))
    assert be.phase_pt(P, celsius_to_kelvin(12.0)) == "gas"
    assert be.phase_pt(P, celsius_to_kelvin(0.0)) == "liquid"
    assert be.phase_pt(6.0e6, 300.0) == "supercritical"


def test_r32_has_higher_gamma_and_R_than_r410a():
    """research.md §1.6 — R32 토출 온도 상승의 두 가지 물리적 원인."""
    r32 = ReferenceGasBackend("R32").c
    r410a = ReferenceGasBackend("R410A").c
    assert r32.gamma > r410a.gamma, "R32 의 비열비가 더 커야 한다"
    assert r32.R > r410a.R, "R32 의 비기체상수가 더 커야 한다 (몰질량이 작으므로)"


def test_get_backend_prefer_reference_forces_reference():
    be = get_backend("R32", prefer="reference")
    assert is_reference_backend(be)


def test_get_backend_auto_prefers_coolprop_when_available():
    be = get_backend("R32", prefer="auto")
    if coolprop_available():
        assert isinstance(be, CoolPropBackend)
    else:
        assert is_reference_backend(be)


def test_get_backend_rejects_unknown_fluid_and_prefer():
    for bad in ("R22", "CO2"):
        try:
            get_backend(bad, prefer="reference")
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad} 는 거부되어야 한다")
    try:
        get_backend("R32", prefer="whatever")
    except ValueError:
        pass
    else:
        raise AssertionError("잘못된 prefer 는 거부되어야 한다")


# ---------------------------------------------------------------------------
# CoolProp 필요 — 참조 백엔드가 실제로 '±10 % 근사'인지 확인
# ---------------------------------------------------------------------------
def test_reference_backend_agrees_with_coolprop_within_stated_accuracy():
    require_coolprop()
    for fluid in ("R32", "R410A", "R290"):
        cp = CoolPropBackend(fluid)
        ref = ReferenceGasBackend(fluid)
        for t_c in (0.0, 7.0, 20.0, 45.0):
            T = celsius_to_kelvin(t_c)
            e = rel_err(ref.p_sat(T), cp.p_sat(T))
            assert e < 0.03, f"{fluid} p_sat @{t_c}C 편차 {e*100:.2f}% > 3%"

    cp = CoolPropBackend("R32")
    ref = ReferenceGasBackend("R32")
    P1 = cp.p_sat(celsius_to_kelvin(7.0))
    P2 = cp.p_sat(celsius_to_kelvin(45.0))
    T1 = cp.t_sat(P1) + 5.0
    w_cp = cp.h_ps(P2, cp.s_pt(P1, T1)) - cp.h_pt(P1, T1)
    w_ref = ref.h_ps(P2, ref.s_pt(P1, T1)) - ref.h_pt(P1, T1)
    e = rel_err(w_ref, w_cp)
    assert e < 0.20, (
        f"참조 백엔드 w_isen 편차 {e*100:.1f}% — 문서상 근사 한계를 크게 벗어남 "
        f"(ref={w_ref/1e3:.2f} kJ/kg, coolprop={w_cp/1e3:.2f} kJ/kg)"
    )
    assert not math.isnan(w_cp)
