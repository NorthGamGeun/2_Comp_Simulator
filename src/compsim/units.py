"""SI 단위 규약 및 경계 변환 (plan.md §1.1 원칙 4).

절대 규칙
---------
1. 코어 연산은 **전량 SI 기본 단위**. 이 모듈 밖에서 kPa/rpm/mH/cc 를 다루지 않는다.
2. dq 변환은 **amplitude-invariant (peak) 기준** (plan.md D4).
   - 토크식 계수 3/2
   - i_max, v_max 는 모두 peak 값
   - 데이터시트가 RMS 이면 `peak_from_rms()` 로 변환 후 주입할 것
3. `p` 는 **극쌍수(pole pairs)**. 8극 모터는 p=4.
"""

from __future__ import annotations

import math

# --- 수학 상수 -------------------------------------------------------------
SQRT2: float = math.sqrt(2.0)
SQRT3: float = math.sqrt(3.0)
TWO_PI: float = 2.0 * math.pi

# --- dq 규약 상수 (research.md §0.2 함정 1) --------------------------------
#: amplitude-invariant Clarke/Park 변환에서의 토크식 계수.
#: power-invariant 규약을 쓸 경우 1.0 이 되지만, 본 프로젝트는 3/2 로 고정한다.
TORQUE_DQ_COEFF: float = 1.5

#: 규약 식별자 — 테스트가 이 값을 고정하여 규약 변경을 감지한다.
DQ_CONVENTION: str = "amplitude-invariant (peak)"

# --- 절대온도 --------------------------------------------------------------
T0_KELVIN: float = 273.15


# ===========================================================================
# 스칼라 변환 — 이름은 항상 `<from>_to_<to>`
# ===========================================================================
def celsius_to_kelvin(t_c: float) -> float:
    return t_c + T0_KELVIN


def kelvin_to_celsius(t_k: float) -> float:
    return t_k - T0_KELVIN


def kpa_to_pa(p_kpa: float) -> float:
    return p_kpa * 1.0e3


def pa_to_kpa(p_pa: float) -> float:
    return p_pa / 1.0e3


def bar_to_pa(p_bar: float) -> float:
    return p_bar * 1.0e5


def pa_to_bar(p_pa: float) -> float:
    return p_pa / 1.0e5


def rpm_to_rev_s(n_rpm: float) -> float:
    """회전수 [rpm] -> [rev/s]."""
    return n_rpm / 60.0


def rev_s_to_rpm(n_rev_s: float) -> float:
    return n_rev_s * 60.0


def rpm_to_rad_s(n_rpm: float) -> float:
    """기계 각속도 omega_m [rad/s]."""
    return n_rpm * TWO_PI / 60.0


def rad_s_to_rpm(w_rad_s: float) -> float:
    return w_rad_s * 60.0 / TWO_PI


def mh_to_h(l_mh: float) -> float:
    """인덕턴스 [mH] -> [H]."""
    return l_mh * 1.0e-3


def h_to_mh(l_h: float) -> float:
    return l_h * 1.0e3


def cc_per_rev_to_m3_per_rev(v_cc: float) -> float:
    """행정체적 [cc/rev] -> [m^3/rev]."""
    return v_cc * 1.0e-6


def m3_per_rev_to_cc_per_rev(v_m3: float) -> float:
    return v_m3 * 1.0e6


def kg_h_to_kg_s(m_kg_h: float) -> float:
    return m_kg_h / 3600.0


def kg_s_to_kg_h(m_kg_s: float) -> float:
    return m_kg_s * 3600.0


def kj_per_kg_to_j_per_kg(w_kj: float) -> float:
    return w_kj * 1.0e3


def j_per_kg_to_kj_per_kg(w_j: float) -> float:
    return w_j / 1.0e3


# --- peak / RMS ------------------------------------------------------------
def peak_from_rms(x_rms: float) -> float:
    """정현파 RMS -> peak(진폭). 데이터시트 정격이 RMS 일 때 사용."""
    return x_rms * SQRT2


def rms_from_peak(x_peak: float) -> float:
    return x_peak / SQRT2


# --- 선간 / 상 (Y 결선) ----------------------------------------------------
def phase_from_line_to_line(x_ll: float) -> float:
    """Y 결선 선간(line-to-line) 전압 -> 상(phase) 전압. 동일 기준(RMS/peak) 유지."""
    return x_ll / SQRT3


def line_to_line_from_phase(x_ph: float) -> float:
    return x_ph * SQRT3


def phase_inductance_from_line_to_line(l_ll: float) -> float:
    """LCR 미터로 두 단자 사이를 재면 두 상 권선이 직렬이므로 상값의 2배가 나온다."""
    return l_ll / 2.0


def phase_resistance_from_line_to_line(r_ll: float) -> float:
    """상저항도 동일하게 선간 측정값의 절반."""
    return r_ll / 2.0


# --- 역기전력 상수 <-> 쇄교자속 --------------------------------------------
#: Ke[Vrms/krpm, 상 기준] -> lambda_pm[Wb] 환산 계수 (극쌍수로 나누기 전).
#:
#: 유도:  E_ph,peak = omega_e * lambda_pm,  omega_e = p * 2*pi*(1000*N_krpm)/60
#:        E_ph,rms  = E_ph,peak / sqrt(2) = Ke * N_krpm
#:   =>   lambda_pm = sqrt(2)*Ke*60 / (2*pi*1000*p)
_KE_PHASE_TO_LAMBDA: float = 60.0 * SQRT2 / (TWO_PI * 1000.0)  # = 0.0135044

