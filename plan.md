# plan.md — 구현 계획서

> **문서 목적**: `research.md` 의 개념을 실행 가능한 코드 구조·인터페이스·검증 절차로 변환한다.
> **선행 문서**: `research.md` (반드시 먼저 검토)
> **상태**: ✅ **승인됨 (2026-08-03) — Phase 0~5 구현 완료.** 계획 대비 변경 사항은 §10 참조.
> **작성일**: 2026-08-03

---

## 0. 확정된 설계 결정 (사용자 답변 반영)

| # | 항목 | 결정 |
|---|---|---|
| D1 | 속도–유량 관계 | **양방향 모드 토글**: `SPEED_DRIVEN` (N 입력 → ṁ 산출) / `FLOW_DRIVEN` (ṁ 입력 → N 산출) |
| D2 | 효율 모델 | **압력비 다항식 상관식** ($\eta_{isen}, \eta_{vol}, \eta_{mech}$), 계수는 UI 조정 가능 + 스크롤/로터리 프리셋 |
| D3 | 범위 | **전체** — Phase 0~5 (환경 → 열역학 코어 → 전자기 코어 → 판정 → 테스트 → UI) |
| D4 | dq 규약 | **amplitude-invariant (peak) 기준**, $p$ = 극쌍수, 토크식 계수 3/2 |
| D5 | 전압 한계 | $v_{max} = k_{margin} \cdot V_{dc}/\sqrt3$ (SVPWM), $k_{margin}$ 기본 0.95 |
| D6 | 판정 기준 | 시각화는 $R_s$ 무시 근사 타원, **판정은 $R_s$ 포함 정확식** |

---

## 1. 아키텍처 개요

### 1.1 설계 원칙

1. **도메인 격리**: 열역학 ↔ 전자기는 `LoadPoint(T_load, omega_m, T_dis, ...)` 단일 DTO로만 통신. 각 도메인은 상대 모듈을 import 하지 않는다.
2. **순수 함수 코어**: 모든 물리 연산은 부작용 없는 순수 함수. 상태·캐시·I/O는 바깥 계층으로 밀어낸다 → 테스트 용이성 확보.
3. **UI 무의존 코어**: `core/` 는 PyQt를 절대 import 하지 않는다. CLI만으로 전체 파이프라인 실행·검증이 가능해야 한다.
4. **단위는 SI, 변환은 경계에서만**: `ui/adapters.py` 만이 kPa↔Pa, rpm↔rev/s, mH↔H 변환을 수행한다.
5. **실패는 명시적으로**: 판정 결과는 예외가 아니라 `Verdict` 값 객체. 예외는 프로그래밍 오류에만 사용.

### 1.2 디렉터리 구조

```
2_Comp_Simulator/
├─ research.md
├─ plan.md
├─ pyproject.toml              # 의존성, pytest 설정, ruff 설정
├─ README.md
├─ src/
│  └─ compsim/
│     ├─ __init__.py
│     ├─ units.py              # SI 규약 상수, 변환 함수, peak/RMS 상수
│     ├─ models.py             # 전 계층 공용 dataclass (frozen)
│     ├─ thermo/
│     │  ├─ __init__.py
│     │  ├─ refrigerant.py     # CoolProp 래퍼 (얇게), 물성 조회 단일 창구
│     │  ├─ efficiency.py      # η_isen, η_vol, η_mech 상관식 + 프리셋
│     │  └─ cycle.py           # 상태점 계산, w_isen, ṁ↔N, T_load 산출
│     ├─ motor/
│     │  ├─ __init__.py
│     │  ├─ pmsm.py            # dq 방정식, 토크식, 전압 계산
│     │  ├─ limits.py          # 전류 원 / 전압 타원 기하, 교점 해석
│     │  └─ control.py         # MTPA, 약계자, 동작점 탐색, T_max_avail
│     ├─ feasibility/
│     │  ├─ __init__.py
│     │  ├─ thermal_gate.py    # 토출온도/감자/액압축/과냉도 판별
│     │  └─ evaluator.py       # Step0~4 오케스트레이션, Verdict 조립
│     └─ ui/
│        ├─ __init__.py
│        ├─ adapters.py        # 단위 변환 + 입력 검증
│        ├─ input_panel.py     # 파라미터 입력 위젯
│        ├─ dq_plot.py         # PyQtGraph dq 평면 플롯
│        ├─ result_panel.py    # Verdict 표시, 경고 텍스트
│        └─ main_window.py     # 조립 + 실행 엔트리
└─ tests/
   ├─ conftest.py              # 공용 픽스처 (표준 사이클, 표준 모터)
   ├─ test_units.py
   ├─ test_thermo_cycle.py     # ★ 검증 조건 1
   ├─ test_efficiency.py
   ├─ test_pmsm.py
   ├─ test_limits.py
   ├─ test_control.py          # MTPA / 약계자
   ├─ test_thermal_gate.py
   ├─ test_evaluator.py        # ★ 검증 조건 2
   └─ test_golden_regression.py # 골든 값 회귀 방지
```

### 1.3 의존성

| 패키지 | 용도 | 비고 |
|---|---|---|
| `CoolProp` | 냉매 물성 (HEOS) | **필수**. 없으면 명확한 안내 후 종료 |
| `numpy` | 벡터 연산, 곡선 샘플링 | 필수 |
| `scipy` | `brentq`, `minimize_scalar` (求根/최적화) | 필수 |
| `PyQt6` + `pyqtgraph` | UI | UI Phase에서만 |
| `pytest`, `pytest-cov` | 검증 | 개발 의존성 |
| `ruff` | 린트/포맷 | 개발 의존성 |

> ⚠️ **환경 주의**: 본 대화의 샌드박스는 PyPI 접근이 차단되어 있어 CoolProp 설치 검증을 수행하지 못했다. Phase 0의 첫 작업은 **사용자 로컬 환경에서 `import CoolProp` 성공을 확인**하는 것이다. 실패 시 `conda install -c conda-forge coolprop` 대안을 사용한다.

