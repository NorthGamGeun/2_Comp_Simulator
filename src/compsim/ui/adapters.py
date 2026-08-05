"""단위 변환 및 입력 검증 — UI 경계 (plan.md §1.1 원칙 4, Phase 5.1).

**이 모듈만이** 실용 단위(kPa, °C, rpm, cc/rev, mH)와 SI 사이를 변환한다.
PyQt 를 import 하지 않으므로 UI 없이 완전히 테스트 가능하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..feasibility.evaluator import EvaluationRequest
from ..models import (
    CompressorSpec,
    CycleSpec,
    EfficiencyCoeffs,
    MotorSpec,
    ThermalLimits,
)
from ..thermo.refrigerant import get_backend
from ..units import (
    KE_REFERENCES,
    back_emf_line_to_line_rms,
    cc_per_rev_to_m3_per_rev,
    celsius_to_kelvin,
    kg_h_to_kg_s,
    kpa_to_pa,
    lambda_pm_from_ke,
    mh_to_h,
    peak_from_rms,
    rpm_to_rev_s,
)

PRESETS = {
    "스크롤 (기본)": EfficiencyCoeffs.scroll_default,
    "로터리": EfficiencyCoeffs.rotary_default,
}


class InputError(ValueError):
    """UI 입력이 물리적으로 성립하지 않음. 사용자에게 그대로 보여줄 메시지를 담는다."""


@dataclass
class UiInputs:
    """UI 위젯이 보유하는 값 — 전부 **실용 단위**."""

    # 냉매 및 사이클
    refrigerant: str = "R32"
    T_evap_c: float = 7.0
    T_cond_c: float = 45.0
    superheat_k: float = 5.0
    subcool_k: float = 5.0
    dP_suction_kpa: float = 0.0
    dP_discharge_kpa: float = 0.0

    # 압축기
    v_disp_cc: float = 20.0
    eff_preset: str = "스크롤 (기본)"
    drive_mode: str = "SPEED_DRIVEN"
    freq_hz: float = 180.0  # 전기 주파수 [Hz] (= rpm * pole_pairs / 60)
    m_dot_kg_h: float = 100.0

    # 모터
    # ⚠️ Ld/Lq/Rs 는 전부 **상(phase)** 값. LCR 미터로 두 단자 사이를 재면
    #    두 상 권선이 직렬이므로 선간값이 나온다 -> 2로 나눠서 입력할 것.
    Ld_mH: float = 3.0
    Lq_mH: float = 6.0
    #: 역기전력 상수 [Vrms/krpm]. 기본값은 lambda_pm = 0.08 Wb (p=3, 선간 기준) 에 대응.
    ke_vrms_krpm: float = 30.78
    #: Ke 의 전압 기준 — "LINE_TO_LINE"(기본) 또는 "PHASE". 둘은 sqrt(3) 배 차이.
    ke_reference: str = "LINE_TO_LINE"
    pole_pairs: int = 3
    Rs_ohm: float = 0.5
    i_max_A: float = 20.0
    V_dc: float = 310.0
    k_margin: float = 0.95
    modulation: str = "SVPWM"
    current_is_rms: bool = False

    # 한계
    T_dis_warn_c: float = 125.0
    T_dis_fail_c: float = 135.0
    T_demag_c: float = 120.0
    dT_magnet_offset_k: float = -10.0

    prefer_backend: str = "auto"

    def validate(self) -> list[str]:
        """치명적이지 않은 경고 목록을 반환. 치명적 오류는 InputError 로 던진다."""
        warns: list[str] = []

        if self.T_cond_c <= self.T_evap_c:
            raise InputError(
                f"응축 온도({self.T_cond_c} °C)는 증발 온도({self.T_evap_c} °C)보다 높아야 합니다."
            )
        if self.v_disp_cc <= 0.0:
            raise InputError("행정체적은 0보다 커야 합니다.")
        if self.pole_pairs < 1:
            raise InputError("극쌍수는 1 이상의 정수여야 합니다.")
        if self.Ld_mH <= 0.0 or self.Lq_mH <= 0.0:
            raise InputError("상 인덕턴스는 0보다 커야 합니다.")
        if self.ke_vrms_krpm <= 0.0:
            raise InputError("역기전력 상수 Ke 는 0보다 커야 합니다.")
        if self.ke_reference.upper() not in KE_REFERENCES:
            raise InputError(
                f"Ke 기준은 {' 또는 '.join(KE_REFERENCES)} 여야 합니다: {self.ke_reference!r}"
            )
        if self.i_max_A <= 0.0 or self.V_dc <= 0.0:
            raise InputError("최대 전류와 직류단 전압은 0보다 커야 합니다.")
        if not 0.0 < self.k_margin <= 1.0:
            raise InputError("전압 마진 계수는 (0, 1] 범위여야 합니다.")
        if self.drive_mode == "SPEED_DRIVEN" and self.freq_hz <= 0.0:
            raise InputError("전기 주파수는 0보다 커야 합니다.")
        if self.drive_mode == "FLOW_DRIVEN" and self.m_dot_kg_h <= 0.0:
            raise InputError("질량 유량은 0보다 커야 합니다.")

        if self.Lq_mH < self.Ld_mH:
            warns.append(
                "Lq < Ld 입니다. 일반적인 IPMSM 은 Lq > Ld 이며, 이 경우 MTPA 가 "
                "i_d > 0 을 선택합니다. 상 인덕턴스를 입력했는지 확인하십시오."
            )
        if self.ke_reference.upper() == "PHASE":
            warns.append(
                "Ke 를 상(phase) 기준으로 해석 중입니다. 데이터시트가 선간 기준이면 "
                "쇄교자속이 √3 배 과대평가되어 토크가 73 % 틀어집니다."
            )
        if self.superheat_k <= 0.0:
            warns.append("흡입 과열도가 0 이하입니다 — 액 압축 위험으로 판정됩니다.")
        limits = self._limits()
        problem = limits.check_gate_ordering()
        if problem:
            warns.append(f"열적 게이트 순서 경고: {problem}")
        return warns

    # --- 파생값 (UI 표시 및 검산용) ---------------------------------------
    @property
    def lambda_pm_Wb(self) -> float:
        """Ke 로부터 환산된 영구자석 쇄교자속 [Wb, 상 peak].

        코어 물리는 여전히 lambda_pm 으로 동작한다 — Ke 는 **입력 편의를 위한
        표현**일 뿐이며, 여기서 단 한 번 환산된다.
        """
        return lambda_pm_from_ke(self.ke_vrms_krpm, int(self.pole_pairs), self.ke_reference)

    @property
    def rpm(self) -> float:
        """전기 주파수 [Hz] -> 기계 회전수 [rpm]. rpm = freq_hz * 60 / pole_pairs."""
        return self.freq_hz * 60.0 / self.pole_pairs

    def back_emf_at(self, rpm: float) -> float:
        """해당 회전수의 무부하 선간 역기전력 실효값 [V] — 실측 대조용."""
        return back_emf_line_to_line_rms(self.lambda_pm_Wb, int(self.pole_pairs), rpm)

    def _limits(self) -> ThermalLimits:
        return ThermalLimits(
            T_dis_warn=celsius_to_kelvin(self.T_dis_warn_c),
            T_dis_fail=celsius_to_kelvin(self.T_dis_fail_c),
            T_demag_limit=celsius_to_kelvin(self.T_demag_c),
            dT_magnet_offset=self.dT_magnet_offset_k,
        )

    def to_request(self) -> EvaluationRequest:
        """SI 로 변환하여 코어가 소비할 요청 객체를 만든다."""
        self.validate()
        be = get_backend(self.refrigerant, prefer=self.prefer_backend)

        cycle = CycleSpec(
            refrigerant=self.refrigerant,  # type: ignore[arg-type]
            P_evap=be.p_sat(celsius_to_kelvin(self.T_evap_c)),
            P_cond=be.p_sat(celsius_to_kelvin(self.T_cond_c)),
            dT_superheat=self.superheat_k,
            dT_subcool=self.subcool_k,
            dP_suction=kpa_to_pa(self.dP_suction_kpa),
            dP_discharge=kpa_to_pa(self.dP_discharge_kpa),
        )
        comp = CompressorSpec(
            V_disp=cc_per_rev_to_m3_per_rev(self.v_disp_cc),
            eff=PRESETS[self.eff_preset](),
            drive_mode=self.drive_mode,  # type: ignore[arg-type]
            N=rpm_to_rev_s(self.rpm) if self.drive_mode == "SPEED_DRIVEN" else None,
            m_dot=kg_h_to_kg_s(self.m_dot_kg_h) if self.drive_mode == "FLOW_DRIVEN" else None,
        )
        # 데이터시트가 RMS 기준이면 peak 로 변환 (research.md §0.2 함정 1)
        i_max = peak_from_rms(self.i_max_A) if self.current_is_rms else self.i_max_A
        motor = MotorSpec(
            Ld=mh_to_h(self.Ld_mH),
            Lq=mh_to_h(self.Lq_mH),
            lambda_pm=self.lambda_pm_Wb,  # Ke -> lambda_pm 환산 (property)
            p=int(self.pole_pairs),
            Rs=self.Rs_ohm,
            i_max=i_max,
            V_dc=self.V_dc,
            k_margin=self.k_margin,
            modulation=self.modulation,  # type: ignore[arg-type]
        )
        return EvaluationRequest(
            cycle=cycle,
            compressor=comp,
            motor=motor,
            limits=self._limits(),
            prefer_backend=self.prefer_backend,
        )


@dataclass
class FieldSpec:
    """입력 위젯 생성용 메타데이터 — 위젯 코드와 단위 규약의 단일 출처."""

    attr: str
    label: str
    unit: str
    decimals: int = 2
    minimum: float = -1.0e9
    maximum: float = 1.0e9
    step: float = 1.0
    group: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


CYCLE_FIELDS = [
    FieldSpec("T_evap_c", "증발 온도", "°C", 1, -40.0, 30.0, 1.0, "냉매 · 사이클"),
    FieldSpec("T_cond_c", "응축 온도", "°C", 1, 10.0, 75.0, 1.0, "냉매 · 사이클"),
    FieldSpec("superheat_k", "흡입 과열도", "K", 1, 0.0, 30.0, 0.5, "냉매 · 사이클"),
    FieldSpec("subcool_k", "과냉도", "K", 1, -5.0, 30.0, 0.5, "냉매 · 사이클"),
    FieldSpec("dP_suction_kpa", "흡입 압력손실", "kPa", 1, 0.0, 200.0, 5.0, "냉매 · 사이클"),
    FieldSpec("dP_discharge_kpa", "토출 압력손실", "kPa", 1, 0.0, 200.0, 5.0, "냉매 · 사이클"),
]

COMPRESSOR_FIELDS = [
    FieldSpec("v_disp_cc", "행정체적", "cc/rev", 2, 0.1, 500.0, 1.0, "압축기"),
    FieldSpec("freq_hz", "회전수 (전기 주파수)", "Hz", 1, 1.0, 1000.0, 5.0, "압축기",
             {"tip": "전기 주파수 [Hz] = 기계 회전수[rpm] × 극쌍수 / 60\n"
                     "기계 rpm = Hz × 60 / 극쌍수"}),
    FieldSpec("m_dot_kg_h", "질량 유량", "kg/h", 1, 0.1, 5000.0, 5.0, "압축기"),
]

MOTOR_FIELDS = [
    FieldSpec(
        "Ld_mH", "d축 인덕턴스 Ld (상)", "mH", 3, 0.01, 500.0, 0.1, "모터 (PMSM)",
        {"tip": "**상(phase)** 인덕턴스입니다. LCR 미터로 두 단자(예: U–V) 사이를 재면 "
                "두 상 권선이 직렬이라 상값의 2배가 나옵니다 → 2로 나눠 입력하십시오."},
    ),
    FieldSpec(
        "Lq_mH", "q축 인덕턴스 Lq (상)", "mH", 3, 0.01, 500.0, 0.1, "모터 (PMSM)",
        {"tip": "**상(phase)** 인덕턴스. IPMSM 은 Lq > Ld 이며 돌극비 Lq/Ld 는 "
                "보통 1.5~3.0 입니다."},
    ),
    FieldSpec(
        "ke_vrms_krpm", "역기전력 상수 Ke", "Vrms/krpm", 3, 0.01, 5000.0, 0.5, "모터 (PMSM)",
        {"tip": "무부하 역기전력 실효값 ÷ 회전수[krpm]. 아래 '기준' 콤보에서 선간/상을 "
                "반드시 확인하십시오 — 둘은 √3 배 차이입니다."},
    ),
    FieldSpec("pole_pairs", "극쌍수 p", "-", 0, 1.0, 24.0, 1.0, "모터 (PMSM)",
              {"tip": "극수가 아니라 **극쌍수**입니다. 6극 모터 → 3."}),
    FieldSpec("Rs_ohm", "상저항 Rs (상)", "Ω", 3, 0.0, 100.0, 0.05, "모터 (PMSM)",
              {"tip": "**상당(per-phase)** 저항. 선간 측정값이면 2로 나누십시오."}),
    FieldSpec("i_max_A", "최대 허용 전류", "A", 2, 0.1, 1000.0, 1.0, "모터 (PMSM)",
              {"tip": "peak 기준. 데이터시트가 RMS 면 위의 체크박스를 켜십시오."}),
    FieldSpec("V_dc", "직류단 전압 Vdc", "V", 1, 10.0, 2000.0, 10.0, "모터 (PMSM)"),
    FieldSpec("k_margin", "전압 마진 계수", "-", 3, 0.5, 1.0, 0.01, "모터 (PMSM)"),
]

LIMIT_FIELDS = [
    FieldSpec("T_dis_warn_c", "토출 온도 경고", "°C", 1, 60.0, 200.0, 5.0, "열적 한계"),
    FieldSpec("T_dis_fail_c", "토출 온도 한계", "°C", 1, 60.0, 220.0, 5.0, "열적 한계"),
    FieldSpec("T_demag_c", "자석 감자 한계", "°C", 1, 60.0, 250.0, 5.0, "열적 한계"),
    FieldSpec("dT_magnet_offset_k", "자석 온도 오프셋", "K", 1, -60.0, 60.0, 1.0, "열적 한계"),
]

ALL_FIELDS = CYCLE_FIELDS + COMPRESSOR_FIELDS + MOTOR_FIELDS + LIMIT_FIELDS


__all__ = [
    "ALL_FIELDS",
    "COMPRESSOR_FIELDS",
    "CYCLE_FIELDS",
    "LIMIT_FIELDS",
    "MOTOR_FIELDS",
    "PRESETS",
    "FieldSpec",
    "InputError",
    "UiInputs",
]
