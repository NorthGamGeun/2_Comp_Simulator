"""PyQtGraph UI — 메인 윈도우 (plan.md Phase 5).

이 모듈만이 PyQt6 / pyqtgraph 에 의존한다. 계산·표시 로직은 전부
`adapters.py` 와 `viewmodel.py` 에 있으며 UI 없이 테스트된다.

실행:
    python -m compsim.ui.main_window
    (또는)  python -m compsim.ui
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import pyqtgraph as pg
    from PyQt6 import QtCore, QtGui, QtWidgets
except ImportError as e:  # pragma: no cover - UI 의존성 부재 환경
    raise SystemExit(
        "UI 실행에는 PyQt6 와 pyqtgraph 가 필요합니다.\n"
        '  pip install "compsim[ui]"   또는   pip install PyQt6 pyqtgraph\n'
        f"(원인: {e})"
    ) from e

from ..feasibility.evaluator import evaluate, max_feasible_speed, sweep_speed
from ..models import REFRIGERANTS
from .adapters import (
    ALL_FIELDS,
    COMPRESSOR_FIELDS,
    CYCLE_FIELDS,
    LIMIT_FIELDS,
    MOTOR_FIELDS,
    PRESETS,
    InputError,
    UiInputs,
)
from .viewmodel import (
    COLOR_NG,
    COLOR_OK,
    COLOR_TORQUE,
    build_dq_plot,
    build_result_panel,
    build_sweep_plot,
)

pg.setConfigOptions(antialias=True, background="w", foreground="#202124")


def _pen(color: str, width: float = 2.0, dashed: bool = False):
    style = QtCore.Qt.PenStyle.DashLine if dashed else QtCore.Qt.PenStyle.SolidLine
    return pg.mkPen(color=color, width=width, style=style)


# ===========================================================================
# 입력 패널
# ===========================================================================
class InputPanel(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.inputs = UiInputs()
        self._spins: dict[str, QtWidgets.QDoubleSpinBox] = {}

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        # --- 냉매 / 모드 콤보 ---
        top = QtWidgets.QGroupBox("냉매 · 모드")
        form = QtWidgets.QFormLayout(top)

        self.cb_ref = QtWidgets.QComboBox()
        self.cb_ref.addItems(list(REFRIGERANTS))
        self.cb_ref.setToolTip(
            "R32   — 비열비가 커 토출 온도가 높음 (열적 게이트 주의)\n"
            "R410A — 의사순수 혼합물, 중간 특성\n"
            "R290  — 프로판. 토출 온도가 낮으나 증기 밀도가 낮아 체적 능력이 작음.\n"
            "        ⚠️ A3 등급 가연성 — 충전량 제한 등 안전 규격 별도 검토 필요"
        )
        form.addRow("냉매", self.cb_ref)

        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItems(["SPEED_DRIVEN (회전수 입력)", "FLOW_DRIVEN (유량 입력)"])
        form.addRow("구동 모드", self.cb_mode)

        self.cb_preset = QtWidgets.QComboBox()
        self.cb_preset.addItems(list(PRESETS))
        form.addRow("압축기 프리셋", self.cb_preset)

        self.cb_mod = QtWidgets.QComboBox()
        self.cb_mod.addItems(["SVPWM", "SPWM"])
        form.addRow("변조 방식", self.cb_mod)

        self.cb_ke_ref = QtWidgets.QComboBox()
        self.cb_ke_ref.addItems(["선간 (line-to-line)", "상 (phase)"])
        self.cb_ke_ref.setToolTip(
            "역기전력 상수 Ke 의 전압 기준입니다.\n"
            "선간과 상은 √3 = 1.732 배 차이가 나므로 반드시 데이터시트를 확인하십시오.\n"
            "PMSM 데이터시트는 대부분 선간(line-to-line) 실효값 기준입니다."
        )
        form.addRow("Ke 기준", self.cb_ke_ref)

        self.chk_rms = QtWidgets.QCheckBox("전류 정격이 RMS 기준")
        self.chk_rms.setToolTip(
            "체크하면 입력 전류에 √2 를 곱해 peak 로 변환합니다.\n"
            "내부 연산은 amplitude-invariant(peak) 규약을 사용합니다."
        )
        form.addRow("", self.chk_rms)
        outer.addWidget(top)

        # --- 수치 입력 그룹들 ---
        for title, specs in (
            ("냉매 · 사이클", CYCLE_FIELDS),
            ("압축기", COMPRESSOR_FIELDS),
            ("모터 (PMSM)", MOTOR_FIELDS),
            ("열적 한계", LIMIT_FIELDS),
        ):
            box = QtWidgets.QGroupBox(title)
            f = QtWidgets.QFormLayout(box)
            for spec in specs:
                sb = QtWidgets.QDoubleSpinBox()
                sb.setDecimals(spec.decimals)
                sb.setRange(spec.minimum, spec.maximum)
                sb.setSingleStep(spec.step)
                sb.setValue(float(getattr(self.inputs, spec.attr)))
                sb.setSuffix(f"  {spec.unit}" if spec.unit != "-" else "")
                if spec.extra.get("tip"):
                    sb.setToolTip(spec.extra["tip"])
                sb.valueChanged.connect(self._on_change)
                self._spins[spec.attr] = sb
                f.addRow(spec.label, sb)

            # 모터 그룹에는 Ke -> lambda_pm 환산 결과를 실시간으로 보여준다.
            # 규약 혼동은 화면에서 즉시 드러나야 잡힌다.
            if title == "모터 (PMSM)":
                self.lbl_derived = QtWidgets.QLabel("—")
                self.lbl_derived.setWordWrap(True)
                self.lbl_derived.setStyleSheet(
                    "color:#3c4043; background:#f1f3f4; border-radius:4px; padding:6px;"
                )
                f.addRow("환산 결과", self.lbl_derived)

                # 설정 저장/불러오기 버튼
                btn_row = QtWidgets.QHBoxLayout()
                self.btn_motor_save = QtWidgets.QPushButton("설정 저장")
                self.btn_motor_load = QtWidgets.QPushButton("설정 불러오기")
                self.btn_motor_save.setToolTip("냉매·사이클, 압축기, 모터 파라미터를 JSON 파일로 저장합니다.")
                self.btn_motor_load.setToolTip("저장된 설정 파일을 불러옵니다.")
                btn_row.addWidget(self.btn_motor_save)
                btn_row.addWidget(self.btn_motor_load)
                f.addRow(btn_row)
                self.btn_motor_save.clicked.connect(self._save_motor)
                self.btn_motor_load.clicked.connect(self._load_motor)
            outer.addWidget(box)

        self.btn = QtWidgets.QPushButton("재계산 (Ctrl+R)")
        self.btn.clicked.connect(self._on_change)
        outer.addWidget(self.btn)
        outer.addStretch(1)

        for cb in (self.cb_ref, self.cb_mode, self.cb_preset, self.cb_mod, self.cb_ke_ref):
            cb.currentIndexChanged.connect(self._on_change)
        self.chk_rms.stateChanged.connect(self._on_change)
        self._sync_mode()
        self._update_derived()

    def _sync_mode(self) -> None:
        speed = self.cb_mode.currentIndex() == 0
        self._spins["freq_hz"].setEnabled(speed)
        self._spins["m_dot_kg_h"].setEnabled(not speed)

    def _update_derived(self) -> None:
        """Ke -> λpm 환산 결과와 실측 대조용 역기전력을 표시한다."""
        if not hasattr(self, "lbl_derived"):  # 위젯 구성 도중 호출 방어
            return
        u = self.collect()
        try:
            lam = u.lambda_pm_Wb
            e1000 = u.back_emf_at(1000.0)
        except (ValueError, InputError):
            self.lbl_derived.setText("—")
            return
        i_ch = lam / (u.Ld_mH * 1e-3) if u.Ld_mH > 0 else float("inf")
        rpm_val = u.rpm
        self.lbl_derived.setText(
            f"λpm = <b>{lam:.5f} Wb</b> (상 peak)<br>"
            f"1000 rpm 무부하 선간 역기전력 = <b>{e1000:.2f} Vrms</b><br>"
            f"특성 전류 i_ch = λpm/Ld = <b>{i_ch:.2f} A</b><br>"
            f"기계 회전수 = <b>{rpm_val:.0f} rpm</b> ({u.freq_hz:.1f} Hz × 60 / {u.pole_pairs})"
        )

    def _on_change(self) -> None:
        self._sync_mode()
        self._update_derived()
        self.changed.emit()

    def collect(self) -> UiInputs:
        u = self.inputs
        u.refrigerant = self.cb_ref.currentText()
        u.drive_mode = "SPEED_DRIVEN" if self.cb_mode.currentIndex() == 0 else "FLOW_DRIVEN"
        u.eff_preset = self.cb_preset.currentText()
        u.modulation = self.cb_mod.currentText()
        u.ke_reference = "LINE_TO_LINE" if self.cb_ke_ref.currentIndex() == 0 else "PHASE"
        u.current_is_rms = self.chk_rms.isChecked()
        for spec in ALL_FIELDS:
            val = self._spins[spec.attr].value()
            setattr(u, spec.attr, int(val) if spec.attr == "pole_pairs" else val)
        return u

    # --- 설정 저장/불러오기 ------------------------------------------------
    _MOTOR_SAVE_ATTRS = [s.attr for s in MOTOR_FIELDS]
    _COMPRESSOR_SAVE_ATTRS = [s.attr for s in COMPRESSOR_FIELDS]
    _CYCLE_SAVE_ATTRS = [s.attr for s in CYCLE_FIELDS]

    def _save_motor(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "설정 저장", "", "JSON 파일 (*.json);;모든 파일 (*)"
        )
        if not path:
            return
        data: dict[str, object] = {}
        # 냉매 · 사이클
        data["refrigerant"] = self.cb_ref.currentText()
        for attr in self._CYCLE_SAVE_ATTRS:
            data[attr] = self._spins[attr].value()
        # 압축기
        data["drive_mode"] = "SPEED_DRIVEN" if self.cb_mode.currentIndex() == 0 else "FLOW_DRIVEN"
        data["eff_preset"] = self.cb_preset.currentText()
        for attr in self._COMPRESSOR_SAVE_ATTRS:
            data[attr] = self._spins[attr].value()
        # 모터
        for attr in self._MOTOR_SAVE_ATTRS:
            data[attr] = self._spins[attr].value()
        data["ke_reference"] = "LINE_TO_LINE" if self.cb_ke_ref.currentIndex() == 0 else "PHASE"
        data["current_is_rms"] = self.chk_rms.isChecked()
        data["modulation"] = self.cb_mod.currentText()
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_motor(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "설정 불러오기", "", "JSON 파일 (*.json);;모든 파일 (*)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            QtWidgets.QMessageBox.warning(self, "불러오기 실패", str(e))
            return
        # 냉매 · 사이클
        if "refrigerant" in data:
            idx = self.cb_ref.findText(data["refrigerant"])
            if idx >= 0:
                self.cb_ref.setCurrentIndex(idx)
        for attr in self._CYCLE_SAVE_ATTRS:
            if attr in data:
                self._spins[attr].setValue(float(data[attr]))
        # 압축기
        if "drive_mode" in data:
            idx = 0 if data["drive_mode"] == "SPEED_DRIVEN" else 1
            self.cb_mode.setCurrentIndex(idx)
        if "eff_preset" in data:
            idx = self.cb_preset.findText(data["eff_preset"])
            if idx >= 0:
                self.cb_preset.setCurrentIndex(idx)
        for attr in self._COMPRESSOR_SAVE_ATTRS:
            if attr in data:
                self._spins[attr].setValue(float(data[attr]))
        # 모터
        for attr in self._MOTOR_SAVE_ATTRS:
            if attr in data:
                self._spins[attr].setValue(float(data[attr]))
        if "ke_reference" in data:
            idx = 0 if data["ke_reference"] == "LINE_TO_LINE" else 1
            self.cb_ke_ref.setCurrentIndex(idx)
        if "current_is_rms" in data:
            self.chk_rms.setChecked(bool(data["current_is_rms"]))
        if "modulation" in data:
            idx = self.cb_mod.findText(data["modulation"])
            if idx >= 0:
                self.cb_mod.setCurrentIndex(idx)
        self._on_change()


# ===========================================================================
# dq 플롯
# ===========================================================================
class DqPlot(pg.PlotWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setLabel("bottom", "i_d", units="A")
        self.setLabel("left", "i_q", units="A")
        self.showGrid(x=True, y=True, alpha=0.25)
        self.setAspectLocked(True)  # 원이 원으로 보여야 한다
        self.addLegend(offset=(-10, 10))

    def render(self, data) -> None:
        self.clear()
        if self.plotItem.legend is not None:
            self.plotItem.legend.clear()
        self.setTitle(data.title)
        for c in data.curves:
            self.plot(c.x, c.y, pen=_pen(c.color, c.width, c.dashed), name=c.name)
        for m in data.markers:
            self.plot(
                [m.x], [m.y],
                pen=None,
                symbol=m.symbol,
                symbolSize=m.size,
                symbolBrush=pg.mkBrush(m.color),
                symbolPen=pg.mkPen("#ffffff", width=1.5),
                name=m.name,
            )
        self.setXRange(*data.x_range, padding=0.02)
        self.setYRange(*data.y_range, padding=0.02)


class SweepPlot(pg.PlotWidget):
    """plan.md §6.3 — 설계자에게 가장 실용적인 화면."""

    def __init__(self) -> None:
        super().__init__()
        self.setLabel("bottom", "회전수", units="rpm")
        self.setLabel("left", "토크", units="N·m")
        self.showGrid(x=True, y=True, alpha=0.25)
        self.addLegend(offset=(-10, 10))
        self.setTitle("속도 스윕 — 가동 가능 범위")

    def render(self, data) -> None:
        self.clear()
        if self.plotItem.legend is not None:
            self.plotItem.legend.clear()
        self.plot(data.rpm, data.T_max, pen=_pen(COLOR_OK, 2.0), name="최대 가용 토크")
        self.plot(data.rpm, data.T_load, pen=_pen(COLOR_TORQUE, 2.0), name="부하 토크")
        if data.boundary_rpm:
            line = pg.InfiniteLine(
                pos=data.boundary_rpm,
                angle=90,
                pen=_pen(COLOR_NG, 2.0, dashed=True),
                label=f"최대 가동 {data.boundary_rpm:.0f} rpm",
                labelOpts={"position": 0.9, "color": COLOR_NG},
            )
            self.addItem(line)


# ===========================================================================
# 결과 패널
# ===========================================================================
class ResultPanel(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)

        self.badge = QtWidgets.QLabel("—")
        self.badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        f = self.badge.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        self.badge.setFont(f)
        self.badge.setMinimumHeight(44)
        v.addWidget(self.badge)

        self.violations = QtWidgets.QTextBrowser()
        self.violations.setMinimumHeight(150)
        self.violations.setOpenExternalLinks(False)
        v.addWidget(self.violations)

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["항목", "값"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, 1)

    def render(self, data) -> None:
        self.badge.setText(data.badge_text)
        self.badge.setStyleSheet(
            f"background:{data.badge_color}; color:white; border-radius:6px; padding:6px;"
        )

        if data.violations:
            html = []
            for sev, code, msg in data.violations:
                color = COLOR_NG if sev == "FAIL" else "#e37400"
                weight = "bold" if sev == "FAIL" else "normal"
                html.append(
                    f'<p style="margin:4px 0; color:{color}; font-weight:{weight};">'
                    f"▸ [{sev}] {code}<br>"
                    f'<span style="color:#3c4043; font-weight:normal;">&nbsp;&nbsp;{msg}</span>'
                    f"</p>"
                )
            self.violations.setHtml("".join(html))
        else:
            self.violations.setHtml(
                '<p style="color:#1e8e3e;">모든 제한 조건을 만족합니다.</p>'
            )

        self.table.setRowCount(len(data.rows))
        for i, row in enumerate(data.rows):
            a = QtWidgets.QTableWidgetItem(row.label)
            b = QtWidgets.QTableWidgetItem(row.value)
            if row.emphasis:
                for it in (a, b):
                    fnt = it.font()
                    fnt.setBold(True)
                    it.setFont(fnt)
            self.table.setItem(i, 0, a)
            self.table.setItem(i, 1, b)
        self.table.resizeColumnsToContents()


# ===========================================================================
# 메인 윈도우
# ===========================================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("압축기 가동 타당성 판별 시뮬레이터")
        self.resize(1500, 950)

        self.panel = InputPanel()
        self.dq = DqPlot()
        self.sweep = SweepPlot()
        self.result = ResultPanel()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(330)
        scroll.setMaximumWidth(400)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self.dq, "d–q 전류 평면")
        tabs.addTab(self.sweep, "속도 스윕")

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.addWidget(scroll)
        split.addWidget(tabs)
        split.addWidget(self.result)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        self.setCentralWidget(split)

        self.status = self.statusBar()
        self.panel.changed.connect(self.recompute)

        # PyQt6 에서 QShortcut 은 QtGui 에 있다 (QtWidgets 아님)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+R"), self, activated=self.recompute)

        self.recompute()

    def recompute(self) -> None:
        ui = self.panel.collect()
        try:
            warns = ui.validate()
            req = ui.to_request()
        except InputError as e:
            self.status.showMessage(f"입력 오류: {e}", 0)
            QtWidgets.QMessageBox.warning(self, "입력 오류", str(e))
            return

        verdict = evaluate(req)
        self.dq.render(build_dq_plot(verdict, req.motor))
        self.result.render(build_result_panel(verdict, req.motor))

        if req.compressor.drive_mode == "SPEED_DRIVEN":
            rpm_hi = max(ui.rpm * 3.0, 12000.0)
            rpms = [300.0 + (rpm_hi - 300.0) * i / 39.0 for i in range(40)]
            pts = sweep_speed(req, rpms, n_scan=150)
            boundary = max_feasible_speed(req, rpm_lo=300.0, rpm_hi=rpm_hi, tol_rpm=25.0)
            self.sweep.render(build_sweep_plot(pts, boundary))
            boundary_hz = boundary * ui.pole_pairs / 60.0
            msg = f"최대 가동 회전수 약 {boundary:.0f} rpm ({boundary_hz:.1f} Hz)"
        else:
            msg = "FLOW_DRIVEN 모드 — 속도 스윕은 SPEED_DRIVEN 에서만 제공됩니다."

        if warns:
            msg += "   |   " + " / ".join(warns)
        self.status.showMessage(msg, 0)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