---

## 2. 데이터 모델 (models.py)

모두 `@dataclass(frozen=True)` — 불변 값 객체.

```
CycleSpec
  refrigerant: Literal["R32","R410A"]
  P_evap, P_cond      [Pa]
  dT_superheat        [K]
  dT_subcool          [K]
  dP_suction, dP_discharge [Pa]  (기본 0)

CompressorSpec
  V_disp              [m^3/rev]
  eff_model: EfficiencyCoeffs
  drive_mode: Literal["SPEED_DRIVEN","FLOW_DRIVEN"]
  N                   [rev/s]  (SPEED_DRIVEN 시 사용)
  m_dot               [kg/s]   (FLOW_DRIVEN 시 사용)

EfficiencyCoeffs
  isen: (a0,a1,a2)
  vol:  (b0,b1,kappa)
  mech: (c0,c1,c2)
  preset_name: str            # "SCROLL_DEFAULT" | "ROTARY_DEFAULT" | "CUSTOM"

MotorSpec
  Ld, Lq              [H]
  lambda_pm           [Wb]
  p                   [-] 극쌍수
  Rs                  [ohm]
  i_max               [A, peak]
  V_dc                [V]
  k_margin            [-] 기본 0.95
  modulation: Literal["SVPWM","SPWM"]

ThermalLimits
  T_dis_warn = 398.15 K (125 C)
  T_dis_fail = 408.15 K (135 C)
  T_demag_limit = 393.15 K (120 C)
  dT_magnet_offset = 10 K
  dT_sh_warn = 3 K
  PR_warn_high = 8.0

# --- 도메인 경계 DTO ---
LoadPoint
  T_load              [N·m]
  omega_m             [rad/s]
  omega_e             [rad/s]
  N                   [rev/s]
  m_dot               [kg/s]
  T_dis               [K]
  T_suc               [K]
  PR                  [-]
  w_isen              [J/kg]
  P_shaft             [W]
  eta_isen, eta_vol, eta_mech
  rho_suc             [kg/m^3]

OperatingPoint
  i_d, i_q            [A, peak]
  v_d, v_q            [V, peak]
  i_mag, v_mag        [A], [V]
  mode: Literal["MTPA","FLUX_WEAKENING","NONE"]

Verdict
  status: Literal["FEASIBLE","FEASIBLE_WITH_WARNING","INFEASIBLE"]
  violations: list[Violation]
  load: LoadPoint | None
  op: OperatingPoint | None
  T_max_avail: float | None
  torque_margin: float | None    # 1 - T_load/T_max_avail

Violation
  code: ViolationCode
  severity: Literal["WARN","FAIL"]
  message_ko: str
  actual: float
  limit: float
```

### 2.1 ViolationCode 열거형 (MECE)

| 그룹 | 코드 | 조건 |
|---|---|---|
| 사전 검사 | `PRESSURE_INVERSION` | $P_{dis} \le P_{suc}$ |
| | `SUPERCRITICAL` | $P_{cond} \ge P_c$ |
| | `BACK_EMF_EXCEEDS_VMAX` | $\omega_e\lambda_{pm} > v_{max}$ |
| 전자기 | `CURRENT_LIMIT` | 상수 토크 곡선 ∩ 전류 원 = ∅ |
| | `VOLTAGE_LIMIT` | 상수 토크 곡선 ∩ 전압 타원 = ∅ |
| | `BOTH_LIMIT` | 각각은 만족하나 교집합 = ∅ |
| | `SOLVER_NOT_CONVERGED` | 수치 해 실패 |
| 열적 | `DISCHARGE_TEMP_HIGH` | $T_{dis} > $ warn/fail 임계 |
| | `MAGNET_DEMAG_RISK` | $T_{magnet} > T_{demag}$ |
| 기계 | `LIQUID_SLUGGING` | $\Delta T_{sh}\le 0$ 또는 흡입 2상 |
| | `LOW_SUPERHEAT` | $0 < \Delta T_{sh} < 3$ K |
| | `NEGATIVE_SUBCOOL` | $\Delta T_{sc} < 0$ |
| 모델 유효성 | `PR_OUT_OF_RANGE` | $PR > 8$ 또는 $< 1.2$ |
| | `EFFICIENCY_EXTRAPOLATED` | 상관식 외삽 구간 |

---

## 3. 수학적 모델링 — 열역학 → 전자기 파이프라인

> 본 절이 사용자가 명시적으로 요구한 **"$T_{load}$ 가 모터 방정식과 어떻게 연결되는가"** 에 대한 답이다.

### 3.1 Stage 1 — 열역학이 생산하는 것

```
CycleSpec + CompressorSpec
        │
        ├─ (1) 흡입 상태:  T₁ = T_sat(P₁) + ΔT_sh
        │                  h₁ = h(P₁,T₁),  s₁ = s(P₁,T₁),  ρ_suc = ρ(P₁,T₁)   [CoolProp]
        │
        ├─ (2) 등엔트로피 토출:  h₂ₛ = h(P₂, s₁)                                [CoolProp]
        │      w_isen = h₂ₛ − h₁
        │
        ├─ (3) 효율:  PR = P₂/P₁
        │             η_isen = a₀+a₁·PR+a₂·PR²
        │             η_vol  = b₀ − b₁·(PR^(1/κ) − 1)
        │             η_mech = c₀+c₁·N+c₂·N²
        │
        ├─ (4) 실제 토출:  h₂ = h₁ + w_isen/η_isen
        │                  T_dis = T(P₂, h₂)                                    [CoolProp]
        │
        ├─ (5) 모드 분기 — 동일 관계식 ṁ = ρ_suc·V_disp·N·η_vol 의 미지수만 다름
        │      SPEED_DRIVEN:  ṁ = ρ_suc·V_disp·N·η_vol
        │      FLOW_DRIVEN :  N  = ṁ / (ρ_suc·V_disp·η_vol)
        │
        └─ (6) ★ 결합점 ★
               P_shaft = ṁ·w_isen / (η_isen·η_mech)
               ω_m     = 2π·N
               T_load  = P_shaft / ω_m
               ω_e     = p·ω_m
```