#: 역기전력 상수의 전압 기준. 데이터시트마다 다르므로 **반드시 확인**할 것.
KE_REFERENCES: tuple[str, ...] = ("LINE_TO_LINE", "PHASE")


def _ke_divisor(reference: str) -> float:
    ref = reference.upper()
    if ref == "LINE_TO_LINE":
        return SQRT3
    if ref == "PHASE":
        return 1.0
    raise ValueError(f"ke_reference 는 LINE_TO_LINE | PHASE 중 하나: {reference!r}")


def lambda_pm_from_ke(
    ke_vrms_per_krpm: float, pole_pairs: int, reference: str = "LINE_TO_LINE"
) -> float:
    """역기전력 상수 Ke [Vrms/krpm] -> 영구자석 쇄교자속 lambda_pm [Wb, 상 peak].

    `reference`:
      "LINE_TO_LINE" — 선간 실효값 기준 (PMSM 데이터시트의 지배적 관행, **기본값**)
      "PHASE"        — 상 실효값 기준

    두 기준은 sqrt(3) = 1.732 배 차이가 나므로 혼동하면 토크가 73 % 틀어진다.

    검산 예 (research/manual §3.3): 6극(p=3) 모터를 1000 rpm 으로 돌려
    선간 실효 48.0 V 측정 -> Ke = 48.0 Vrms/krpm -> lambda_pm = 0.1248 Wb
    """
    if pole_pairs < 1:
        raise ValueError(f"pole_pairs 는 1 이상이어야 합니다: {pole_pairs}")
    if ke_vrms_per_krpm < 0.0:
        raise ValueError(f"Ke 는 음수일 수 없습니다: {ke_vrms_per_krpm}")
    return ke_vrms_per_krpm * _KE_PHASE_TO_LAMBDA / (_ke_divisor(reference) * pole_pairs)


def ke_from_lambda_pm(
    lambda_pm: float, pole_pairs: int, reference: str = "LINE_TO_LINE"
) -> float:
    """lambda_pm [Wb] -> 역기전력 상수 Ke [Vrms/krpm]. `lambda_pm_from_ke` 의 역함수."""
    if pole_pairs < 1:
        raise ValueError(f"pole_pairs 는 1 이상이어야 합니다: {pole_pairs}")
    return lambda_pm * _ke_divisor(reference) * pole_pairs / _KE_PHASE_TO_LAMBDA


def back_emf_line_to_line_rms(lambda_pm: float, pole_pairs: int, rpm: float) -> float:
    """주어진 회전수에서의 무부하 선간 역기전력 실효값 [V] — 실측 대조용."""
    omega_e = rpm_to_rad_s(rpm) * pole_pairs
    return omega_e * lambda_pm * SQRT3 / SQRT2


# --- 회전/전기 각속도 ------------------------------------------------------
def mech_to_elec_speed(omega_m: float, pole_pairs: int) -> float:
    """기계 각속도 -> 전기 각속도. `pole_pairs` 는 극쌍수(극수 아님)."""
    return omega_m * pole_pairs


def elec_to_mech_speed(omega_e: float, pole_pairs: int) -> float:
    return omega_e / pole_pairs


# --- 인버터 전압 한계 (research.md §2.5) -----------------------------------
def v_max_from_vdc(v_dc: float, modulation: str = "SVPWM", k_margin: float = 0.95) -> float:
    """직류단 전압 -> 상전압 peak 한계.

    SVPWM 선형 영역: V_dc / sqrt(3)
    SPWM  선형 영역: V_dc / 2
    데드타임/소자 강하 마진 `k_margin` 을 곱한다.
    """
    mod = modulation.upper()
    if mod == "SVPWM":
        base = v_dc / SQRT3
    elif mod == "SPWM":
        base = v_dc / 2.0
    else:
        raise ValueError(f"지원하지 않는 변조 방식: {modulation!r} (SVPWM | SPWM)")
    if not 0.0 < k_margin <= 1.0:
        raise ValueError(f"k_margin 은 (0, 1] 이어야 함: {k_margin}")
    return k_margin * base


__all__ = [
    "DQ_CONVENTION",
    "KE_REFERENCES",
    "SQRT2",
    "SQRT3",
    "T0_KELVIN",
    "TORQUE_DQ_COEFF",
    "TWO_PI",
    "back_emf_line_to_line_rms",
    "bar_to_pa",
    "cc_per_rev_to_m3_per_rev",
    "celsius_to_kelvin",
    "elec_to_mech_speed",
    "h_to_mh",
    "j_per_kg_to_kj_per_kg",
    "ke_from_lambda_pm",
    "kelvin_to_celsius",
    "kg_h_to_kg_s",
    "kg_s_to_kg_h",
    "kj_per_kg_to_j_per_kg",
    "kpa_to_pa",
    "lambda_pm_from_ke",
    "line_to_line_from_phase",
    "m3_per_rev_to_cc_per_rev",
    "mech_to_elec_speed",
    "mh_to_h",
    "pa_to_bar",
    "pa_to_kpa",
    "peak_from_rms",
    "phase_from_line_to_line",
    "phase_inductance_from_line_to_line",
    "phase_resistance_from_line_to_line",
    "rad_s_to_rpm",
    "rev_s_to_rpm",
    "rms_from_peak",
    "rpm_to_rad_s",
    "rpm_to_rev_s",
    "v_max_from_vdc",
]
