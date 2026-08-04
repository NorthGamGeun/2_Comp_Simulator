"""표시용 데이터 조립 — PyQt 무의존 (plan.md §6).

위젯은 여기서 만든 순수 데이터만 그린다. 덕분에 시각화 로직 전체를
UI 없이 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import MotorSpec, Verdict
from ..motor.control import id_on_constant_torque, mtpa_id
from ..motor.limits import (
    current_circle,
    mtpa_curve,
    voltage_boundary_exact,
    voltage_ellipse,
)
from ..units import ke_from_lambda_pm, kelvin_to_celsius, kg_s_to_kg_h, rev_s_to_rpm

# 색상 규약 (plan.md §6.1)
COLOR_CURRENT = "#2f6fd0"  # 파랑 — 전류 한계 원
COLOR_VOLTAGE = "#2e9e5b"  # 초록 — 전압 한계 타원
COLOR_VOLTAGE_EXACT = "#7fc8a0"  # 연초록 — Rs 포함 정확 경계
COLOR_TORQUE = "#e08a2e"  # 주황 — 상수 토크 곡선
COLOR_MTPA = "#9aa0a6"  # 회색 — MTPA 참조선
COLOR_OK = "#1e8e3e"
COLOR_NG = "#d93025"
COLOR_WARN = "#e37400"


@dataclass(frozen=True)
class Curve:
    x: list[float]
    y: list[float]
    name: str
    color: str
    dashed: bool = False
    width: float = 2.0


@dataclass(frozen=True)
class Marker:
    x: float
    y: float
    name: str
    color: str
    symbol: str = "o"
    size: float = 14.0


@dataclass(frozen=True)
class DqPlotData:
    curves: list[Curve] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    x_range: tuple[float, float] = (-1.0, 1.0)
    y_range: tuple[float, float] = (-1.0, 1.0)
    title: str = ""


@dataclass(frozen=True)
class ResultRow:
    label: str
    value: str
    emphasis: bool = False


@dataclass(frozen=True)
class ResultPanelData:
    badge_text: str
    badge_color: str
    violations: list[tuple[str, str, str]]  # (severity, code, message)
    rows: list[ResultRow]


# ===========================================================================
# dq 평면
# ===========================================================================
def build_dq_plot(verdict: Verdict, motor: MotorSpec) -> DqPlotData:
    curves: list[Curve] = []
    markers: list[Marker] = []

    xs, ys = current_circle(motor.i_max)
    curves.append(Curve(xs, ys, f"전류 한계 원 ({motor.i_max:.1f} A)", COLOR_CURRENT))

    omega_e = verdict.load.omega_e if verdict.load is not None else 0.0

    if omega_e > 0.0:
        xs, ys = voltage_ellipse(motor, omega_e)
        if xs:
            curves.append(
                Curve(xs, ys, f"전압 한계 타원 (Rs 무시, {motor.v_max:.0f} V)", COLOR_VOLTAGE)
            )
        xs, ys = voltage_boundary_exact(motor, omega_e)
        if xs:
            curves.append(
                Curve(xs, ys, "전압 한계 (Rs 포함 · 판정 기준)", COLOR_VOLTAGE_EXACT, dashed=True)
            )

    xs, ys = mtpa_curve(motor, motor.i_max)
    curves.append(Curve(xs, ys, "MTPA 궤적", COLOR_MTPA, dashed=True, width=1.5))

    if verdict.load is not None and not motor.is_spmsm:
        T = verdict.load.T_load
        iq_hi = motor.i_max * 1.4
        iq_lo = max(T / (1.5 * motor.p * motor.lambda_pm) * 0.05, 1e-4)
        pts_x, pts_y = [], []
        n = 400
        for k in range(n):
            iq = iq_lo + (iq_hi - iq_lo) * k / (n - 1)
            i_d = id_on_constant_torque(motor, T, iq)
            if i_d is None or i_d < -3.0 * motor.i_max or i_d > 3.0 * motor.i_max:
                continue
            pts_x.append(i_d)
            pts_y.append(iq)
        if pts_x:
            curves.append(
                Curve(pts_x, pts_y, f"상수 토크 곡선 ({T:.2f} N·m)", COLOR_TORQUE)
            )

    markers.append(
        Marker(-motor.i_characteristic, 0.0, "특성 전류점", COLOR_MTPA, symbol="x", size=12.0)
    )

    if verdict.op is not None:
        markers.append(
            Marker(
                verdict.op.i_d,
                verdict.op.i_q,
                f"운전점 ({verdict.op.mode})",
                COLOR_OK if verdict.is_feasible else COLOR_NG,
                size=16.0,
            )
        )
        # MTPA 참조점 (약계자 시 얼마나 벗어났는지 보여준다)
        if verdict.op.mode == "FLUX_WEAKENING":
            markers.append(
                Marker(
                    mtpa_id(motor, verdict.op.i_q),
                    verdict.op.i_q,
                    "동일 i_q 의 MTPA 점",
                    COLOR_MTPA,
                    symbol="t",
                    size=10.0,
                )
            )

    span = max(motor.i_max * 1.35, motor.i_characteristic * 1.25)
    title = "d–q 전류 평면"
    if verdict.load is not None:
        title += f"  ({rev_s_to_rpm(verdict.load.N):.0f} rpm, ω_e={verdict.load.omega_e:.0f} rad/s)"

    return DqPlotData(
        curves=curves,
        markers=markers,
        x_range=(-span, span * 0.45),
        y_range=(-span * 0.12, span),
        title=title,
    )


# ===========================================================================
# 결과 패널
# ===========================================================================
_BADGE = {
    "FEASIBLE": ("동작 가능 (FEASIBLE)", COLOR_OK),
    "FEASIBLE_WITH_WARNING": ("동작 가능 — 경고 있음", COLOR_WARN),
    "INFEASIBLE": ("동작 불가 (INFEASIBLE)", COLOR_NG),
}


def build_result_panel(verdict: Verdict, motor: MotorSpec | None = None) -> ResultPanelData:
    badge_text, badge_color = _BADGE[verdict.status]

    violations = [
        (v.severity, v.code.value, v.message_ko)
        for v in sorted(verdict.violations, key=lambda x: 0 if x.severity == "FAIL" else 1)
    ]

    rows: list[ResultRow] = []
    lp = verdict.load
    if lp is not None:
        rows += [
            ResultRow("물성 백엔드", lp.backend_name),
            ResultRow("회전수", f"{rev_s_to_rpm(lp.N):.0f} rpm"),
            ResultRow("압력비", f"{lp.PR:.3f}"),
            ResultRow("질량 유량", f"{kg_s_to_kg_h(lp.m_dot):.1f} kg/h"),
            ResultRow("등엔트로피 일", f"{lp.w_isen/1e3:.2f} kJ/kg"),
            ResultRow("흡입 온도", f"{kelvin_to_celsius(lp.T_suc):.1f} °C"),
            ResultRow("토출 온도", f"{kelvin_to_celsius(lp.T_dis):.1f} °C", emphasis=True),
            ResultRow(
                "효율 (등엔트로피/체적/기계)",
                f"{lp.eta_isen:.3f} / {lp.eta_vol:.3f} / {lp.eta_mech:.3f}",
            ),
            ResultRow("샤프트 동력", f"{lp.P_shaft:.0f} W"),
            ResultRow("부하 토크", f"{lp.T_load:.3f} N·m", emphasis=True),
        ]
    if verdict.T_max_avail is not None:
        rows.append(ResultRow("최대 가용 토크", f"{verdict.T_max_avail:.3f} N·m", emphasis=True))
    if verdict.torque_margin is not None:
        rows.append(
            ResultRow("토크 여유율", f"{verdict.torque_margin*100:.1f} %", emphasis=True)
        )
    elif verdict.T_max_avail is not None and verdict.T_max_avail <= 0.0:
        rows.append(ResultRow("토크 여유율", "해당 없음 (가용 토크 0)", emphasis=True))

    op = verdict.op
    if op is not None:
        rows += [
            ResultRow("제어 모드", op.mode),
            ResultRow("전류 (i_d, i_q)", f"({op.i_d:.2f}, {op.i_q:.2f}) A"),
            ResultRow("전류 크기 |i|", f"{op.i_mag:.2f} A"),
            ResultRow("전압 (v_d, v_q)", f"({op.v_d:.1f}, {op.v_q:.1f}) V"),
            ResultRow("전압 크기 |v|", f"{op.v_mag:.1f} V"),
        ]
    else:
        rows.append(ResultRow("운전점", "해 없음 — 아래 위반 항목 참조", emphasis=True))

    if motor is not None:
        rows += [
            ResultRow(
                "역기전력 상수 Ke",
                f"{ke_from_lambda_pm(motor.lambda_pm, motor.p, 'LINE_TO_LINE'):.3f} Vrms/krpm (선간)",
            ),
            ResultRow("쇄교자속 λpm (환산)", f"{motor.lambda_pm:.5f} Wb"),
            ResultRow("Ld / Lq (상)", f"{motor.Ld*1e3:.3f} / {motor.Lq*1e3:.3f} mH"),
            ResultRow("특성 전류 i_ch", f"{motor.i_characteristic:.2f} A"),
        ]

    return ResultPanelData(badge_text, badge_color, violations, rows)


# ===========================================================================
# 속도 스윕 뷰 (plan.md §6.3)
# ===========================================================================
@dataclass(frozen=True)
class SweepPlotData:
    rpm: list[float]
    T_load: list[float]
    T_max: list[float]
    boundary_rpm: float | None
    title: str = "속도 스윕 — 가동 가능 범위"


def build_sweep_plot(points, boundary_rpm: float | None = None) -> SweepPlotData:
    return SweepPlotData(
        rpm=[p.rpm for p in points],
        T_load=[p.T_load for p in points],
        T_max=[p.T_max_avail for p in points],
        boundary_rpm=boundary_rpm if (boundary_rpm or 0.0) > 0.0 else None,
    )


__all__ = [
    "COLOR_CURRENT",
    "COLOR_MTPA",
    "COLOR_NG",
    "COLOR_OK",
    "COLOR_TORQUE",
    "COLOR_VOLTAGE",
    "COLOR_WARN",
    "Curve",
    "DqPlotData",
    "Marker",
    "ResultPanelData",
    "ResultRow",
    "SweepPlotData",
    "build_dq_plot",
    "build_result_panel",
    "build_sweep_plot",
]