**출력**: `LoadPoint(T_load, omega_m, omega_e, T_dis, ...)`

**주의**: $\eta_{mech}$ 가 $N$ 의 함수이므로 FLOW_DRIVEN 모드에서는 $N$ 을 먼저 (5)에서 구한 뒤 (3)의 $\eta_{mech}$ 를 평가하는 순서를 지켜야 한다. $\eta_{vol}$ 은 $PR$ 만의 함수라 반복이 불필요하다. **향후 $\eta_{vol}(PR,N)$ 확장 대비**: `cycle.py` 는 내부적으로 고정점 반복 함수 `_solve_flow_speed(...)` 를 두되, v1에서는 1회 평가로 즉시 수렴하도록 구현한다(인터페이스 안정성 확보).

### 3.2 Stage 2 — 전자기가 소비하는 것

`LoadPoint` 의 $(T_{load}, \omega_e)$ 만 사용한다. 냉매가 무엇이었는지, 압력이 얼마였는지는 이 단계에서 **알 필요도 없고 알아서도 안 된다**.

**풀어야 할 연립계**:

$$
\begin{cases}
\dfrac{3}{2}p\left[\lambda_{pm} i_q + (L_d-L_q)i_d i_q\right] = T_{load} & \text{(토크 등식)}\\[8pt]
i_d^2 + i_q^2 \le i_{max}^2 & \text{(전류 제약)}\\[6pt]
(R_s i_d - \omega_e L_q i_q)^2 + \left(R_s i_q + \omega_e(L_d i_d + \lambda_{pm})\right)^2 \le v_{max}^2 & \text{(전압 제약)}
\end{cases}
$$

미지수 2개($i_d,i_q$), 등식 1개 → **1-자유도 곡선(상수 토크 궤적)** 위에서 제약을 만족하는 점을 찾는 문제. 이 구조 인식이 알고리즘 설계의 핵심이다.

**파라미터화 전략**: 상수 토크 곡선을 $i_q$ 로 파라미터화한다.

$$i_d(i_q) = \frac{\dfrac{2T_{load}}{3p\,i_q} - \lambda_{pm}}{L_d - L_q}\quad (L_d \ne L_q)$$

SPMSM($L_d = L_q$)은 $i_q = \dfrac{2T_{load}}{3p\lambda_{pm}}$ 로 고정되고 $i_d$ 는 자유 → 별도 분기.

이로써 **2차원 탐색이 $i_q$ 에 대한 1차원 문제로 축소**된다. 안정성과 속도 모두 확보.

### 3.3 Stage 3 — 동작점 탐색 알고리즘

```
입력: T_load, ω_e, MotorSpec
──────────────────────────────────────────────
S0. 사전 검사
    if ω_e·λ_pm > v_max  and  T_load > 0:
        → 후보 구간이 매우 좁음. 계속 진행하되 BACK_EMF 플래그 기록
    if T_load ≈ 0: → 무부하, i_q=0, MTPA 자명해

S1. MTPA 후보
    g(i_q) = (3/2)p[λ_pm·i_q + (Ld−Lq)·i_d_mtpa(i_q)·i_q] − T_load
    where i_d_mtpa(i_q) = λ_pm/(2(Lq−Ld)) − sqrt(λ_pm²/(4(Lq−Ld)²) + i_q²)
    brentq 로 g(i_q)=0 을 [0, i_max] 에서 풀이
    → 근이 없으면 CURRENT_LIMIT (MTPA 궤적으로도 토크 미달)

S2. 전류 한계 검사
    if |i_mtpa| > i_max → CURRENT_LIMIT

S3. 전압 한계 검사
    if |v(i_mtpa, ω_e)| ≤ v_max → FEASIBLE, mode=MTPA, 종료

S4. 약계자 (전압 초과 시)
    f(i_q) = |v(i_d(i_q), i_q, ω_e)|² − v_max²      # 상수 토크 곡선 위에서
    구간 [i_q_min, i_q_max] 에서 f 의 부호 변화 탐색 (샘플링 → brentq)
    가능 구간 ∩ {|i| ≤ i_max} 에서 |i| 최소점 선택
    → 해 있음: FEASIBLE, mode=FLUX_WEAKENING
    → 전압은 만족하나 전류 초과: CURRENT_LIMIT
    → 전압 만족 구간 자체가 없음: VOLTAGE_LIMIT
    → 각각은 되나 교집합 없음: BOTH_LIMIT

S5. T_max_avail 계산 (여유율 표시용)
    전류 원 ∩ 전압 타원 영역에서 T_em 최대화
    → 경계 탐색: 전류 원 위 스윕 + 전압 타원 위 스윕 + 내부 MTPA 최대점
    → 여유율 = 1 − T_load/T_max_avail
```

**수치적 견고성 요구사항**:

- 모든 `brentq` 호출은 사전에 구간 부호 변화를 확인하고, 실패 시 `SOLVER_NOT_CONVERGED` 를 반환(예외 전파 금지).
- $|L_q - L_d| < 10^{-9}$ H → SPMSM 분기.
- $i_q \to 0$ 근방 0-나눗셈 방지: $|i_q| < 10^{-9}$ 가드.
- 모든 탐색 구간은 물리적 상한($i_{max}$ 의 1.5배 등)으로 유계.

### 3.4 Stage 4 — 열적 게이트

전자기 판정과 **독립적으로** 실행하고 결과를 합산한다(전자기 불가여도 열적 위반을 함께 보고해야 설계 피드백이 유용하다).

```
T_dis ≥ T_dis_fail        → FAIL: DISCHARGE_TEMP_HIGH
T_dis_warn ≤ T_dis < fail → WARN: DISCHARGE_TEMP_HIGH
T_dis + Δoffset > T_demag → FAIL: MAGNET_DEMAG_RISK
흡입점 phase ≠ gas        → FAIL: LIQUID_SLUGGING
0 < ΔT_sh < 3 K           → WARN: LOW_SUPERHEAT
ΔT_sc < 0                 → FAIL: NEGATIVE_SUBCOOL
PR > 8 또는 < 1.2         → WARN: PR_OUT_OF_RANGE
```

