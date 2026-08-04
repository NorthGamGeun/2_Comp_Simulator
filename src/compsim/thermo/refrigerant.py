"""냉매 물성 조회 — 단일 창구 (plan.md §1.2 `refrigerant.py`).

두 개의 백엔드를 둔다.

CoolPropBackend  (**설계 판단용 정식 경로**)
    Helmholtz 자유에너지 상태방정식(HEOS). research.md §1.1 이 요구하는 정확도.
    R32/R290 은 순수물질, R410A 는 CoolProp 내장 의사순수(pseudo-pure) 유체를 사용한다.

    ⚠️ **R290(프로판)은 A3 등급 가연성 냉매**다. 본 도구는 열역학·전자기 타당성만
    판별하며 충전량 제한(IEC 60335-2-40), 누설 감지, 방폭 설계 등 안전 요구사항은
    다루지 않는다. 해당 규격은 별도로 반드시 검토할 것.

ReferenceGasBackend  (**독립 검증 및 CoolProp 부재 환경용**)
    압축인자 Z 로 보정한 이상기체. research.md §6 의 '경로 B' 를 그대로 구현한 것.
    ⚠️ 정확도 약 ±10 % (엔탈피), 토출 온도는 더 큰 오차를 가질 수 있다.
    **절대 설계 판단에 사용하지 말 것.** 다음 두 목적에 한정한다.
      1. CoolProp 결과의 독립 경로 교차 검증 (동어반복 방지)
      2. CoolProp 미설치 환경에서 파이프라인 *구조* 회귀 테스트

두 백엔드는 동일한 인터페이스를 구현하므로 상위 계층은 어느 쪽인지 알 필요가 없다.
다만 `LoadPoint.backend_name` 에 기록되어 Verdict 에 경고로 노출된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..numerics import solve_scalar


class BackendUnavailableError(RuntimeError):
    """요청한 물성 백엔드를 사용할 수 없음."""


@runtime_checkable
class RefrigerantBackend(Protocol):
    """물성 조회 인터페이스. 모든 값은 SI (Pa, K, J/kg, J/kg/K, kg/m^3)."""

    name: str
    fluid: str

    def h_pt(self, P: float, T: float) -> float: ...
    def s_pt(self, P: float, T: float) -> float: ...
    def h_ps(self, P: float, s: float) -> float: ...
    def t_ph(self, P: float, h: float) -> float: ...
    def t_ps(self, P: float, s: float) -> float: ...
    def rho_pt(self, P: float, T: float) -> float: ...
    def t_sat(self, P: float) -> float: ...
    def p_sat(self, T: float) -> float: ...
    def p_crit(self) -> float: ...
    def t_crit(self) -> float: ...
    def phase_pt(self, P: float, T: float) -> str: ...


# ===========================================================================
# CoolProp 백엔드
# ===========================================================================
def coolprop_available() -> bool:
    try:
        import CoolProp  # noqa: F401
    except Exception:
        return False
    return True


class CoolPropBackend:
    """CoolProp HEOS 래퍼. 얇게 유지 — 물리 로직을 여기 두지 않는다."""

    name = "CoolProp"

    #: CoolProp 유체명. R290 은 CoolProp 에서 "R290" 또는 "Propane" 둘 다 인식한다.
    _ALIASES = {"R32": "R32", "R410A": "R410A", "R290": "R290"}

    def __init__(self, fluid: str) -> None:
        if not coolprop_available():
            raise BackendUnavailableError(
                "CoolProp 이 설치되어 있지 않습니다.\n"
                "  pip install CoolProp        (또는)\n"
                "  conda install -c conda-forge coolprop\n"
                "정확도 요구(research.md §1.1)상 설계 판단에는 CoolProp 이 필수입니다."
            )
        if fluid not in self._ALIASES:
            raise ValueError(f"지원하지 않는 냉매: {fluid!r}")
        from CoolProp.CoolProp import PropsSI, PhaseSI  # noqa: N812

        self._props = PropsSI
        self._phase = PhaseSI
        self.fluid = self._ALIASES[fluid]

    def h_pt(self, P: float, T: float) -> float:
        return float(self._props("H", "P", P, "T", T, self.fluid))

    def s_pt(self, P: float, T: float) -> float:
        return float(self._props("S", "P", P, "T", T, self.fluid))

    def h_ps(self, P: float, s: float) -> float:
        return float(self._props("H", "P", P, "S", s, self.fluid))

    def t_ph(self, P: float, h: float) -> float:
        return float(self._props("T", "P", P, "H", h, self.fluid))

    def t_ps(self, P: float, s: float) -> float:
        return float(self._props("T", "P", P, "S", s, self.fluid))

    def rho_pt(self, P: float, T: float) -> float:
        return float(self._props("D", "P", P, "T", T, self.fluid))

    def t_sat(self, P: float) -> float:
        # Q=1 (포화증기). 근공비 혼합물의 글라이드는 <0.2 K 로 무시.
        return float(self._props("T", "P", P, "Q", 1.0, self.fluid))

    def p_sat(self, T: float) -> float:
        return float(self._props("P", "T", T, "Q", 1.0, self.fluid))

    def p_crit(self) -> float:
        return float(self._props("PCRIT", "", 0, "", 0, self.fluid))

    def t_crit(self) -> float:
        return float(self._props("TCRIT", "", 0, "", 0, self.fluid))

    def phase_pt(self, P: float, T: float) -> str:
        try:
            return str(self._phase("P", P, "T", T, self.fluid))
        except Exception:  # pragma: no cover - CoolProp 내부 예외 방어
            return "unknown"


# ===========================================================================
# 참조(근사) 백엔드
# ===========================================================================
@dataclass(frozen=True)
class _FluidConstants:
    """Wagner 포화압력 계수는 문헌 포화표에 최소자승 적합한 값.

    적합 오차 (-40 ~ +60/70 °C 구간):
        R32   최대 0.79 % / RMS 0.36 %
        R410A 최대 0.05 % / RMS 0.03 %
        R290  최대 0.75 % / RMS 0.31 %
    적합 데이터와 잔차는 tests/test_refrigerant.py 가 검증한다.
    """

    M: float  # [kg/mol]
    gamma: float  # 비열비 (이상기체 근사)
    Z: float  # 압축인자 (수축/토출 대표값, 상수 근사)
    Tc: float  # [K]
    Pc: float  # [Pa]
    wagner: tuple[float, float, float, float]

    @property
    def R(self) -> float:
        """비기체상수 [J/(kg*K)]."""
        return 8.31446261815324 / self.M

    @property
    def cp0(self) -> float:
        """이상기체 정압비열 [J/(kg*K)]."""
        return self.gamma * self.R / (self.gamma - 1.0)


_FLUIDS: dict[str, _FluidConstants] = {
    "R32": _FluidConstants(
        M=0.052024,
        gamma=1.29,
        Z=0.88,
        Tc=351.255,
        Pc=5.782e6,
        wagner=(-8.518007, 5.344796, -8.276785, 8.197071),
    ),
    "R410A": _FluidConstants(
        M=0.072585,
        gamma=1.19,
        Z=0.86,
        Tc=344.494,
        Pc=4.9012e6,
        wagner=(-6.971350, -0.121759, 1.112825, -12.977044),
    ),
    # 프로판. 임계점이 높아(96.7 °C) 통상 응축 조건에서 임계점에서 멀고,
    # 비열비가 작아(1.13) 토출 온도가 낮다. 대신 증기 밀도가 낮아 체적 능력이 작다.
    "R290": _FluidConstants(
        M=0.0440956,
        gamma=1.13,
        Z=0.89,
        Tc=369.89,
        Pc=4.2512e6,
        wagner=(-6.724950, 1.375106, -1.084524, -4.219010),
    ),
}


class ReferenceGasBackend:
    """Z 보정 이상기체 (research.md §6 '경로 B').

    모델 정의 — 이 형태를 지키는 것이 중요하다:
        rho(P,T) = P / (Z * R * T)
        s(P,T)   = cp0*ln(T/Tref) - R*ln(P/Pref)      ← Z 없음 (순수 이상기체)
        h(P,T)   = Z * cp0 * (T - Tref)               ← Z 를 엔탈피 편차 인자로 사용

    이 조합에서 등엔트로피 압축은
        T2s = T1 * PR^((gamma-1)/gamma)
        w_isen = Z * cp0 * (T2s - T1)
               = Z * (gamma/(gamma-1)) * R * T1 * [PR^((gamma-1)/gamma) - 1]
    가 되어 research.md §6 의 참조식과 **정확히 일치**한다.
    엔트로피 관계에 Z 를 넣지 않는 이유가 바로 이것이다.
    """

    name = "ReferenceGas(approx)"

    _T_REF = 273.15
    _P_REF = 1.0e5

    def __init__(self, fluid: str) -> None:
        if fluid not in _FLUIDS:
            raise ValueError(f"지원하지 않는 냉매: {fluid!r}")
        self.fluid = fluid
        self.c = _FLUIDS[fluid]

    # --- 상태 관계 ---------------------------------------------------------
    def h_pt(self, P: float, T: float) -> float:  # noqa: ARG002 - 이상기체는 P 무관
        c = self.c
        return c.Z * c.cp0 * (T - self._T_REF)

    def s_pt(self, P: float, T: float) -> float:
        c = self.c
        return c.cp0 * math.log(T / self._T_REF) - c.R * math.log(P / self._P_REF)

    def t_ps(self, P: float, s: float) -> float:
        c = self.c
        return self._T_REF * math.exp((s + c.R * math.log(P / self._P_REF)) / c.cp0)

    def h_ps(self, P: float, s: float) -> float:
        return self.h_pt(P, self.t_ps(P, s))

    def t_ph(self, P: float, h: float) -> float:  # noqa: ARG002
        c = self.c
        return self._T_REF + h / (c.Z * c.cp0)

    def rho_pt(self, P: float, T: float) -> float:
        c = self.c
        return P / (c.Z * c.R * T)

    # --- 포화선 -----------------------------------------------------------
    def p_sat(self, T: float) -> float:
        c = self.c
        if T >= c.Tc:
            return c.Pc
        tau = 1.0 - T / c.Tc
        a, b, d, e = c.wagner
        poly = a * tau + b * tau**1.5 + d * tau**2.5 + e * tau**5
        return c.Pc * math.exp((c.Tc / T) * poly)

    def t_sat(self, P: float) -> float:
        c = self.c
        if P >= c.Pc:
            return c.Tc
        root = solve_scalar(lambda t: self.p_sat(t) - P, 180.0, c.Tc - 1e-6, n_scan=400)
        if root is None:  # pragma: no cover - 물리적으로 도달 불가
            raise BackendUnavailableError(f"t_sat 수렴 실패: P={P}")
        return root

    def p_crit(self) -> float:
        return self.c.Pc

    def t_crit(self) -> float:
        return self.c.Tc

    def phase_pt(self, P: float, T: float) -> str:
        c = self.c
        if P >= c.Pc or T >= c.Tc:
            return "supercritical"
        ts = self.t_sat(P)
        if T > ts + 1e-9:
            return "gas"
        if T < ts - 1e-9:
            return "liquid"
        return "twophase"


# ===========================================================================
# 팩토리
# ===========================================================================
def get_backend(fluid: str, *, prefer: str = "auto") -> RefrigerantBackend:
    """물성 백엔드를 반환한다.

    prefer:
      "auto"      — CoolProp 이 있으면 CoolProp, 없으면 ReferenceGas (경고 목적 기록)
      "coolprop"  — CoolProp 강제. 없으면 BackendUnavailableError
      "reference" — 참조 백엔드 강제 (교차 검증용)
    """
    p = prefer.lower()
    if p == "coolprop":
        return CoolPropBackend(fluid)
    if p == "reference":
        return ReferenceGasBackend(fluid)
    if p != "auto":
        raise ValueError(f"prefer 는 auto|coolprop|reference 중 하나: {prefer!r}")
    if coolprop_available():
        return CoolPropBackend(fluid)
    return ReferenceGasBackend(fluid)


def is_reference_backend(backend: RefrigerantBackend) -> bool:
    return isinstance(backend, ReferenceGasBackend)


__all__ = [
    "BackendUnavailableError",
    "CoolPropBackend",
    "ReferenceGasBackend",
    "RefrigerantBackend",
    "coolprop_available",
    "get_backend",
    "is_reference_backend",
]
