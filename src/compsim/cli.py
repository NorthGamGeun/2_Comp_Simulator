"""CLI 엔트리 — UI 없이 전체 파이프라인 실행 (plan.md Phase 3.4).

사용:
    python -m compsim.cli --demo
    python -m compsim.cli --config case.json
    python -m compsim.cli --demo --sweep 600:12000:400
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .feasibility.evaluator import EvaluationRequest, evaluate, max_feasible_speed, sweep_speed
from .models import (
    REFRIGERANTS,
    CompressorSpec,
    CycleSpec,
    EfficiencyCoeffs,
    MotorSpec,
    ThermalLimits,
    Verdict,
)
from .thermo.refrigerant import get_backend
from .units import (
    back_emf_line_to_line_rms,
    cc_per_rev_to_m3_per_rev,
    celsius_to_kelvin,
    ke_from_lambda_pm,
    kelvin_to_celsius,
    kg_s_to_kg_h,
    kpa_to_pa,
    lambda_pm_from_ke,
    mh_to_h,
    rev_s_to_rpm,
    rpm_to_rev_s,
)

_PRESETS = {
    "SCROLL_DEFAULT": EfficiencyCoeffs.scroll_default,
    "ROTARY_DEFAULT": EfficiencyCoeffs.rotary_default,
}


def demo_request(refrigerant: str = "R32", rpm: float = 3600.0) -> EvaluationRequest:
    """표준 데모 케이스 — R32, 증발 7 °C / 응축 45 °C, 소형 가정용 6극 IPMSM."""
    be = get_backend(refrigerant)
    cycle = CycleSpec(
        refrigerant=refrigerant,  # type: ignore[arg-type]
        P_evap=be.p_sat(celsius_to_kelvin(7.0)),
        P_cond=be.p_sat(celsius_to_kelvin(45.0)),
        dT_superheat=5.0,
        dT_subcool=5.0,
    )
    comp = CompressorSpec(
        V_disp=cc_per_rev_to_m3_per_rev(20.0),
        eff=EfficiencyCoeffs.scroll_default(),
        drive_mode="SPEED_DRIVEN",
        N=rpm_to_rev_s(rpm),
    )
    motor = MotorSpec(
        Ld=mh_to_h(3.0),
        Lq=mh_to_h(6.0),
        # 역기전력 상수 30.78 Vrms/krpm (선간) == lambda_pm 0.08 Wb @ p=3
        lambda_pm=lambda_pm_from_ke(30.78, 3, "LINE_TO_LINE"),
        p=3,
        Rs=0.5,
        i_max=20.0,
        V_dc=310.0,
    )
    return EvaluationRequest(cycle=cycle, compressor=comp, motor=motor, limits=ThermalLimits())


def request_from_dict(d: dict[str, Any]) -> EvaluationRequest:
    """JSON 설정 -> EvaluationRequest. 입력은 **실용 단위**(kPa, °C, rpm, cc/rev, mH)."""
    c = d["cycle"]
    fluid = c["refrigerant"]
    be = get_backend(fluid, prefer=d.get("prefer_backend", "auto"))

    if "P_evap_kpa" in c:
        p_evap = kpa_to_pa(c["P_evap_kpa"])
    else:
        p_evap = be.p_sat(celsius_to_kelvin(c["T_evap_c"]))
    if "P_cond_kpa" in c:
        p_cond = kpa_to_pa(c["P_cond_kpa"])
    else:
        p_cond = be.p_sat(celsius_to_kelvin(c["T_cond_c"]))

    cycle = CycleSpec(
        refrigerant=fluid,
        P_evap=p_evap,
        P_cond=p_cond,
        dT_superheat=c.get("superheat_k", 5.0),
        dT_subcool=c.get("subcool_k", 5.0),
        dP_suction=kpa_to_pa(c.get("dP_suction_kpa", 0.0)),
        dP_discharge=kpa_to_pa(c.get("dP_discharge_kpa", 0.0)),
    )

    k = d["compressor"]
    preset = k.get("eff_preset", "SCROLL_DEFAULT")
    if preset == "CONSTANT":
        eff = EfficiencyCoeffs.constant(
            k["eta_isen"], k["eta_vol"], k["eta_mech"]
        )
    else:
        eff = _PRESETS[preset]()
    mode = k.get("drive_mode", "SPEED_DRIVEN")
    comp = CompressorSpec(
        V_disp=cc_per_rev_to_m3_per_rev(k["v_disp_cc"]),
        eff=eff,
        drive_mode=mode,
        N=rpm_to_rev_s(k["rpm"]) if mode == "SPEED_DRIVEN" else None,
        m_dot=k["m_dot_kg_h"] / 3600.0 if mode == "FLOW_DRIVEN" else None,
    )

    m = d["motor"]
    p_pairs = int(m["pole_pairs"])

    # 역기전력 상수 Ke 를 우선하고, lambda_pm 직접 입력도 하위호환으로 허용한다.
    if "ke_vrms_krpm" in m:
        lam = lambda_pm_from_ke(
            m["ke_vrms_krpm"], p_pairs, m.get("ke_reference", "LINE_TO_LINE")
        )
    elif "lambda_pm_Wb" in m:
        lam = m["lambda_pm_Wb"]
    else:
        raise KeyError(
            "motor 에 'ke_vrms_krpm'(권장) 또는 'lambda_pm_Wb' 중 하나가 필요합니다."
        )

    motor = MotorSpec(
        Ld=mh_to_h(m["Ld_mH"]),
        Lq=mh_to_h(m["Lq_mH"]),
        lambda_pm=lam,
        p=p_pairs,
        Rs=m["Rs_ohm"],
        i_max=m["i_max_A"],
        V_dc=m["V_dc"],
        k_margin=m.get("k_margin", 0.95),
        modulation=m.get("modulation", "SVPWM"),
    )

    lim = ThermalLimits(**{
        f"T_dis_{k2}": celsius_to_kelvin(v)
        for k2, v in d.get("limits", {}).items()
        if k2 in ("warn", "fail")
    }) if d.get("limits") else ThermalLimits()

    return EvaluationRequest(
        cycle=cycle, compressor=comp, motor=motor, limits=lim,
        prefer_backend=d.get("prefer_backend", "auto"),
    )


def verdict_to_dict(v: Verdict) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": v.status,
        "violations": [
            {
                "code": x.code.value,
                "severity": x.severity,
                "message": x.message_ko,
                "actual": x.actual,
                "limit": x.limit,
            }
            for x in v.violations
        ],
        "T_max_avail_Nm": v.T_max_avail,
        "torque_margin": v.torque_margin,
    }
    if v.load is not None:
        d = asdict(v.load)
        d["T_dis_C"] = kelvin_to_celsius(v.load.T_dis)
        d["T_suc_C"] = kelvin_to_celsius(v.load.T_suc)
        d["m_dot_kg_h"] = kg_s_to_kg_h(v.load.m_dot)
        d["rpm"] = rev_s_to_rpm(v.load.N)
        out["load"] = d
    if v.op is not None:
        out["operating_point"] = asdict(v.op)
    return out


def motor_to_dict(motor: MotorSpec) -> dict[str, Any]:
    return {
        "Ld_mH_phase": motor.Ld * 1e3,
        "Lq_mH_phase": motor.Lq * 1e3,
        "saliency_ratio": motor.Lq / motor.Ld,
        "Rs_ohm_phase": motor.Rs,
        "pole_pairs": motor.p,
        "ke_vrms_krpm_line_to_line": ke_from_lambda_pm(motor.lambda_pm, motor.p, "LINE_TO_LINE"),
        "ke_vrms_krpm_phase": ke_from_lambda_pm(motor.lambda_pm, motor.p, "PHASE"),
        "lambda_pm_Wb": motor.lambda_pm,
        "back_emf_1000rpm_Vrms_ll": back_emf_line_to_line_rms(motor.lambda_pm, motor.p, 1000.0),
        "i_characteristic_A": motor.i_characteristic,
        "i_max_A_peak": motor.i_max,
        "v_max_V_peak": motor.v_max,
    }


def format_verdict(v: Verdict) -> str:
    badge = {
        "FEASIBLE": "[OK]   동작 가능 (FEASIBLE)",
        "FEASIBLE_WITH_WARNING": "[WARN] 동작 가능 - 경고 있음",
        "INFEASIBLE": "[FAIL] 동작 불가 (INFEASIBLE)",
    }[v.status]
    lines = ["=" * 62, badge, "=" * 62]

    if v.violations:
        for x in v.violations:
            mark = "!!" if x.severity == "FAIL" else " *"
            lines.append(f" {mark} {x.code.value}")
            lines.append(f"      {x.message_ko}")
        lines.append("-" * 62)

    if v.load is not None:
        lp = v.load
        lines += [
            f" 냉매 / 백엔드      {lp.backend_name}",
            f" 회전수             {rev_s_to_rpm(lp.N):.0f} rpm",
            f" 압력비             {lp.PR:.3f}",
            f" 질량 유량          {kg_s_to_kg_h(lp.m_dot):.1f} kg/h",
            f" 등엔트로피 일      {lp.w_isen/1e3:.2f} kJ/kg",
            f" 흡입 / 토출 온도   {kelvin_to_celsius(lp.T_suc):.1f} / "
            f"{kelvin_to_celsius(lp.T_dis):.1f} °C",
            f" 효율 (i/v/m)       {lp.eta_isen:.3f} / {lp.eta_vol:.3f} / {lp.eta_mech:.3f}",
            f" 샤프트 동력        {lp.P_shaft:.0f} W",
            f" 부하 토크          {lp.T_load:.3f} N·m",
        ]
    if v.T_max_avail is not None:
        lines.append(f" 최대 가용 토크     {v.T_max_avail:.3f} N·m")
    if v.torque_margin is not None:
        lines.append(f" 토크 여유율        {v.torque_margin*100:.1f} %")
    if v.op is not None:
        o = v.op
        lines += [
            "-" * 62,
            f" 제어 모드          {o.mode}",
            f" 전류 (i_d, i_q)    ({o.i_d:.2f}, {o.i_q:.2f}) A,  |i| = {o.i_mag:.2f} A",
            f" 전압 (v_d, v_q)    ({o.v_d:.1f}, {o.v_q:.1f}) V,  |v| = {o.v_mag:.1f} V",
        ]
    lines.append("=" * 62)
    return "\n".join(lines)


def format_motor(motor: MotorSpec) -> str:
    """모터 파라미터 요약 — 규약 혼동을 눈으로 잡을 수 있게 양쪽 표현을 함께 보여준다."""
    ke_ll = ke_from_lambda_pm(motor.lambda_pm, motor.p, "LINE_TO_LINE")
    ke_ph = ke_from_lambda_pm(motor.lambda_pm, motor.p, "PHASE")
    return "\n".join([
        "-" * 62,
        " [모터 파라미터 확인]",
        f" Ld / Lq (상)       {motor.Ld*1e3:.3f} / {motor.Lq*1e3:.3f} mH"
        f"   (돌극비 {motor.Lq/motor.Ld:.2f})",
        f" Rs (상)            {motor.Rs:.3f} Ω",
        f" 극쌍수 p           {motor.p}  ({2*motor.p}극)",
        f" 역기전력 상수 Ke   {ke_ll:.3f} Vrms/krpm (선간)"
        f" = {ke_ph:.3f} (상)",
        f" 쇄교자속 λpm       {motor.lambda_pm:.5f} Wb  (환산값)",
        f" 1000rpm 선간 역기전력  {back_emf_line_to_line_rms(motor.lambda_pm, motor.p, 1000.0):.2f}"
        f" Vrms   ← 실측 대조용",
        f" 특성 전류 i_ch     {motor.i_characteristic:.2f} A"
        f"   (i_max {motor.i_max:.1f} A → "
        f"{'유한 최대 속도' if motor.i_characteristic > motor.i_max else '무한 속도 가능'})",
        f" 전압 한계 v_max    {motor.v_max:.1f} V (peak, {motor.modulation})",
    ])


def _parse_sweep(spec: str) -> list[float]:
    lo, hi, step = (float(x) for x in spec.split(":"))
    out, x = [], lo
    while x <= hi + 1e-9:
        out.append(x)
        x += step
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="compsim", description="에어컨 압축기 가동 타당성 판별 시뮬레이터"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--config", help="JSON 설정 파일 경로")
    g.add_argument("--demo", action="store_true", help="표준 데모 케이스 실행")
    ap.add_argument("--refrigerant", default="R32", choices=list(REFRIGERANTS))
    ap.add_argument("--rpm", type=float, default=3600.0)
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    ap.add_argument("--sweep", help="속도 스윕 lo:hi:step (rpm)")
    ap.add_argument("--max-speed", action="store_true", help="최대 가동 회전수 탐색")
    ap.add_argument(
        "--show-motor", action="store_true",
        help="모터 파라미터 환산 결과 표시 (Ke↔λpm, 선간↔상 대조)",
    )
    args = ap.parse_args(argv)

    if args.demo:
        req = demo_request(args.refrigerant, args.rpm)
    else:
        with open(args.config, encoding="utf-8") as f:
            req = request_from_dict(json.load(f))

    verdict = evaluate(req)

    if args.json:
        payload = verdict_to_dict(verdict)
        if args.show_motor:
            payload["motor"] = motor_to_dict(req.motor)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_verdict(verdict))
        if args.show_motor:
            print(format_motor(req.motor))

    if args.sweep:
        pts = sweep_speed(req, _parse_sweep(args.sweep))
        print("\n rpm      T_load    T_max     판정")
        print(" " + "-" * 44)
        for p in pts:
            mark = "OK " if p.feasible else "NG "
            print(f" {p.rpm:7.0f}  {p.T_load:7.3f}  {p.T_max_avail:7.3f}   {mark}{p.status}")

    if args.max_speed:
        print(f"\n 최대 가동 회전수: {max_feasible_speed(req):.0f} rpm")

    return 0 if verdict.is_feasible else 2


if __name__ == "__main__":
    sys.exit(main())