### 3.5 최종 Verdict 조립 규칙

```
FAIL 이 하나라도 존재       → INFEASIBLE
FAIL 없고 WARN 존재         → FEASIBLE_WITH_WARNING
둘 다 없음                  → FEASIBLE
```

---

## 4. Phase별 구현 계획

### Phase 0 — 환경 및 스캐폴딩 (예상 0.5일)

| # | 작업 | 완료 기준 |
|---|---|---|
| 0.1 | `pyproject.toml` 작성, 가상환경 구성 | `pip install -e ".[dev]"` 성공 |
| 0.2 | CoolProp 설치 및 동작 확인 | `PropsSI("H","P",1e6,"T",285,"R32")` 반환 |
| 0.3 | `units.py` — SI 규약, 변환 함수, `SQRT3`, `PEAK_FROM_RMS` 상수 | `test_units.py` 통과 |
| 0.4 | `models.py` — 전 dataclass 정의 | import 성공, 타입 힌트 완비 |
| 0.5 | ruff + pytest 설정, CI 스크립트 | `ruff check` / `pytest` 실행 가능 |

**게이트**: `test_units.py` 통과.

---

### Phase 1 — 열역학 코어 (예상 1.5일)

| # | 작업 | 완료 기준 |
|---|---|---|
| 1.1 | `refrigerant.py` — CoolProp 얇은 래퍼 (`h_pt`, `s_pt`, `h_ps`, `t_ph`, `rho_pt`, `t_sat`, `p_crit`, `phase_pt`) | 모든 함수 스모크 테스트 |
| 1.2 | `efficiency.py` — 3개 상관식 + 클램프 + 외삽 플래그 + SCROLL/ROTARY 프리셋 | `test_efficiency.py` 통과 |
| 1.3 | `cycle.py` — 상태점 1/2s/2/3, `w_isen`, `T_dis` | 상태점 값이 물리적으로 타당 (h₂>h₂ₛ>h₁) |
| 1.4 | `cycle.py` — `_solve_flow_speed` 양방향 모드 | 왕복 변환 테스트: N→ṁ→N 항등 |
| 1.5 | `cycle.py` — `compute_load_point()` → `LoadPoint` 반환 | ★ **검증 조건 1** 통과 |

**게이트**: `test_thermo_cycle.py` 전부 통과 (§5.1 참조).

---

### Phase 2 — 전자기 코어 (예상 2일)

| # | 작업 | 완료 기준 |
|---|---|---|
| 2.1 | `pmsm.py` — `torque(id,iq)`, `voltage(id,iq,ω_e)`, `back_emf(ω_e)` | 해석 검산 테스트 통과 |
| 2.2 | `limits.py` — 전류 원/전압 타원 기하 파라미터, 점 포함 여부 판정, 플롯용 좌표 생성 | 알려진 기하값과 일치 |
| 2.3 | `control.py` — `mtpa_id(iq)`, SPMSM 분기 | $L_d=L_q$ 시 $i_d=0$ |
| 2.4 | `control.py` — `solve_operating_point()` (§3.3 S1–S4) | 저속 MTPA / 고속 약계자 시나리오 통과 |
| 2.5 | `control.py` — `max_available_torque(ω_e)` | 속도 증가 시 단조 감소 확인 |

**게이트**: `test_pmsm.py`, `test_limits.py`, `test_control.py` 전부 통과.

---

### Phase 3 — 판정 오케스트레이션 (예상 1일)

| # | 작업 | 완료 기준 |
|---|---|---|
| 3.1 | `thermal_gate.py` — §3.4 규칙 전부 | 각 위반 코드별 트리거 테스트 |
| 3.2 | `evaluator.py` — Step 0 사전 위생 검사 | 압력 역전/초임계 즉시 반환 |
| 3.3 | `evaluator.py` — Step 1~4 오케스트레이션, `Verdict` 조립 | ★ **검증 조건 2** 통과 |
| 3.4 | CLI 엔트리 `python -m compsim.cli --config case.json` | JSON 입출력으로 전체 파이프라인 실행 |

**게이트**: `test_evaluator.py` 전부 통과. **이 시점에 UI 없이도 시뮬레이터가 완전히 동작해야 한다.**

---

### Phase 4 — 검증 루프 강화 (예상 1일)

| # | 작업 | 완료 기준 |
|---|---|---|
| 4.1 | 골든 값 회귀 테스트 pin (§5.3) | 값 변경 시 즉시 실패 |
| 4.2 | 속성 기반(property-based) 테스트: 단조성·대칭성·에너지 보존 | §5.4 목록 전부 |
| 4.3 | 커버리지 측정, `core/` 90 % 이상 | `pytest --cov` 리포트 |
| 4.4 | 실패 케이스 원인 분석 및 자체 수정 루프 | 전 테스트 green |

**게이트**: 🚦 **전 테스트 통과 전까지 Phase 5(UI) 착수 금지** — 사용자 요구사항.

---

### Phase 5 — PyQtGraph UI (예상 2일)

| # | 작업 | 완료 기준 |
|---|---|---|
| 5.1 | `adapters.py` — 단위 변환 + 입력 유효성 검사 | 잘못된 입력 시 UI 레벨 에러 메시지 |
| 5.2 | `input_panel.py` — 냉매/사이클/압축기/모터 4개 그룹, 모드 토글, 프리셋 콤보 | 모든 파라미터 편집 가능 |
| 5.3 | `dq_plot.py` — 전류 원(파랑), 전압 타원(초록), 상수 토크 곡선(주황), 동작점 마커 | §6 시각화 명세 충족 |
| 5.4 | `result_panel.py` — Verdict 배지, 위반 리스트, 여유율 게이지, 핵심 수치 테이블 | 불가 시 **붉은색 경고 텍스트로 원인 명시** |
| 5.5 | `main_window.py` — 레이아웃 조립, 재계산 트리거, 속도 스윕 슬라이더 | 실행 후 인터랙티브 동작 |
| 5.6 | 속도 스윕 뷰: $\omega$ vs $T_{max}^{avail}$ 및 $T_{load}$ 오버레이 | 가동 가능 속도 범위 시각 확인 |

