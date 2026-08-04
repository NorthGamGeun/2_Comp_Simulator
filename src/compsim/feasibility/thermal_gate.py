"""열적 / 기계적 위험 판별 (research.md §3, plan.md §3.4).

전자기 판정과 **독립적으로** 실행한다. 전자기적으로 불가한 경우에도 열적 위반을
함께 보고해야 설계 피드백으로서 가치가 있기 때문이다.
"""

from __future__ import annotations

from ..models import CycleSpec, LoadPoint, ThermalLimits, Violation, ViolationCode
from ..units import kelvin_to_celsius


def _c(t_k: float) -> float:
    return kelvin_to_celsius(t_k)


def check_discharge_temperature(load: LoadPoint, lim: ThermalLimits) -> list[Violation]:
    """오일 탄화 / 유막 파단 위험 (research.md §3.1)."""
    out: list[Violation] = []
    if load.T_dis >= lim.T_dis_fail:
        out.append(
            Violation(
                ViolationCode.DISCHARGE_TEMP_HIGH,
                "FAIL",
                f"토출 온도 {_c(load.T_dis):.1f}°C 가 한계 {_c(lim.T_dis_fail):.1f}°C 이상 "
                f"— 유막 파단 및 절연 열화 위험",
                actual=load.T_dis,
                limit=lim.T_dis_fail,
            )
        )
    elif load.T_dis >= lim.T_dis_warn:
        out.append(
            Violation(
                ViolationCode.DISCHARGE_TEMP_HIGH,
                "WARN",
                f"토출 온도 {_c(load.T_dis):.1f}°C 가 경고선 {_c(lim.T_dis_warn):.1f}°C 초과 "
                f"— 냉동기유 탄화/산화 개시 영역",
                actual=load.T_dis,
                limit=lim.T_dis_warn,
            )
        )
    return out


def check_magnet_demagnetization(load: LoadPoint, lim: ThermalLimits) -> list[Violation]:
    """영구자석 열적 감자 위험 (research.md §3.2).

    v1 단순화: T_magnet ~ T_dis + offset (토출 가스 냉각형 모터 가정).
    정밀 판별은 자기회로 FEA 가 필요하다 — 본 도구는 스크리닝용이다.
    """
    t_magnet = load.T_dis + lim.dT_magnet_offset
    if t_magnet > lim.T_demag_limit:
        return [
            Violation(
                ViolationCode.MAGNET_DEMAG_RISK,
                "FAIL",
                f"추정 자석 온도 {_c(t_magnet):.1f}°C 가 감자 한계 "
                f"{_c(lim.T_demag_limit):.1f}°C 초과 — 비가역 감자 위험",
                actual=t_magnet,
                limit=lim.T_demag_limit,
            )
        ]
    return []


def check_liquid_slugging(
    cycle: CycleSpec, load: LoadPoint, lim: ThermalLimits
) -> list[Violation]:
    """액 압축 및 과열도 부족 (research.md §3.3)."""
    out: list[Violation] = []
    two_phase = load.suction_phase not in ("gas", "supercritical", "unknown")
    if cycle.dT_superheat <= 0.0 or two_phase:
        out.append(
            Violation(
                ViolationCode.LIQUID_SLUGGING,
                "FAIL",
                f"흡입 과열도 {cycle.dT_superheat:.1f} K, 흡입 상태 '{load.suction_phase}' "
                f"— 액 압축(liquid slugging) 위험",
                actual=cycle.dT_superheat,
                limit=0.0,
            )
        )
    elif cycle.dT_superheat < lim.dT_sh_warn:
        out.append(
            Violation(
                ViolationCode.LOW_SUPERHEAT,
                "WARN",
                f"흡입 과열도 {cycle.dT_superheat:.1f} K 가 권장 {lim.dT_sh_warn:.1f} K 미만 "
                f"— 센서 오차/과도 상태 여유 부족",
                actual=cycle.dT_superheat,
                limit=lim.dT_sh_warn,
            )
        )
    return out


def check_subcool(cycle: CycleSpec) -> list[Violation]:
    if cycle.dT_subcool < 0.0:
        return [
            Violation(
                ViolationCode.NEGATIVE_SUBCOOL,
                "FAIL",
                f"과냉도 {cycle.dT_subcool:.1f} K < 0 — 팽창밸브 입구 플래시 가스 발생",
                actual=cycle.dT_subcool,
                limit=0.0,
            )
        ]
    return []


def check_pressure_ratio_range(load: LoadPoint, lim: ThermalLimits) -> list[Violation]:
    if load.PR > lim.PR_warn_high:
        return [
            Violation(
                ViolationCode.PR_OUT_OF_RANGE,
                "WARN",
                f"압력비 {load.PR:.2f} 가 단단 압축 권장 상한 {lim.PR_warn_high:.1f} 초과 "
                f"— 2단 압축/인젝션 검토 필요",
                actual=load.PR,
                limit=lim.PR_warn_high,
            )
        ]
    if load.PR < lim.PR_warn_low:
        return [
            Violation(
                ViolationCode.PR_OUT_OF_RANGE,
                "WARN",
                f"압력비 {load.PR:.2f} 가 하한 {lim.PR_warn_low:.1f} 미만 — 상관식 외삽 구간",
                actual=load.PR,
                limit=lim.PR_warn_low,
            )
        ]
    return []


def check_model_validity(load: LoadPoint) -> list[Violation]:
    """모델 신뢰도에 대한 정직성 경고."""
    out: list[Violation] = []
    if load.extrapolated:
        out.append(
            Violation(
                ViolationCode.EFFICIENCY_EXTRAPOLATED,
                "WARN",
                f"효율 상관식이 유효 압력비 구간 밖에서 평가됨 (PR={load.PR:.2f})",
                actual=load.PR,
            )
        )
    if "Reference" in load.backend_name:
        out.append(
            Violation(
                ViolationCode.REFERENCE_BACKEND_IN_USE,
                "WARN",
                "CoolProp 미사용 — 근사 물성(오차 약 ±10%)으로 계산되었습니다. "
                "설계 판단에는 CoolProp 설치가 필요합니다.",
            )
        )
    return out


def check_thermal_mechanical(
    cycle: CycleSpec, load: LoadPoint, lim: ThermalLimits
) -> list[Violation]:
    """열적/기계적 게이트 전체 실행."""
    out: list[Violation] = []
    out += check_discharge_temperature(load, lim)
    out += check_magnet_demagnetization(load, lim)
    out += check_liquid_slugging(cycle, load, lim)
    out += check_subcool(cycle)
    out += check_pressure_ratio_range(load, lim)
    out += check_model_validity(load)
    return out


__all__ = [
    "check_discharge_temperature",
    "check_liquid_slugging",
    "check_magnet_demagnetization",
    "check_model_validity",
    "check_pressure_ratio_range",
    "check_subcool",
    "check_thermal_mechanical",
]
