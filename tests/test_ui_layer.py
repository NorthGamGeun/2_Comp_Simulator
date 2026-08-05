"""UI 계층 테스트 — PyQt 없이 실행 가능한 부분 전체 (plan.md Phase 5.1~5.4).

`main_window.py` 는 PyQt 의존이므로 여기서는 (a) 정적 구문 검사와
(b) 참조하는 코어 API 가 실제로 존재하는지만 확인한다.
"""

from __future__ import annotations

import ast
import py_compile
import tempfile
from pathlib import Path

from _helpers import assert_abs

from compsim.feasibility.evaluator import evaluate, sweep_speed
from compsim.models import MotorSpec, ViolationCode
from compsim.ui.adapters import (
    ALL_FIELDS,
    PRESETS,
    InputError,
    UiInputs,
)
from compsim.ui.viewmodel import (
    COLOR_NG,
    COLOR_OK,
    build_dq_plot,
    build_result_panel,
    build_sweep_plot,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "compsim"


# ===========================================================================
# adapters — 단위 변환 및 검증
# ===========================================================================
def test_default_inputs_produce_valid_request():
    """기본값은 스크롤 프리셋(가변 효율)이므로 상수 효율 기준값과 정확히 같지 않다."""
    req = UiInputs(prefer_backend="reference").to_request()
    v = evaluate(req)
    assert v.load is not None
    assert req.compressor.eff.preset_name == "SCROLL_DEFAULT"
    assert_abs(v.load.T_load, 5.235, tol=0.02)
    assert v.op is not None and v.is_feasible


def test_constant_efficiency_path_reproduces_hand_calc():
    """상수 효율로 맞추면 §5.1 손계산값 5.172 N·m 와 일치해야 한다."""
    from dataclasses import replace

    from compsim.models import EfficiencyCoeffs

    req = UiInputs(prefer_backend="reference").to_request()
    req = replace(
        req,
        compressor=replace(req.compressor, eff=EfficiencyCoeffs.constant(0.70, 0.95, 0.95)),
    )
    v = evaluate(req)
    assert_abs(v.load.T_load, 5.172, tol=0.01)


def test_unit_conversion_reaches_si():
    u = UiInputs(prefer_backend="reference", v_disp_cc=20.0, Ld_mH=3.0, freq_hz=180.0)
    req = u.to_request()
    assert_abs(req.compressor.V_disp, 20e-6, tol=1e-12)
    assert_abs(req.motor.Ld, 3e-3, tol=1e-12)
    assert_abs(req.compressor.N, 60.0, tol=1e-12)


def test_rms_checkbox_converts_current_to_peak():
    """research.md §0.2 함정 1 — 가장 흔한 치명적 오류원."""
    peak = UiInputs(prefer_backend="reference", i_max_A=20.0, current_is_rms=False)
    rms = UiInputs(prefer_backend="reference", i_max_A=20.0, current_is_rms=True)
    assert_abs(peak.to_request().motor.i_max, 20.0, tol=1e-9)
    assert_abs(rms.to_request().motor.i_max, 20.0 * 2**0.5, tol=1e-9)


def test_flow_driven_mode_conversion():
    u = UiInputs(prefer_backend="reference", drive_mode="FLOW_DRIVEN", m_dot_kg_h=103.9)
    req = u.to_request()
    assert_abs(req.compressor.m_dot, 103.9 / 3600.0, tol=1e-12)
    assert req.compressor.N is None


def test_invalid_inputs_raise_input_error_with_korean_message():
    cases = [
        UiInputs(T_evap_c=45.0, T_cond_c=7.0),
        UiInputs(v_disp_cc=0.0),
        UiInputs(pole_pairs=0),
        UiInputs(i_max_A=0.0),
        UiInputs(k_margin=1.5),
        UiInputs(drive_mode="SPEED_DRIVEN", freq_hz=0.0),
        UiInputs(drive_mode="FLOW_DRIVEN", m_dot_kg_h=0.0),
    ]
    for u in cases:
        try:
            u.validate()
        except InputError as e:
            assert str(e), "오류 메시지가 비어 있으면 안 된다"
        else:
            raise AssertionError(f"거부되어야 함: {u}")


def test_warnings_are_non_fatal():
    u = UiInputs(prefer_backend="reference", Ld_mH=6.0, Lq_mH=3.0)
    warns = u.validate()
    assert any("Lq" in w for w in warns)
    u.to_request()  # 예외 없이 통과해야 한다


def test_gate_ordering_warning_surfaces_in_ui():
    u = UiInputs(prefer_backend="reference", dT_magnet_offset_k=+10.0)
    warns = u.validate()
    assert any("게이트" in w for w in warns), warns


def test_all_presets_are_constructible():
    for name in PRESETS:
        u = UiInputs(prefer_backend="reference", eff_preset=name)
        assert evaluate(u.to_request()).load is not None


def test_field_specs_cover_every_numeric_attribute():
    """위젯 메타데이터가 실제 속성과 어긋나면 UI 가 조용히 값을 무시한다."""
    u = UiInputs()
    for spec in ALL_FIELDS:
        assert hasattr(u, spec.attr), f"UiInputs 에 {spec.attr} 없음"
        assert spec.minimum < spec.maximum
        assert spec.label and spec.unit


def test_field_defaults_are_within_declared_range():
    u = UiInputs()
    for spec in ALL_FIELDS:
        val = float(getattr(u, spec.attr))
        assert spec.minimum <= val <= spec.maximum, (
            f"{spec.attr} 기본값 {val} 이 범위 [{spec.minimum}, {spec.maximum}] 밖"
        )


# ===========================================================================
# viewmodel — dq 플롯
# ===========================================================================
def _verdict(rpm: float = 3600.0):
    # rpm -> freq_hz: freq_hz = rpm * pole_pairs / 60, default pole_pairs=3
    freq = rpm * 3 / 60.0
    u = UiInputs(prefer_backend="reference", freq_hz=freq)
    req = u.to_request()
    return evaluate(req), req.motor


def test_dq_plot_contains_required_curves():
    """plan.md §6.1 필수 요소 — 전류 원, 전압 타원, 상수 토크 곡선, MTPA."""
    v, m = _verdict()
    d = build_dq_plot(v, m)
    names = " ".join(c.name for c in d.curves)
    for token in ("전류 한계 원", "전압 한계 타원", "상수 토크 곡선", "MTPA"):
        assert token in names, f"'{token}' 곡선이 없음: {names}"


def test_dq_plot_marks_operating_point_green_when_feasible():
    v, m = _verdict(3600.0)
    d = build_dq_plot(v, m)
    op_markers = [x for x in d.markers if "운전점" in x.name]
    assert len(op_markers) == 1
    assert op_markers[0].color == COLOR_OK
    assert_abs(op_markers[0].x, v.op.i_d, tol=1e-12)


def test_dq_plot_has_no_operating_marker_when_infeasible():
    v, m = _verdict(36000.0)
    assert not v.is_feasible
    d = build_dq_plot(v, m)
    assert [x for x in d.markers if "운전점" in x.name] == []


def test_dq_plot_curves_lie_where_expected():
    v, m = _verdict()
    d = build_dq_plot(v, m)
    circle = next(c for c in d.curves if "전류 한계 원" in c.name)
    for x, y in zip(circle.x, circle.y):
        assert_abs((x * x + y * y) ** 0.5, m.i_max, tol=1e-9)


def test_dq_plot_marks_characteristic_current():
    v, m = _verdict()
    d = build_dq_plot(v, m)
    ch = next(x for x in d.markers if "특성 전류" in x.name)
    assert_abs(ch.x, -m.i_characteristic, tol=1e-12)


def test_dq_plot_range_includes_all_geometry():
    v, m = _verdict()
    d = build_dq_plot(v, m)
    assert d.x_range[0] <= -m.i_max
    assert d.y_range[1] >= m.i_max
    assert d.x_range[0] < d.x_range[1] and d.y_range[0] < d.y_range[1]


def test_dq_plot_shows_mtpa_reference_marker_in_flux_weakening():
    v, m = _verdict(7200.0)
    assert v.op is not None and v.op.mode == "FLUX_WEAKENING"
    d = build_dq_plot(v, m)
    assert any("MTPA" in x.name for x in d.markers)


def test_dq_plot_handles_spmsm_without_constant_torque_curve():
    u = UiInputs(prefer_backend="reference", Ld_mH=4.5, Lq_mH=4.5)
    req = u.to_request()
    v = evaluate(req)
    d = build_dq_plot(v, req.motor)
    assert isinstance(req.motor, MotorSpec) and req.motor.is_spmsm
    assert any("전류 한계 원" in c.name for c in d.curves)


# ===========================================================================
# viewmodel — 결과 패널
# ===========================================================================
def test_result_panel_badge_colors():
    v_ok, _ = _verdict(3600.0)
    v_ng, _ = _verdict(36000.0)
    assert build_result_panel(v_ng).badge_color == COLOR_NG
    assert "동작 불가" in build_result_panel(v_ng).badge_text
    assert build_result_panel(v_ok).badge_color != COLOR_NG


def test_result_panel_lists_failures_first():
    """plan.md §6.2 — FAIL 이 상단에 와야 원인이 먼저 보인다."""
    v, _ = _verdict(36000.0)
    p = build_result_panel(v)
    sevs = [s for s, _, _ in p.violations]
    assert "FAIL" in sevs
    assert sevs.index("FAIL") == 0
    if "WARN" in sevs:
        assert sevs.index("FAIL") < sevs.index("WARN")


def test_result_panel_shows_voltage_limit_cause():
    v, _ = _verdict(36000.0)
    p = build_result_panel(v)
    codes = " ".join(c for _, c, _ in p.violations)
    assert (
        ViolationCode.VOLTAGE_LIMIT.value in codes
        or ViolationCode.BOTH_LIMIT.value in codes
    )


def test_result_panel_rows_include_key_metrics():
    v, _ = _verdict()
    labels = [r.label for r in build_result_panel(v).rows]
    for token in ("부하 토크", "최대 가용 토크", "토출 온도", "압력비", "제어 모드"):
        assert token in labels, f"'{token}' 행이 없음: {labels}"


def test_result_panel_handles_zero_available_torque():
    v, _ = _verdict(36000.0)
    p = build_result_panel(v)
    rows = {r.label: r.value for r in p.rows}
    assert rows["토크 여유율"] == "해당 없음 (가용 토크 0)"
    assert "해 없음" in rows["운전점"]


def test_result_panel_handles_early_return_verdict():
    from compsim.feasibility.evaluator import EvaluationRequest
    from compsim.models import CycleSpec

    u = UiInputs(prefer_backend="reference")
    req = u.to_request()
    bad = EvaluationRequest(
        cycle=CycleSpec(refrigerant="R32", P_evap=2.0e6, P_cond=1.0e6),
        compressor=req.compressor, motor=req.motor, prefer_backend="reference",
    )
    p = build_result_panel(evaluate(bad))
    assert p.badge_color == COLOR_NG
    assert p.rows, "load 가 없어도 최소한의 행은 있어야 한다"


# ===========================================================================
# viewmodel — 속도 스윕
# ===========================================================================
def test_sweep_plot_data_shapes_match():
    u = UiInputs(prefer_backend="reference")
    req = u.to_request()
    pts = sweep_speed(req, [1000.0, 3000.0, 6000.0, 9000.0, 12000.0], n_scan=100)
    d = build_sweep_plot(pts, boundary_rpm=8500.0)
    assert len(d.rpm) == len(d.T_load) == len(d.T_max) == 5
    assert d.boundary_rpm == 8500.0
    assert all(b <= a + 1e-6 for a, b in zip(d.T_max, d.T_max[1:])), "가용 토크는 비증가"


def test_sweep_plot_boundary_none_when_zero():
    assert build_sweep_plot([], boundary_rpm=0.0).boundary_rpm is None
    assert build_sweep_plot([], boundary_rpm=None).boundary_rpm is None


# ===========================================================================
# main_window — 정적 검사 (PyQt 미설치 환경 대응)
# ===========================================================================
def test_main_window_compiles():
    p = SRC / "ui" / "main_window.py"
    with tempfile.TemporaryDirectory() as td:
        py_compile.compile(str(p), cfile=str(Path(td) / "mw.pyc"), doraise=True)


def _imported_modules(path: Path) -> set[str]:
    """실제 import 문만 AST 로 추출한다 (주석/문자열의 언급은 무시)."""
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    return mods


def test_only_main_window_imports_pyqt():
    """코어가 PyQt 를 import 하면 CLI/테스트가 UI 의존성에 묶인다.

    문자열 매칭이 아니라 AST 로 판정한다 — 초기 구현은 "PyQt 를 import 하지
    않는다" 는 주석까지 위반으로 잡는 오탐이 있었다.
    """
    allowed = {"main_window.py", "__main__.py"}
    offenders = []
    for f in SRC.rglob("*.py"):
        if f.parent.name == "ui" and f.name in allowed:
            continue
        mods = _imported_modules(f)
        if mods & {"PyQt6", "PyQt5", "pyqtgraph", "PySide6"}:
            offenders.append(str(f.relative_to(SRC)))
    assert offenders == [], f"코어/뷰모델이 PyQt 를 import 함: {offenders}"


def test_main_window_actually_imports_pyqt():
    """반대 방향 확인 — 위 테스트가 공허하게 통과하지 않도록."""
    mods = _imported_modules(SRC / "ui" / "main_window.py")
    assert mods & {"PyQt6", "pyqtgraph"}, "main_window 는 PyQt 를 import 해야 한다"


def test_core_modules_import_without_pyqt_installed():
    """PyQt 미설치 환경에서도 코어와 뷰모델이 import 되어야 한다."""
    import importlib

    for name in (
        "compsim.cli",
        "compsim.ui.adapters",
        "compsim.ui.viewmodel",
        "compsim.feasibility.evaluator",
    ):
        assert importlib.import_module(name) is not None


def test_main_window_references_only_existing_core_apis():
    """UI 가 호출하는 이름이 실제로 존재하는지 정적으로 확인한다."""
    import compsim.feasibility.evaluator as ev
    import compsim.ui.adapters as ad
    import compsim.ui.viewmodel as vm

    src = (SRC / "ui" / "main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                imported.append((node.module, a.name))

    modmap = {
        "..feasibility.evaluator": ev,
        ".adapters": ad,
        ".viewmodel": vm,
    }
    for mod, name in imported:
        if mod in modmap:
            assert hasattr(modmap[mod], name), f"{mod} 에 {name} 이 없음 (UI 가 참조 중)"


def test_ui_package_exports_pyqt_free_api():
    import compsim.ui as ui

    for name in ("UiInputs", "InputError", "build_dq_plot", "build_result_panel"):
        assert hasattr(ui, name)