**게이트**: 사용자에게 실행 및 시각적/공학적 검토 요청.

---

## 5. 검증 명세 (Verifier)

> 사용자 요구: 기계 판정 가능한 pytest 코드를 **먼저** 작성하고 자체 검증한 뒤에만 UI 검토를 요청한다.

### 5.1 ★ 검증 조건 1 — 열역학-동역학 연동 (`test_thermo_cycle.py`)

**표준 픽스처 `STANDARD_R32_CYCLE`**:

| 항목 | 값 |
|---|---|
| 냉매 | R32 |
| 증발 온도 / 압력 | 7 °C / $P_{sat}$(CoolProp) |
| 응축 온도 / 압력 | 45 °C / $P_{sat}$(CoolProp) |
| 흡입 과열도 | 5 K |
| 과냉도 | 5 K |
| 행정체적 | 20 cc/rev |
| 회전수 | 3600 rpm (60 rev/s) |
| $\eta_{isen}$ | 0.70 (테스트에서는 상수로 고정) |
| $\eta_{vol}$ | 0.95 |
| $\eta_{mech}$ | 0.95 |

**테스트 1-A — 독립 경로 교차 검증 (허용오차 10 %)**

CoolProp 경로와 실가스 보정 폴리트로픽 수계산 경로를 비교한다.

$$w_{ref} = \frac{\gamma}{\gamma-1} Z R T_1\left[PR^{(\gamma-1)/\gamma}-1\right]$$

R32: $\gamma=1.29$, $Z=0.88$, $R=159.8$ J/(kg·K) → $w_{ref} \approx 46.5$ kJ/kg, $\rho_{suc}\approx 25$ kg/m³.

```
assert rel_error(w_isen_coolprop, w_ref) < 0.10
assert rel_error(rho_suc_coolprop, 25.2) < 0.10
```

> **왜 10 %인가**: 경로 B는 이상기체 + 상수 $Z$ 근사이므로 그 자체가 수 % 오차를 갖는다. 여기에 5 %를 요구하면 **테스트가 근사식의 정확도를 검증하는 꼴**이 되어 무의미하다. 5 % 요구는 아래 1-B에서 충족한다.

**테스트 1-B — 토크 산출 (허용오차 5 %)**

$$T_{load}^{ref} = \frac{\rho_{suc}V_{disp}\eta_{vol}\,w_{isen}}{2\pi\,\eta_{isen}\eta_{mech}}$$

이 식은 §research 1.7에서 유도한 **닫힌 형태 해석식**이며, 구현 코드와는 **다른 경로**(구현은 $\dot m$ → $P_{shaft}$ → $/\omega_m$ 를 거침)로 계산되므로 독립 검증이 성립한다. 수계산 참조값을 테스트에 명시하고 5 % 이내 일치를 요구한다.

```
# 개략 검산 (CoolProp 실측치로 Phase 1에서 pin)
# ρ≈25.2, V=20e-6, ηv=0.95, w≈46.5e3, ηi=0.70, ηm=0.95
# T_ref ≈ (25.2 × 20e-6 × 0.95 × 46.5e3) / (2π × 0.70 × 0.95) ≈ 5.32 N·m
assert rel_error(load.T_load, T_ref_hand) < 0.05
```

**테스트 1-C — R32 vs R410A 토출 온도 특성**

동일 증발/응축 온도에서:
```
assert T_dis_R32 > T_dis_R410A + 15.0   # K, research §1.6
```
물리적 방향성을 고정하는 회귀 방어선.

**테스트 1-D — 왕복 항등성**
```
SPEED_DRIVEN(N=60) → ṁ  →  FLOW_DRIVEN(ṁ) → N'
assert abs(N' − 60) < 1e-9
```

**테스트 1-E — 에너지 보존**
```
assert P_shaft == pytest.approx(ṁ*(h2−h1)/η_mech, rel=1e-9)
assert h2 > h2s > h1
```

---

### 5.2 ★ 검증 조건 2 — 전압 제한 트리거 (`test_evaluator.py`)

**설계 논리** (research §1.7, §6): 용적형 압축기의 $T_{load}$ 는 회전수에 거의 무관하지만, 전압 타원 반축은 $1/\omega_e$ 로 축소된다. 따라서 **속도만 극단적으로 올리면 반드시 `VOLTAGE_LIMIT` 이 발생**한다. 이는 매직 넘버가 아닌 해석적으로 보장된 결과다.

**표준 모터 픽스처 `STANDARD_IPMSM`**:

| 항목 | 값 | 근거 |
|---|---|---|
| $L_d$ | 3.0 mH | 소형 가정용 압축기 IPMSM 전형값 |
| $L_q$ | 6.0 mH | 돌극비 2.0 |
| $\lambda_{pm}$ | 0.08 Wb | – |
| $p$ | 3 (극쌍) | 6극 |
| $R_s$ | 0.5 Ω | – |
| $i_{max}$ | 20 A (peak) | – |
| $V_{dc}$ | 310 V | 단상 220 V 정류 |
| $v_{max}$ | $0.95 \times 310/\sqrt3 \approx 170$ V | – |

