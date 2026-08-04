"""전 계층 공용 값 객체 (plan.md §2).

모든 dataclass 는 frozen — 파이프라인은 순수 함수의 연쇄이며 상태를 갖지 않는다.
모든 필드는 **SI 기본 단위**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from .units import (
    TWO_PI,
    celsius_to_kelvin,
    mech_to_elec_speed,
    v_max_from_vdc,
)

#: 지원 냉매. R290(프로판)은 A3 등급 가연성 — 안전 규격은 본 도구 범위 밖이다.
Refrigerant = Literal["R32", "R410A", "R290"]
REFRIGERANTS: tuple[str, ...] = ("R32", "R410A", "R290")
DriveMode = Literal["SPEED_DRIVEN", "FLOW_DRIVEN"]
Modulation = Literal["SVPWM", "SPWM"]
Severity = Literal["WARN", "FAIL"]
Status = Literal["FEASIBLE", "FEASIBLE_WITH_WARNING", "INFEASIBLE"]
ControlMode = Literal["MTPA", "FLUX_WEAKENING", "NONE"]


# ===========================================================================
# 위반 코드 (plan.md §2.1) — MECE
# ===========================================================================
class ViolationCode(str, Enum):
    # 사전 위생 검사
    PRESSURE_INVERSION = "PRESSURE_INVERSION"
    SUPERCRITICAL = "SUPERCRITICAL"
    BACK_EMF_EXCEEDS_VMAX = "BACK_EMF_EXCEEDS_VMAX"
    # 전자기
    CURRENT_LIMIT = "CURRENT_LIMIT"
    VOLTAGE_LIMIT = "VOLTAGE_LIMIT"
    BOTH_LIMIT = "BOTH_LIMIT"
    SOLVER_NOT_CONVERGED = "SOLVER_NOT_CONVERGED"
    # 열적
    DISCHARGE_TEMP_HIGH = "DISCHARGE_TEMP_HIGH"
    MAGNET_DEMAG_RISK = "MAGNET_DEMAG_RISK"
    # 기계
    LIQUID_SLUGGING = "LIQUID_SLUGGING"
    LOW_SUPERHEAT = "LOW_SUPERHEAT"
    NEGATIVE_SUBCOOL = "NEGATIVE_SUBCOOL"
    # 모델 유효성
    PR_OUT_OF_RANGE = "PR_OUT_OF_RANGE"
    EFFICIENCY_EXTRAPOLATED = "EFFICIENCY_EXTRAPOLATED"
    REFERENCE_BACKEND_IN_USE = "REFERENCE_BACKEND_IN_USE"


@dataclass(frozen=True)
class Violation:
    code: ViolationCode
    severity: Severity
    message_ko: str
    actual: float = float("nan")
    limit: float = float("nan")

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return f"[{self.severity}] {self.code.value}: {self.message_ko}"


# ===========================================================================
# 입력 스펙
# ===========================================================================
@dataclass(frozen=True)
class EfficiencyCoeffs:
    """압력비 다항식 상관식 계수 (research.md §1.4).

    eta_isen(PR) = a0 + a1*PR + a2*PR^2
    eta_vol(PR)  = b0 - b1*(PR^(1/kappa) - 1)      # 클리어런스 재팽창 모델
    eta_mech(N)  = c0 + c1*N + c2*N^2              # N [rev/s]
    """

    isen: tuple[float, float, float]
    vol: tuple[float, float, float]  # (b0, b1, kappa)
    mech: tuple[float, float, float]
    preset_name: str = "CUSTOM"
    pr_valid: tuple[float, float] = (1.2, 8.0)

    @staticmethod
    def scroll_default() -> EfficiencyCoeffs:
        # 설계 PR≈3 부근에서 eta_isen 최대 (~0.72), 클리어런스비 ~0.015
        return EfficiencyCoeffs(
            isen=(0.400, 0.200, -0.0320),
            vol=(1.0, 0.015, 1.29),
            mech=(0.975, -2.0e-4, -6.0e-7),
            preset_name="SCROLL_DEFAULT",
        )

    @staticmethod
    def rotary_default() -> EfficiencyCoeffs:
        # 로터리는 누설/마찰이 커 최고 효율이 낮고 클리어런스비가 크다
        return EfficiencyCoeffs(
            isen=(0.380, 0.190, -0.0330),
            vol=(1.0, 0.035, 1.29),
            mech=(0.965, -3.0e-4, -8.0e-7),
            preset_name="ROTARY_DEFAULT",
        )

    @staticmethod
    def constant(eta_isen: float, eta_vol: float, eta_mech: float) -> EfficiencyCoeffs:
        """테스트/검산용 상수 효율 (검증 조건 1에서 사용)."""
        return EfficiencyCoeffs(
            isen=(eta_isen, 0.0, 0.0),
            vol=(eta_vol, 0.0, 1.0),
            mech=(eta_mech, 0.0, 0.0),
            preset_name="CONSTANT",
        )


@dataclass(frozen=True)
class CycleSpec:
    """냉동 사이클 조건. 압력은 Pa, 온도차는 K."""

    refrigerant: Refrigerant
    P_evap: float
    P_cond: float
    dT_superheat: float = 5.0
    dT_subcool: float = 5.0
    dP_suction: float = 0.0
    dP_discharge: float = 0.0

    @property
    def P_suction(self) -> float:
        return self.P_evap - self.dP_suction

    @property
    def P_discharge(self) -> float:
        return self.P_cond + self.dP_discharge

    @property
    def pressure_ratio(self) -> float:
        return self.P_discharge / self.P_suction


@dataclass(frozen=True)
class CompressorSpec:
    """압축기 스펙. V_disp [m^3/rev], N [rev/s], m_dot [kg/s]."""

    V_disp: float
    eff: EfficiencyCoeffs = field(default_factory=EfficiencyCoeffs.scroll_default)
    drive_mode: DriveMode = "SPEED_DRIVEN"
    N: float | None = None
    m_dot: float | None = None

    def __post_init__(self) -> None:
        if self.drive_mode == "SPEED_DRIVEN" and self.N is None:
            raise ValueError("SPEED_DRIVEN 모드에는 N [rev/s] 이 필요합니다.")
        if self.drive_mode == "FLOW_DRIVEN" and self.m_dot is None:
            raise ValueError("FLOW_DRIVEN 모드에는 m_dot [kg/s] 이 필요합니다.")
        if self.V_disp <= 0.0:
            raise ValueError("V_disp 는 양수여야 합니다.")


@dataclass(frozen=True)
class MotorSpec:
    """PMSM 스펙. 전류/전압은 **peak** 기준 (units.DQ_CONVENTION)."""

    Ld: float
    Lq: float
    lambda_pm: float
    p: int  # 극쌍수
    Rs: float
    i_max: float
    V_dc: float
    k_margin: float = 0.95
    modulation: Modulation = "SVPWM"

    def __post_init__(self) -> None:
        for name in ("Ld", "Lq", "lambda_pm", "Rs", "i_max", "V_dc"):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} 는 음수일 수 없습니다.")
        if self.p < 1:
            raise ValueError("p 는 1 이상의 극쌍수여야 합니다.")

    @property
    def v_max(self) -> float:
        return v_max_from_vdc(self.V_dc, self.modulation, self.k_margin)

    @property
    def saliency(self) -> float:
        """Lq - Ld. IPMSM 은 양수, SPMSM 은 0."""
        return self.Lq - self.Ld

    @property
    def is_spmsm(self) -> bool:
        return abs(self.saliency) < 1.0e-9

    @property
    def i_characteristic(self) -> float:
        """특성 전류 lambda_pm / Ld. i_max 보다 작으면 이론상 무한 속도 구동 가능."""
        return self.lambda_pm / self.Ld if self.Ld > 0.0 else float("inf")


@dataclass(frozen=True)
class ThermalLimits:
    """열적/기계적 한계 (research.md §3). 온도는 K.

    게이트 순서 정합성 (중요)
    -------------------------
    세 임계가 토출 온도 축에서 다음 순서를 지켜야 한다.

        T_dis_warn  <  T_dis_at_demag  <  T_dis_fail
          (125 °C)       (130 °C)          (135 °C)

    여기서 T_dis_at_demag = T_demag_limit - dT_magnet_offset 이다.
    이 순서가 깨지면 한 게이트가 다른 게이트를 항상 가려 무의미해진다.
    `check_gate_ordering()` 및 test_thermal_gate 가 이를 강제한다.

    ⚠️ dT_magnet_offset 의 **부호** 주의: 자석은 압축 직후 최고온 토출 가스보다
    낮은 온도에서 평형을 이룬다(셸 내부 혼합/방열). 따라서 음수가 기본값이다.
    흡입 가스 냉각형 모터라면 더 큰 음수를, 자석이 토출부에 직접 노출되는
    특수 구조라면 양수를 넣어야 한다 — research.md §8 Q2 확인 필요 항목.
    """

    T_dis_warn: float = celsius_to_kelvin(125.0)
    T_dis_fail: float = celsius_to_kelvin(135.0)
    T_demag_limit: float = celsius_to_kelvin(120.0)
    dT_magnet_offset: float = -10.0
    dT_sh_warn: float = 3.0
    PR_warn_high: float = 8.0
    PR_warn_low: float = 1.2

    @property
    def T_dis_at_demag(self) -> float:
        """감자 게이트가 발동하기 시작하는 토출 온도."""
        return self.T_demag_limit - self.dT_magnet_offset

    def check_gate_ordering(self) -> str | None:
        """게이트 순서가 정합적인지 확인. 문제가 있으면 설명 문자열을 반환."""
        if not self.T_dis_warn < self.T_dis_fail:
            return "T_dis_warn 이 T_dis_fail 보다 크거나 같습니다."
        d = self.T_dis_at_demag
        if not self.T_dis_warn < d < self.T_dis_fail:
            return (
                f"감자 게이트 발동 온도 {d - 273.15:.1f}°C 가 토출 온도 게이트 구간 "
                f"({self.T_dis_warn - 273.15:.1f} ~ {self.T_dis_fail - 273.15:.1f}°C) "
                f"밖입니다 — 한 게이트가 다른 게이트를 가립니다."
            )
        return None


# ===========================================================================
# 도메인 경계 DTO — 열역학이 생산하고 전자기가 소비하는 유일한 객체
# ===========================================================================
@dataclass(frozen=True)
class LoadPoint:
    T_load: float  # [N*m]
    omega_m: float  # [rad/s]
    omega_e: float  # [rad/s]
    N: float  # [rev/s]
    m_dot: float  # [kg/s]
    T_dis: float  # [K]
    T_suc: float  # [K]
    PR: float
    w_isen: float  # [J/kg]
    P_shaft: float  # [W]
    eta_isen: float
    eta_vol: float
    eta_mech: float
    rho_suc: float  # [kg/m^3]
    h1: float
    h2s: float
    h2: float
    s1: float
    suction_phase: str
    backend_name: str
    extrapolated: bool = False

    @staticmethod
    def from_speed(
        *,
        T_load: float,
        N: float,
        pole_pairs: int,
        **kw: float | str | bool,
    ) -> LoadPoint:  # pragma: no cover - 편의 생성자
        omega_m = TWO_PI * N
        return LoadPoint(
            T_load=T_load,
            omega_m=omega_m,
            omega_e=mech_to_elec_speed(omega_m, pole_pairs),
            N=N,
            **kw,  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class OperatingPoint:
    i_d: float  # [A, peak]
    i_q: float  # [A, peak]
    v_d: float  # [V, peak]
    v_q: float  # [V, peak]
    i_mag: float  # [A]
    v_mag: float  # [V]
    mode: ControlMode
    torque: float  # [N*m] 실제 발생 토크 (검산용)


@dataclass(frozen=True)
class Verdict:
    status: Status
    violations: tuple[Violation, ...]
    load: LoadPoint | None = None
    op: OperatingPoint | None = None
    T_max_avail: float | None = None
    torque_margin: float | None = None

    @property
    def violation_codes(self) -> set[ViolationCode]:
        return {v.code for v in self.violations}

    @property
    def is_feasible(self) -> bool:
        return self.status != "INFEASIBLE"

    @property
    def fails(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity == "FAIL")

    @property
    def warns(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity == "WARN")

    @staticmethod
    def assemble(
        violations: list[Violation],
        *,
        load: LoadPoint | None = None,
        op: OperatingPoint | None = None,
        T_max_avail: float | None = None,
    ) -> Verdict:
        """plan.md §3.5 조립 규칙."""
        has_fail = any(v.severity == "FAIL" for v in violations)
        has_warn = any(v.severity == "WARN" for v in violations)
        if has_fail:
            status: Status = "INFEASIBLE"
        elif has_warn:
            status = "FEASIBLE_WITH_WARNING"
        else:
            status = "FEASIBLE"

        margin: float | None = None
        if load is not None and T_max_avail is not None and T_max_avail > 0.0:
            margin = 1.0 - load.T_load / T_max_avail
        return Verdict(
            status=status,
            violations=tuple(violations),
            load=load,
            op=op,
            T_max_avail=T_max_avail,
            torque_margin=margin,
        )


__all__ = [
    "REFRIGERANTS",
    "CompressorSpec",
    "ControlMode",
    "CycleSpec",
    "DriveMode",
    "EfficiencyCoeffs",
    "LoadPoint",
    "Modulation",
    "MotorSpec",
    "OperatingPoint",
    "Refrigerant",
    "Severity",
    "Status",
    "ThermalLimits",
    "Verdict",
    "Violation",
    "ViolationCode",
]
