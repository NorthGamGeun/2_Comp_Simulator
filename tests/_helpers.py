"""테스트 유틸리티 및 표준 픽스처."""

from __future__ import annotations

import pytest

from compsim.models import (
    CompressorSpec,
    CycleSpec,
    EfficiencyCoeffs,
    MotorSpec,
    ThermalLimits,
)
from compsim.thermo.refrigerant import ReferenceGasBackend, coolprop_available
from compsim.units import (
    cc_per_rev_to_m3_per_rev,
    celsius_to_kelvin,
    lambda_pm_from_ke,
    mh_to_h,
    rpm_to_rev_s,
)


# ===========================================================================
# 단정 유틸리티
# ===========================================================================
def assert_abs(actual: float, expected: float, *, tol: float = 1e-6, msg: str = "") -> None:
    """절대 오차 기반 단정."""
    diff = abs(actual - expected)
    tail = f" ({msg})" if msg else ""
    assert diff <= tol, f"{actual} != {expected} (diff={diff}, tol={tol}){tail}"


def assert_close(actual: float, expected: float, *, rel: float = 1e-6, msg: str = "") -> None:
    """상대 오차 기반 단정. expected==0 이면 절대 비교로 폴백."""
    if expected == 0.0:
        diff = abs(actual)
        tail = f" ({msg})" if msg else ""
        assert diff <= rel, f"{actual} != 0.0 (abs={diff}, rel_tol={rel}){tail}"
        return
    r = abs((actual - expected) / expected)
    tail = f" ({msg})" if msg else ""
    assert r <= rel, f"{actual} != {expected} (rel_err={r:.3e}, tol={rel}){tail}"


def rel_err(actual: float, expected: float) -> float:
    """상대 오차 (부호 없는 양수). expected==0 이면 abs(actual) 반환."""
    if expected == 0.0:
        return abs(actual)
    return abs((actual - expected) / expected)


# ===========================================================================
# CoolProp 가용성 스킵
# ===========================================================================
def require_coolprop() -> None:
    """CoolProp 이 없으면 테스트를 skip 처리한다."""
    if not coolprop_available():
        pytest.skip("CoolProp 미설치 — 교차 검증 테스트 건너뜀")


# ===========================================================================
# 표준 픽스처
# ===========================================================================
def standard_motor(**overrides) -> MotorSpec:
    """표준 IPM 모터 (case_r32_standard.json 기준).

    Ld=3 mH, Lq=6 mH, Ke=30.78 Vrms/krpm (선간), p=3, Rs=0.5 Ω,
    i_max=20 A, V_dc=310 V, SVPWM, k_margin=0.95.
    """
    defaults = dict(
        Ld=mh_to_h(3.0),           # 3 mH
        Lq=mh_to_h(6.0),           # 6 mH
        lambda_pm=lambda_pm_from_ke(30.78, pole_pairs=3, reference="LINE_TO_LINE"),
        p=3,
        Rs=0.5,
        i_max=20.0,
        V_dc=310.0,
        k_margin=0.95,
        modulation="SVPWM",
    )
    defaults.update(overrides)
    return MotorSpec(**defaults)


def standard_spmsm(**overrides) -> MotorSpec:
    """표면 부착형 PMSM (Ld == Lq, 돌극성 없음).

    Ld=Lq=5 mH, lambda_pm=0.08 Wb, p=3, Rs=0.5 Ω, i_max=20 A, V_dc=310 V.
    """
    defaults = dict(
        Ld=mh_to_h(5.0),
        Lq=mh_to_h(5.0),           # Ld == Lq → saliency = 0
        lambda_pm=0.08,
        p=3,
        Rs=0.5,
        i_max=20.0,
        V_dc=310.0,
        k_margin=0.95,
        modulation="SVPWM",
    )
    defaults.update(overrides)
    return MotorSpec(**defaults)


def standard_compressor(*, rpm: float = 3600.0, **overrides) -> CompressorSpec:
    """표준 스크롤 압축기 (20 cc/rev, 상수 효율, 속도 구동).

    상수 효율을 사용하여 테스트 결과를 해석적으로 검증 가능하게 한다.
    """
    defaults = dict(
        V_disp=cc_per_rev_to_m3_per_rev(20.0),
        eff=EfficiencyCoeffs.constant(0.70, 0.95, 0.95),
        drive_mode="SPEED_DRIVEN",
        N=rpm_to_rev_s(rpm),
    )
    defaults.update(overrides)
    return CompressorSpec(**defaults)


def standard_cycle(
    refrigerant: str = "R32",
    *,
    evap_c: float = 7.0,
    cond_c: float = 45.0,
    dT_superheat: float = 5.0,
    dT_subcool: float = 5.0,
    dP_suction: float = 0.0,
    dP_discharge: float = 0.0,
) -> CycleSpec:
    """표준 냉동 사이클 (증발 7 °C / 응축 45 °C).

    온도에서 포화 압력을 참조 백엔드로 환산한다.
    """
    be = ReferenceGasBackend(refrigerant)
    P_evap = be.p_sat(celsius_to_kelvin(evap_c))
    P_cond = be.p_sat(celsius_to_kelvin(cond_c))
    return CycleSpec(
        refrigerant=refrigerant,
        P_evap=P_evap,
        P_cond=P_cond,
        dT_superheat=dT_superheat,
        dT_subcool=dT_subcool,
        dP_suction=dP_suction,
        dP_discharge=dP_discharge,
    )


def standard_limits(**overrides) -> ThermalLimits:
    """기본 열적/기계적 한계 (ThermalLimits 기본값 사용, 오버라이드 가능)."""
    defaults = dict(
        T_dis_warn=celsius_to_kelvin(125.0),
        T_dis_fail=celsius_to_kelvin(135.0),
        T_demag_limit=celsius_to_kelvin(120.0),
        dT_magnet_offset=-10.0,
        dT_sh_warn=3.0,
        PR_warn_high=8.0,
        PR_warn_low=1.2,
    )
    defaults.update(overrides)
    return ThermalLimits(**defaults)