> **사전 손계산 (픽스처 타당성 확인)** — 이 계산으로 두 테스트가 의도한 결과를 내는지 미리 검증했다.
>
> - 특성 전류 $i_{ch} = \lambda_{pm}/L_d = 0.08/0.003 = 26.7\ \mathrm{A} > i_{max}=20\ \mathrm{A}$
>   → **유한 최대 속도를 갖는 모터**. 고속에서 전압 타원이 전류 원과 완전히 분리되므로 테스트 2-B/2-C가 확정적으로 성립한다. (만약 $i_{ch}<i_{max}$ 였다면 이론상 무한 속도 구동이 가능해 테스트가 성립하지 않는다.)
> - @3600 rpm: $T_{load}\approx 5.33$ N·m → $i_q\approx 12.5$ A, $i_d\approx -5$ A, $|i|\approx 13.5 < 20$ ✓, $|v|\approx 118 < 170$ V ✓ → **Feasible**
> - @36000 rpm: 전압 타원 반축 $170/(11310\times0.003)=5.0$ A, $170/(11310\times0.006)=2.5$ A, 중심 $i_d=-26.7$ A
>   → 타원의 $i_d$ 범위 $[-31.7,\,-21.7]$ 이 전류 원($|i|\le20$)과 **완전히 분리** → 가용 토크 0 → **Infeasible**

**테스트 2-A — 정상 속도 Feasible**
```
N = 3600 rpm (ω_e = 3·2π·60 = 1131 rad/s)
verdict = evaluate(STANDARD_R32_CYCLE, STANDARD_IPMSM, N)
assert verdict.status in ("FEASIBLE","FEASIBLE_WITH_WARNING")
assert VOLTAGE_LIMIT not in verdict.violation_codes
assert verdict.op.i_mag < STANDARD_IPMSM.i_max
```

**테스트 2-B — 극단 속도 → VOLTAGE_LIMIT 트리거**
```
N = 36000 rpm (ω_e = 11310 rad/s)
# 사전 확인: 무부하 역기전력 = ω_e·λ_pm = 11310 × 0.08 ≈ 905 V >> v_max 170 V
verdict = evaluate(..., N)
assert verdict.status == "INFEASIBLE"
assert verdict.violation_codes & {VOLTAGE_LIMIT, BOTH_LIMIT}   # 둘 중 하나
assert BACK_EMF_EXCEEDS_VMAX in verdict.violation_codes        # 사전 검사도 함께 걸림
assert verdict.T_max_avail == pytest.approx(0.0, abs=1e-6)
```
> 위 손계산대로 전압 타원과 전류 원이 분리되므로 엄밀히는 `BOTH_LIMIT` 이 반환된다. 어느 쪽이든 "전압 한계가 원인" 이라는 정보가 UI에 전달되어야 하며, 테스트는 그 집합 관계를 검증한다.

**테스트 2-C — 전이 속도의 단조성 (핵심)**
```
speeds = linspace(1000, 30000, 60) rpm
feasible = [evaluate(..., N).is_feasible for N in speeds]
# feasible 시퀀스는 True...True False...False 형태여야 한다 (단 한 번의 전이)
transitions = count_transitions(feasible)
assert transitions <= 1
```
> 단조성 테스트는 **경계값 하드코딩 없이** 물리적 일관성을 검증한다. 알고리즘이 특정 속도에서 오판정하면 전이가 2회 이상 나타나 즉시 잡힌다.

**테스트 2-D — 전류 제한 분리 트리거**
```
i_max 를 2 A 로 축소 (속도는 정상)
assert CURRENT_LIMIT in verdict.violation_codes
assert VOLTAGE_LIMIT not in verdict.violation_codes
```
두 제한이 **혼동 없이 구분**되는지 확인 — 원인 분해 로직의 MECE성 검증.

**테스트 2-E — 열적 게이트 독립 트리거**
```
응축 온도를 65 °C 로 올림 → T_dis > 125 °C
assert DISCHARGE_TEMP_HIGH in verdict.violation_codes
```

---

### 5.3 골든 값 회귀 테스트 (`test_golden_regression.py`)

Phase 1 완료 시점의 CoolProp 계산값을 JSON 으로 고정(pin)하고, 이후 리팩터링에서 값이 바뀌면 즉시 실패시킨다. 허용오차 $10^{-6}$.

| 케이스 | 고정 값 |
|---|---|
| R32 표준 사이클 | $h_1, s_1, h_{2s}, \rho_{suc}, w_{isen}, T_{load}$ |
| R410A 표준 사이클 | 동일 |
| 표준 IPMSM @3600rpm | $i_d, i_q, v_d, v_q, T_{max}^{avail}$ |

> 골든 값은 **정확성의 증거가 아니라 변경 감지 장치**다. 정확성은 5.1의 독립 경로가 담당한다. 이 구분을 문서에 명시해 오해를 방지한다.

### 5.4 속성 기반 불변식 테스트

| # | 불변식 | 근거 |
|---|---|---|
| P1 | $T_{em}(i_d^*,i_q^*) = T_{load}$ (rel 1e-6) | 토크 등식은 반드시 만족 |
| P2 | MTPA 해는 동일 토크의 임의 해보다 $\|i\|$ 가 작거나 같다 | MTPA 정의 |
| P3 | $T_{max}^{avail}(\omega)$ 는 $\omega$ 에 대해 비증가 | 전압 타원 축소 |
| P4 | $\eta \in (0,1]$ 항상 | 물리 |
| P5 | $PR$ 증가 → $T_{dis}$ 증가 | 압축 물리 |
| P6 | $V_{dc}$ 증가 → feasible 영역 확대 (단조) | 전압 여유 |
| P7 | $i_{max}$ 증가 → feasible 영역 확대 (단조) | 전류 여유 |
| P8 | $T_{load} = 0$ → $i_q = 0$ | 무부하 |

### 5.5 실행 규약

```bash
pytest -q                      # 전체
pytest -q -m "not slow"        # 빠른 루프
pytest --cov=src/compsim       # 커버리지
```

**자체 수정 루프**: 실패 시 (1) 실패 어서션의 물리적 의미 해석 → (2) 코드 vs 테스트 중 어느 쪽이 틀렸는지 판단 → (3) 수정 → (4) 전체 재실행. 테스트를 통과시키기 위해 **허용오차를 임의로 늘리는 것은 금지**. 오차 조정이 필요하면 그 근거를 문서에 기록하고 사용자에게 보고한다.

---

## 6. UI 시각화 명세

### 6.1 dq 전류 평면 (메인 플롯)

| 요소 | 색상/스타일 | 내용 |
|---|---|---|
| 전류 한계 원 | 파랑 실선 | 원점 중심, 반경 $i_{max}$ |
| 전압 한계 타원 | 초록 실선 | 중심 $(-\lambda_{pm}/L_d, 0)$, 반축 $v_{max}/(\omega_e L_d)$, $v_{max}/(\omega_e L_q)$ |
| 상수 토크 곡선 | 주황 실선 | $T_{em}=T_{load}$ 궤적 (쌍곡선형) |
| MTPA 궤적 | 회색 점선 | 참조선 |
| 동작점 | ● 마커 | Feasible: 초록 / Infeasible: 빨강 |
| 특성 전류점 | × 마커 | $(-\lambda_{pm}/L_d, 0)$ |

축: $i_d$ (x, 음수 영역 포함), $i_q$ (y). 종횡비 1:1 고정 (원이 원으로 보여야 함).

### 6.2 결과 패널

```
┌─────────────────────────────────────┐
│  ⛔ 동작 불가 (INFEASIBLE)          │  ← 배지, 빨강
├─────────────────────────────────────┤
│ ▸ 전압 제한 초과 (VOLTAGE_LIMIT)     │  ← 붉은색 굵은 텍스트
│   요구 전압 905.2 V > 한계 170.1 V   │
│ ▸ 토출 온도 경고 (WARN)              │  ← 주황
│   T_dis 128.4 °C > 125.0 °C          │
├─────────────────────────────────────┤
│ 부하 토크      5.33 N·m              │
│ 최대 가용 토크 0.00 N·m              │
│ 토크 여유율    해당 없음              │  ← 음수면 빨강
│ 샤프트 동력    2009 W                │
│ 압력비         2.80                  │
│ 질량 유량      103.4 kg/h            │
└─────────────────────────────────────┘

> 위 수치는 §5.2 표준 픽스처 @36000 rpm 케이스의 실제 손계산 결과다(§5.2 각주 참조). UI 문구 검토용 예시이므로 구현 시 실제 계산값으로 대체된다.
```

### 6.3 속도 스윕 뷰 (보조 플롯)

x축 회전수(rpm), y축 토크(N·m). $T_{max}^{avail}(\omega)$ 곡선과 $T_{load}(\omega)$ 곡선을 겹쳐 그리고, 교점(= 최대 가동 속도)에 수직선을 표시한다. **이것이 설계자에게 가장 실용적인 화면**이다 — 단일 동작점의 가부보다 "어디까지 돌릴 수 있는가"가 실제 설계 질문이기 때문.

---

## 7. 일정 요약 및 게이트

| Phase | 산출물 | 예상 | 게이트 |
|---|---|---|---|
| 0 | 스캐폴딩, units, models | 0.5일 | `test_units.py` |
| 1 | 열역학 코어 | 1.5일 | ★ 검증 조건 1 |
| 2 | 전자기 코어 | 2일 | MTPA/약계자 테스트 |
| 3 | 판정 오케스트레이션 + CLI | 1일 | ★ 검증 조건 2 |
| 4 | 검증 루프 강화 | 1일 | 🚦 **전 테스트 green — UI 착수 전제조건** |
| 5 | PyQtGraph UI | 2일 | 사용자 시각/공학 검토 |

**총 예상: 8일**

---

## 8. 명시적 비범위 (Out of Scope, v1)

MECE 원칙상 "하지 않을 것"도 명시한다.

| 항목 | 사유 | 향후 |
|---|---|---|
| 과도 응답(기동 토크, 관성) | 정상상태 판별이 목적 | v2 |
| 모터 손실(동손/철손) 의 사이클 되먹임 | 단방향 파이프라인 유지 | v2 고정점 반복 |
| 크랭크 각도별 순시 토크 리플 | 평균 토크로 충분 | v2 |
| 자기회로 FEA 기반 감자 판별 | FEA 데이터 필요 | 외부 데이터 연동 |
| 인젝션/2단 압축 사이클 | 단단 압축 한정 | v2 |
| 온도 의존 파라미터($R_s(T)$, $\lambda_{pm}(T)$) | 상온 상수 가정 | v2 |
| R290/R454B 등 추가 냉매 | CoolProp 지원 시 문자열 추가만으로 확장 가능 | 즉시 확장 가능 |

---

## 9. 검토 요청 사항

다음 항목에 대해 인라인 메모를 남겨주시면 반영 후 착수하겠습니다.

| # | 확인 요청 | 현재 가정 |
|---|---|---|
| C1 | `research.md` §8 의 미해결 질문 Q1~Q5 | 문서 내 기본 가정대로 진행 |
| C2 | 표준 모터 픽스처 스펙(§5.2)이 실제 대상과 유사한가? | 소형 가정용 6극 IPMSM 전형값 |
| C3 | 검증 조건 1의 허용오차 이원화(1-A 10 % / 1-B 5 %)를 수용하는가? | §5.1 논거대로 진행 |
| C4 | 토출 온도 한계 125 °C(WARN) / 135 °C(FAIL) 이원화가 적절한가? | 이원화 채택 |
| C5 | UI 프레임워크 PyQt6 로 확정? (PyQt5 필요 시 사전 고지) | PyQt6 |
| C6 | 속도 스윕 뷰(§6.3)를 v1 범위에 포함할 것인가? | 포함 |

---

---

## 10. 구현 후 변경 이력 (계획 대비 실제)

> 2026-08-03 "계획 승인" 이후 Phase 0~5 구현 완료. 계획에서 벗어난 항목을 전부 기록한다.

### 10.1 환경 제약으로 인한 변경

| # | 계획 | 실제 | 사유 |
|---|---|---|---|
| A1 | `scipy.optimize.brentq` 사용 | `numerics.py` 에 Brent 법 자체 구현 | 개발 환경 PyPI 접근 차단. 부수 효과로 런타임 의존성이 `numpy` 하나로 축소됨 |
| A2 | `pytest` 로 검증 | pytest 호환 테스트 + 폴백 러너 `run_tests.py` | 동일. 테스트는 fixture 없이 작성해 두 러너 모두에서 동작 |
| A3 | CoolProp 단일 백엔드 | `RefrigerantBackend` 프로토콜 + 2개 구현 | CoolProp 미설치 환경 대응. **부수 효과가 더 중요**: §6 의 '경로 B' 가 코드로 실체화되어 교차 검증이 구조적으로 보장됨 |
| A4 | `pytest-cov` | stdlib `trace` 기반 측정 | 동일 |

**A3 의 안전장치**: 근사 백엔드 사용 시 `REFERENCE_BACKEND_IN_USE` 경고가 항상 Verdict 에 포함된다. 설계 판단에 CoolProp 이 필수라는 점은 코드·README·경고문 세 곳에 명시.

### 10.2 설계 결함 수정 (구현 중 발견)

| # | 결함 | 수정 | 확인 요청 |
|---|---|---|---|
| **B1** | **열적 게이트 순서 모순** — `dT_magnet_offset = +10 K`, `T_demag_limit = 120 °C` 조합에서 감자 게이트가 `T_dis > 110 °C` 에 발동하여 토출 온도 경고선(125 °C)을 항상 가림. 즉 §3.4 의 두 게이트 중 하나가 무의미해짐 | `dT_magnet_offset = -10 K` 로 변경 (자석은 최고온 토출 가스보다 낮은 온도에서 평형). 게이트 순서가 125 °C(경고) → 130 °C(감자) → 135 °C(한계) 로 정합 | ⚠️ **C7** 아래 참조 |
| B2 | `_search_min_current` 가 격자 스캔만 수행 → 해석적 최적점(i_d=0)을 정확히 포착 못 함 | 3단계로 변경: 해석적 최적점 우선 검사 → 격자 스캔 → 전압 경계(brentq)/내부 최소(황금분할) 정밀화 | 없음 |
| B3 | UI PyQt 검출 테스트가 문자열 매칭 → 주석의 "PyQt" 언급까지 오탐 | AST 기반 import 검사로 교체 + 역방향 테스트 추가 | 없음 |
| B4 | `main_window.py` 에서 `QtWidgets.QShortcut` 참조 (PyQt6 에서는 `QtGui` 소속) | `QtGui.QShortcut` 으로 수정, Ctrl+R 재계산 단축키 연결 | 없음 |

### 10.3 계획에 없던 추가

| 항목 | 사유 |
|---|---|
| `ui/viewmodel.py` (PyQt 무의존 표시 로직) | §1.1 원칙 3(UI 무의존 코어)을 UI 계층 내부까지 관철. 시각화 로직 전체가 PyQt 없이 테스트됨 |
| `ThermalLimits.check_gate_ordering()` | B1 재발 방지. UI 경고로도 노출 |
| `max_feasible_speed()` | §6.3 속도 스윕 뷰의 경계선 계산 |
| `tools/pin_golden.py` | §5.3 골든 값 생성/갱신 자동화 |
| `voltage_boundary_exact()` | D6(판정=정확식 / 시각화=근사)의 차이를 UI 에서 눈으로 확인 가능하게 |

### 10.4 검증 결과

```
테스트   214 통과 / 0 실패 / 5 스킵(CoolProp 필요)
커버리지 테스트 가능 범위 91.8 %  (전체 84.5 %, main_window.py 는 PyQt 필요로 제외)
```

| 검증 조건 | 상태 |
|---|---|
| 1-A 독립 경로 교차 검증 (10 %) | ⏸ CoolProp 설치 후 실행 필요 |
| 1-B 토크 산출 (5 %) | ✅ 통과 (참조 백엔드 경로). CoolProp 경로는 설치 후 자동 실행 |
| 1-B' 닫힌 형태 항등성 | ✅ 통과 (1e-12) |
| 2-A 정상 속도 Feasible | ✅ 통과 |
| 2-B 극단 속도 VOLTAGE_LIMIT | ✅ 통과 (`T_max_avail = 0` 확인) |
| 2-C 전이 단조성 | ✅ 통과 (40점 스윕, 전이 1회) |
| 2-D 전류 제한 분리 | ✅ 통과 |
| 2-E 열적 게이트 독립 | ✅ 통과 |
| P1~P8 불변식 | ✅ 전부 통과 |

**손계산 대조** (표준 R32 7/45 °C, 20 cc/rev, 3600 rpm, 상수 효율 0.70/0.95/0.95):

| 항목 | 손계산 | 구현 |
|---|---|---|
| T_load | 5.17 N·m | 5.172 N·m |
| i_d, i_q @3600rpm | ≈ (−5, 12.5) A | (−4.74, 12.20) A |
| \|v\| @3600rpm | ≈ 118 V | 117.2 V |
| T_max_avail @36000rpm | 0 (영역 분리) | 0.000 |

### 10.5 추가 확인 요청

| # | 항목 | 현재 값 |
|---|---|---|
| **C7** | **B1 의 `dT_magnet_offset` 부호와 크기** — 자석 온도를 토출 가스 대비 어떻게 볼 것인가는 모터 냉각 구조에 따라 달라집니다(research.md §8 Q2). 현재 −10 K 는 게이트 정합성을 위한 공학적 판단이며, 실제 대상 압축기의 냉각 방식 확인이 필요합니다 | −10 K |
| C8 | 스크롤 프리셋 효율 계수 (a=0.400/0.200/−0.0320 등)는 문헌 일반형 기반 가정값입니다. 실측 데이터가 있으면 교체 권장 | SCROLL_DEFAULT |
| C9 | 참조 백엔드의 Wagner 포화압력 계수는 문헌 포화표에 자체 적합한 값입니다(잔차 R32 0.79 %, R410A 0.05 %). CoolProp 설치 시 사용되지 않습니다 | — |

---

> ✅ **Phase 0~5 구현 완료. 전 테스트 통과 후 UI 검토 요청 단계입니다.**
