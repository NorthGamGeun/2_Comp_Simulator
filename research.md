# research.md — 에어컨 압축기 가동 타당성 판별 시뮬레이터

> **문서 목적**: 구현 이전 단계에서 다중 물리(열역학 + 전자기학 + 열적 한계) 도메인의 지배 방정식, 물성 취급 방식, 판별 기준을 MECE하게 정리한다. 본 문서는 **개념 정의서**이며, 실행 계획은 `plan.md`에 분리 기술한다.
>
> **상태**: 검토 대기 (v0.1)
> **작성일**: 2026-08-03

---

## 0. 기호 및 단위 규약 (Notation & Units)

내부 연산은 **전량 SI 기본 단위**로 수행하고, 단위 변환은 UI 경계(Adapter)에서만 수행한다. 이 규칙을 어기면 열역학(kJ/kg 관행)과 전자기(Wb, H 관행) 사이에서 10³ 배 오차가 발생하기 쉽다.

### 0.1 열역학

| 기호 | 의미 | SI 단위 | UI 표시 단위 |
|---|---|---|---|
| $P_{evap},P_{cond}$ | 증발/응축 압력 | Pa | kPa 또는 bar |
| $P_{suc},P_{dis}$ | 압축기 흡입/토출 압력 | Pa | kPa |
| $T_{suc},T_{dis}$ | 흡입/토출 온도 | K | °C |
| $\Delta T_{sh}$ | 흡입 과열도 (superheat) | K | K |
| $\Delta T_{sc}$ | 응축기 출구 과냉도 (subcool) | K | K |
| $h$ | 비엔탈피 | J/kg | kJ/kg |
| $s$ | 비엔트로피 | J/(kg·K) | kJ/(kg·K) |
| $\rho_{suc}$ | 흡입 상태 밀도 | kg/m³ | kg/m³ |
| $\dot m$ | 냉매 질량 유량 | kg/s | kg/h |
| $V_{disp}$ | 압축기 행정체적 | m³/rev | cc/rev |
| $\gamma$ | 비열비 $c_p/c_v$ | – | – |
| $PR$ | 압력비 $P_{dis}/P_{suc}$ | – | – |
| $\eta_{isen},\eta_{vol},\eta_{mech}$ | 등엔트로피/체적/기계 효율 | – | – |

### 0.2 전자기 (PMSM)

| 기호 | 의미 | SI 단위 | UI 표시 단위 |
|---|---|---|---|
| $L_d,L_q$ | d/q축 인덕턴스 | H | mH |
| $\lambda_{pm}$ | 영구자석 쇄교자속 (peak, 상당) | Wb | Wb |
| $p$ | 극쌍수 (pole **pairs**) | – | – |
| $R_s$ | 상 저항 (per-phase) | Ω | Ω |
| $i_d,i_q$ | d/q축 전류 | A (peak) | A |
| $v_d,v_q$ | d/q축 전압 | V (peak) | V |
| $i_{max}$ | 최대 허용 전류 벡터 크기 | A (peak) | A |
| $V_{dc}$ | 인버터 직류단 전압 | V | V |
| $v_{max}$ | 상전압 peak 한계 | V | V |
| $\omega_e$ | 전기 각속도 | rad/s | – |
| $\omega_m$ | 기계 각속도 | rad/s | rpm |
| $N$ | 압축기 회전수 | rev/s | rpm |
| $T_{load},T_{em}$ | 부하/전자기 토크 | N·m | N·m |

> ⚠️ **함정 1 — peak vs RMS**: 본 프로젝트는 **peak(진폭) 기준 dq 변환(amplitude-invariant)** 을 채택한다. 이 경우 토크식이 $T=\frac{3}{2}p[\cdots]$ 형태가 되며, $i_{max}$ 도 peak 값이다. 데이터시트가 RMS 기준이면 $\sqrt2$ 배 변환이 필요하다. 이 규약을 코드 전역에 단일 상수로 못박아야 한다.
>
> ⚠️ **함정 2 — pole vs pole pair**: $p$ 는 극쌍수(pole pairs)로 통일. 8극 모터는 $p=4$.

---

## 1. 도메인 A — 냉동 사이클 열역학

### 1.1 왜 이상기체 근사가 부적합한가

냉매 압축은 임계점 근방의 고밀도 증기 영역에서 일어난다.

- R32: $T_c = 78.1\,^\circ\mathrm{C}$, $P_c = 5.78\ \mathrm{MPa}$
- R410A: $T_c \approx 71.3\,^\circ\mathrm{C}$, $P_c \approx 4.90\ \mathrm{MPa}$ (의사순수 혼합물)

응축 온도 45 °C, 응축 압력 ≈ 2.8 MPa 는 **환산 압력 $P_r = P/P_c \approx 0.49$**, **환산 온도 $T_r \approx 0.90$** 에 해당한다. 이 영역의 압축인자는 $Z \approx 0.7\text{–}0.9$ 로, 이상기체 가정 시 밀도(→ 질량 유량)에서 10–30 %, 압축 일에서 유사 규모의 오차가 발생한다. 따라서 **CoolProp 의 Helmholtz 자유에너지 기반 상태방정식(HEOS)** 을 사용한다.

- R32: 순수 물질(difluoromethane) → HEOS 직접 사용, 신뢰도 높음.
- R410A: R32/R125 = 50/50 wt% **근공비(near-azeotropic)** 혼합물. CoolProp 의 `REFPROP::R410A` 대신 내장 의사순수 유체 `R410A` 를 사용하면 온도 글라이드(<0.2 K)를 무시하는 대신 계산이 빠르고 안정적이다. 본 프로젝트 정확도 요구(5 %) 에서 충분하다.

### 1.2 압축기 상태점 정의

사이클 4점을 다음과 같이 정의한다.

| 점 | 위치 | 결정 방식 |
|---|---|---|
| 1 | 압축기 흡입 | $P_1 = P_{evap} - \Delta P_{suc}$, $T_1 = T_{sat}(P_{evap}) + \Delta T_{sh}$ |
| 2s | 등엔트로피 토출 | $P_2$, $s_{2s} = s_1$ |
| 2 | 실제 토출 | $P_2$, $h_2 = h_1 + \dfrac{h_{2s}-h_1}{\eta_{isen}}$ |
| 3 | 응축기 출구 | $P_2$, $T_3 = T_{sat}(P_2) - \Delta T_{sc}$ |

$P_2 = P_{cond} + \Delta P_{dis}$. 배관 압력손실 $\Delta P$ 는 기본 0 으로 두되 입력 가능하게 둔다.

### 1.3 등엔트로피 압축 일

$$w_{isen} = h_{2s} - h_1 = h(P_2, s_1) - h(P_1, T_1)\quad [\mathrm{J/kg}]$$

실제 지시 일(indicated work) 및 샤프트 동력:

$$w_{act} = \frac{w_{isen}}{\eta_{isen}},\qquad
P_{ind} = \dot m\, w_{act},\qquad
P_{shaft} = \frac{P_{ind}}{\eta_{mech}}$$

$\eta_{mech}$ 는 베어링 마찰·오일 점성 손실을 포괄한다. **모터 축이 실제로 감당해야 하는 것은 $P_{shaft}$ 이며, 전기 입력 동력이 아니다.** 모터 동손/철손은 도메인 B에서 별도 처리한다(이중 계상 금지).

### 1.4 효율 상관식 (사용자 선택: 압력비 다항식)

스크롤/로터리 압축기 문헌의 일반형을 채택한다.

$$\eta_{isen}(PR) = a_0 + a_1 PR + a_2 PR^2$$
$$\eta_{vol}(PR) = b_0 - b_1 (PR^{1/\kappa} - 1)$$
$$\eta_{mech}(N) = c_0 + c_1 N + c_2 N^2$$

- $\eta_{vol}$ 식은 재팽창(re-expansion) 물리에서 유도된다: 클리어런스 체적비 $C$ 에 대해 $\eta_{vol} = 1 - C\,(PR^{1/\kappa} - 1)$. 즉 $b_0 \approx 1$, $b_1 \approx C$ (스크롤 0.01–0.03, 로터리 0.02–0.05), $\kappa \approx \gamma$.
- 계수는 **UI에서 조정 가능한 파라미터**로 노출하고, 코드에는 스크롤/로터리 기본 프리셋을 둔다.
- $\eta_{isen}$ 은 설계 압력비 부근에서 최대(0.65–0.75)이고 $PR$ 이 커지면 감소하는 아래로 볼록한 형상 → $a_2 < 0$.
- **가드레일**: 모든 효율을 $(0, 1]$ 로 클램프하고, 외삽 구간(예: $PR<1.2$ 또는 $PR>8$)은 경고 플래그를 세운다.

### 1.5 회전수 ↔ 질량 유량 (사용자 선택: 양방향 모드)

**Speed-driven 모드** (인버터 제어 관점, 기본값):
$$\dot m = \rho_{suc}\, V_{disp}\, N\, \eta_{vol}(PR)$$

**Flow-driven 모드** (사이클 설계 관점):
$$N = \frac{\dot m}{\rho_{suc}\, V_{disp}\, \eta_{vol}(PR)}$$

두 모드는 **동일한 하나의 방정식의 서로 다른 미지수 풀이**이므로, 코드에서는 단일 관계식 함수 하나와 얇은 해석기(solver) 두 개로 구현해야 한다(중복 로직 금지). $\eta_{vol}$ 이 $PR$ 만의 함수이고 $PR$ 은 압력 입력에서 직접 정해지므로, 두 모드 모두 **반복 없이 닫힌 형태(closed-form)로 풀린다**. (추후 $\eta_{vol}$ 을 $N$ 의 함수로 확장하면 고정점 반복이 필요해진다 — 인터페이스를 미리 그렇게 열어둔다.)

### 1.6 R32 vs R410A — 토출 온도 상승의 물리

R32 의 토출 온도가 높은 이유는 복합적이다.

1. **비열비 $\gamma$**: R32 $\approx 1.25\text{–}1.30$, R410A $\approx 1.15\text{–}1.20$. 등엔트로피 온도 상승 $T_{2s}/T_1 \approx PR^{(\gamma-1)/\gamma}$ 에서 지수가 커진다.
2. **몰 질량**: R32 52.0 g/mol vs R410A 72.6 g/mol → 비기체상수 $R$ 이 1.4배 → 단위 질량당 압축 일이 크다.
3. **낮은 $c_p$ 대비 큰 일**: 동일 압력비에서 엔탈피 상승분이 온도 상승으로 더 크게 전환된다.

**개략 검산** (이상기체 + 압축인자 보정, 증발 7 °C / 응축 45 °C, 과열 5 K):

| 냉매 | $PR$ | $\rho_{suc}$ [kg/m³] | $\Delta h_{isen}$ [kJ/kg] | $T_{2s}$ [°C] |
|---|---|---|---|---|
| R32 | ≈ 2.80 | ≈ 25 | ≈ 46 | ≈ 86 |
| R410A | ≈ 2.75 | ≈ 35 | ≈ 31 | ≈ 62 |

> 위 표는 **차수(order-of-magnitude) 감각용 근사값**이며, 실제 구현은 CoolProp 값을 사용한다. 실제 토출 온도는 $\eta_{isen}$ 손실이 열로 전환되어 위 등엔트로피 값보다 **더 높다** ($h_2 > h_{2s}$).
>
> ⚠️ 즉 R32는 동일 사이클에서 토출 온도가 약 20 K 이상 높고, 이것이 §3의 열적 한계 판별을 실제로 트리거하는 지배 요인이다. 반면 **단위 질량당 일이 크므로 동일 냉방 능력에 필요한 $\dot m$ 은 작다** — 토크는 이 두 효과의 상쇄로 결정되므로 직관이 아닌 계산이 필요하다.

### 1.7 부하 토크 — 두 도메인의 결합점

$$\boxed{\;T_{load} = \frac{P_{shaft}}{\omega_m} = \frac{\dot m \cdot w_{isen}}{\eta_{isen}\,\eta_{mech}\;\cdot\;2\pi N}\;}$$

여기서 $\omega_m = 2\pi N$ (rad/s), $N$ 은 rev/s.

이 식이 **열역학 도메인의 유일한 출력이자 전자기 도메인의 유일한 입력**이다. 인터페이스를 이 한 점으로 좁히는 것이 아키텍처의 핵심 — 두 도메인은 `(T_load, ω_m)` 튜플로만 대화한다.

Speed-driven 모드에서 §1.5를 대입하면:

$$T_{load} = \frac{\rho_{suc} V_{disp} \eta_{vol} \, w_{isen}}{2\pi\, \eta_{isen} \eta_{mech}}$$

> 🔑 **중요한 물리적 통찰**: $N$ 이 소거된다. 즉 이상적으로 **부하 토크는 회전수와 무관하고 압력비·흡입 상태에만 의존**한다(용적형 압축기의 특성). 실제로는 $\eta_{vol}, \eta_{mech}$ 의 $N$ 의존성 때문에 약한 속도 의존성이 남는다. 이 성질 덕분에 dq 평면에서 **부하 토크 곡선은 속도가 변해도 거의 고정**이고, **전압 제한 타원만 속도에 따라 축소**된다 → 검증 조건 2의 물리적 근거가 된다.

---

## 2. 도메인 B — PMSM 전자기학

### 2.1 정상상태 dq 모델 (rotor-flux 기준 프레임)

$$v_d = R_s i_d - \omega_e L_q i_q$$
$$v_q = R_s i_q + \omega_e (L_d i_d + \lambda_{pm})$$
$$\omega_e = p\,\omega_m$$

정상상태이므로 미분항 $L\,di/dt = 0$.

### 2.2 토크 방정식

$$T_{em} = \frac{3}{2}p\left[\lambda_{pm} i_q + (L_d - L_q) i_d i_q\right]$$

- 제1항: 마그넷 토크
- 제2항: 릴럭턴스 토크. IPMSM 은 $L_q > L_d$ 이므로 $(L_d - L_q) < 0$ → **$i_d < 0$ 일 때 릴럭턴스 토크가 양(+)** 이 되어 토크에 기여한다. 이것이 MTPA 가 $i_d<0$ 을 선택하는 이유이며, 약계자와 방향이 일치한다.
- SPMSM 은 $L_d \approx L_q$ → 릴럭턴스 항 소멸, MTPA 는 $i_d = 0$.

**요구 조건**: 정상상태 동작점에서 $T_{em} = T_{load}$ (관성 토크 0).

### 2.3 구속 조건 1 — 전류 한계 원

$$i_d^2 + i_q^2 \le i_{max}^2$$

$i_{max}$ 는 인버터 소자 정격과 모터 권선 열 정격 중 **작은 값**으로 결정한다. dq 평면에서 원점 중심 반경 $i_{max}$ 의 원.

### 2.4 구속 조건 2 — 전압 한계 타원

$$v_d^2 + v_q^2 \le v_{max}^2$$

$R_s$ 항을 무시한 근사형(고속 영역에서 타당):

$$\left(L_d i_d + \lambda_{pm}\right)^2 + \left(L_q i_q\right)^2 \le \left(\frac{v_{max}}{\omega_e}\right)^2$$

이는 중심 $\left(-\dfrac{\lambda_{pm}}{L_d},\,0\right)$, 반축 $\dfrac{v_{max}}{\omega_e L_d}$ 및 $\dfrac{v_{max}}{\omega_e L_q}$ 인 **타원**이다.

> 🔑 **핵심**: $\omega_e$ 가 커지면 반축이 $1/\omega_e$ 로 축소 → 타원이 중심점 $(-\lambda_{pm}/L_d, 0)$ 으로 수축한다. 고속에서 동작점이 타원 밖으로 밀려나는 것이 "동작 불가(Voltage Limit)" 의 물리적 실체다.
>
> **구현 판단**: 시각화는 위 근사형(깔끔한 타원)을 그리되, **판정은 $R_s$ 를 포함한 정확식**으로 수행한다. 저속·고토크에서 $R_s i$ 강하가 무시 못 할 수준이기 때문이다. 두 결과가 다를 수 있으므로 UI에 "판정 기준: 저항 포함" 을 명시한다.

### 2.5 전압 한계의 정의 — $v_{max}$

| 변조 방식 | $v_{max}$ (상전압 peak) | 비고 |
|---|---|---|
| SPWM (선형 영역) | $V_{dc}/2$ | 보수적 |
| SVPWM (선형 영역) | $V_{dc}/\sqrt{3}$ | **기본값 채택** |
| 과변조/6-step | 최대 $2V_{dc}/\pi$ | 고조파 급증, 예외 처리 |

추가로 데드타임·소자 전압강하 마진 $k_{margin}\approx 0.90\text{–}0.95$ 를 곱한다:
$$v_{max} = k_{margin}\cdot \frac{V_{dc}}{\sqrt3}$$

### 2.6 전류 지령 궤적 — MTPA 및 약계자

**(a) MTPA (Maximum Torque Per Ampere)**

$T_{em}$ 을 고정하고 $|i|$ 를 최소화하는 라그랑주 조건에서:

$$i_d = \frac{\lambda_{pm}}{2(L_q - L_d)} - \sqrt{\frac{\lambda_{pm}^2}{4(L_q-L_d)^2} + i_q^2}$$

($L_d = L_q$ 인 SPMSM 은 특이점 → $i_d=0$ 으로 분기 처리 필수.)

**(b) 해법 전략 — 3단계 계층**

목표 토크 $T_{load}$ 에 대해:

1. **MTPA 후보 계산**: 위 관계식 + 토크식을 연립하여 $(i_d^{mtpa}, i_q^{mtpa})$ 를 구한다. 스칼라 방정식이므로 Brent 법 등 1차원 求根으로 안정적으로 풀린다.
2. **전류 한계 검사**: $|i^{mtpa}| > i_{max}$ 이면 → **`CURRENT_LIMIT` 불가**. (전류를 더 흘려도 토크가 부족)
3. **전압 한계 검사**: MTPA 점이 타원 밖이면 → **약계자 영역**. 전압 타원과 상수 토크 쌍곡선의 교점 중 $|i|$ 최소인 점을 찾는다.
   - 해가 존재하고 전류 원 내부 → **`FEASIBLE (Flux-Weakening)`**
   - 교점이 없거나 전류 원 밖 → **`VOLTAGE_LIMIT` 불가**

**(c) 판정 로직의 엄밀한 형태**

"동작 가능"의 정의는 다음 집합이 공집합이 아닌 것이다:

$$\mathcal{S} = \left\{ (i_d,i_q) \;\middle|\; T_{em}(i_d,i_q) = T_{load} \;\wedge\; |i| \le i_{max} \;\wedge\; |v(i,\omega_e)| \le v_{max} \right\}$$

- $\mathcal{S} \ne \emptyset$ → Feasible, 그 중 $|i|$ 최소점을 동작점으로 채택
- $\mathcal{S} = \emptyset$ → Infeasible. 원인 분해:
  - 상수 토크 곡선 ∩ 전류 원 $=\emptyset$ → `CURRENT_LIMIT`
  - 상수 토크 곡선 ∩ 전류 원 $\ne\emptyset$ 이나 ∩ 전압 타원 $=\emptyset$ → `VOLTAGE_LIMIT`
  - 둘 다 만족하는 부분이 없음(교집합만 빔) → `BOTH_LIMIT`

> 이 집합론적 정의를 채택하면 판정이 **원인별로 MECE하게 분해**되고, UI 경고 문구가 자동으로 결정된다.

**(d) 최대 가용 토크 (여유율 표시용)**

$$T_{max}^{avail}(\omega_e) = \max_{(i_d,i_q)\in \text{원}\cap\text{타원}} T_{em}(i_d,i_q)$$

이를 계산해 두면 UI에 "여유율 = $1 - T_{load}/T_{max}^{avail}$" 을 표시할 수 있고, "불가" 판정 시 **얼마나 부족한지** 정량 제시가 가능하다. 단순 True/False보다 설계 피드백 가치가 훨씬 크다.

### 2.7 검산용 특성 지표

| 지표 | 식 | 의미 |
|---|---|---|
| 특성 전류 | $i_{ch} = \lambda_{pm}/L_d$ | 이 값이 $i_{max}$ 보다 작으면 무한 속도 구동 가능(이상적) |
| 기저 속도 | $\omega_{base} \approx \dfrac{v_{max}}{\sqrt{(L_d i_d + \lambda_{pm})^2 + (L_q i_q)^2}}$ | 이 이상에서 약계자 진입 |
| 무부하 역기전력 | $E_0 = \omega_e \lambda_{pm}$ | $E_0 > v_{max}$ 이면 $i_d<0$ 없이는 원천적으로 불가 |

$E_0 > v_{max}$ 조건은 **연산 전에 즉시 판별 가능한 저비용 사전 검사**이므로 파이프라인 앞단에 배치한다.

---

## 3. 도메인 C — 열적 / 기계적 위험 판별

전자기적으로 가능해도 물리적으로 파괴적일 수 있다. 이 도메인은 **독립적인 게이트**로 작동한다.

### 3.1 토출 온도 한계

| 임계값 | 근거 | 판정 |
|---|---|---|
| $T_{dis} > 125\ ^\circ\mathrm{C}$ | POE/PVE 냉동기유 탄화 및 산화 개시, 슬러지 생성 | `WARN` |
| $T_{dis} > 135\ ^\circ\mathrm{C}$ | 절연 등급(보통 Class B/F) 및 유막 파단 위험 | `FAIL` |

임계값은 상수 하드코딩이 아니라 **설정 가능한 파라미터**로 노출한다(오일 종류·모터 절연 등급에 따라 달라짐).

### 3.2 영구자석 열적 감자 (Thermal Demagnetization)

NdFeB 자석은 온도 상승 시 보자력 $H_{cj}$ 가 감소한다(온도계수 약 $-0.5\ \%/\mathrm{K}$).

- 감자 위험 지표: **자석 온도** $T_{magnet}$ 와 **d축 반작용 자계**의 조합.
- 약계자 제어는 $i_d<0$ 로 자석에 **감자 방향 자계**를 인가하므로, 고온 + 큰 $|i_d|$ 는 비가역 감자를 유발할 수 있다.
- **단순화 모델(v1)**: $T_{magnet} \approx T_{dis} + \Delta T_{offset}$ (토출 가스 냉각형 모터 가정) 로 두고, $T_{magnet} > T_{demag,limit}$ (예: NdFeB N-grade 120 °C, H-grade 140 °C) 이면 `FAIL`.
- **위험 지표 확장(v2)**: $|i_d| > i_{d,demag}(T_{magnet})$ 조건 추가. 유한요소 데이터가 필요하므로 v1에서는 파라미터 입력으로 대체.

> 정직한 한계 명시: 정밀한 감자 판별은 자기회로 FEA 없이는 불가능하다. 본 시뮬레이터는 **스크리닝(screening) 도구**이지 인증 도구가 아니다.

### 3.3 액 압축 (Liquid Slugging)

흡입 냉매에 액상이 혼입되면 비압축성 유체가 실린더에 갇혀 순간 압력이 폭증한다.

- 판정: $\Delta T_{sh} \le 0$ 이거나 흡입 상태의 건도 $x < 1$ → `FAIL (LIQUID_SLUGGING)`
- 실무 마진: $\Delta T_{sh} < 3\ \mathrm{K}$ → `WARN` (센서 오차 및 과도 상태 여유 부족)
- CoolProp 로 흡입점 상태를 조회해 상(phase)을 직접 확인하는 것이 가장 견고하다.

### 3.4 과냉도 및 압력비 위생 검사

| 검사 | 조건 | 판정 |
|---|---|---|
| 과냉도 음수 | $\Delta T_{sc} < 0$ | `FAIL` — 팽창밸브 입구 플래시 가스 |
| 압력 역전 | $P_{dis} \le P_{suc}$ | `FAIL` — 물리적으로 불성립 |
| 압력비 과대 | $PR > 8$ | `WARN` — 단단 압축 범위 이탈, 2단/인젝션 검토 |
| 초임계 | $P_{cond} \ge P_c$ | `FAIL` — 응축 불성립(본 모델 범위 밖) |

---

## 4. 도메인 결합 — 통합 파이프라인

```
[입력]
 ├─ 냉매 종류, P_evap, P_cond, ΔT_sh, ΔT_sc
 ├─ 모드: Speed-driven (N 입력) | Flow-driven (ṁ 입력)
 ├─ 압축기: V_disp, 효율 상관식 계수 (a,b,c)
 └─ 모터: Ld, Lq, λpm, p, Rs, i_max, V_dc, 변조방식

        ↓ Step 0 — 사전 위생 검사 (저비용 즉시 판별)
   P_dis > P_suc ?  |  P_cond < P_c ?  |  ΔT_sh > 0 ?  |  E0 = ω_e·λpm < v_max ?
        ↓
        ↓ Step 1 — 열역학 (CoolProp)
   상태점 1, 2s, 2, 3  →  w_isen  →  η_isen(PR), η_vol(PR), η_mech(N)
   모드에 따라 (N ↔ ṁ) 해석
   →  P_shaft  →  ★ T_load,  ω_m ★     ← 도메인 경계 (이 튜플만 전달)
        ↓
        ↓ Step 2 — 전자기 (MTPA / 약계자)
   ω_e = p·ω_m,  v_max = k·V_dc/√3
   T_load 를 만족하는 최소 전류 해 (i_d*, i_q*) 탐색
        ↓
        ↓ Step 3 — 구속 조건 판별
   |i*| ≤ i_max ?     →  CURRENT_LIMIT
   |v(i*,ω_e)| ≤ v_max ? → VOLTAGE_LIMIT
   여유율 = 1 − T_load / T_max_avail(ω_e)
        ↓
        ↓ Step 4 — 열적 / 기계적 게이트
   T_dis vs 오일 탄화선  |  T_magnet vs 감자선  |  액압축  |  과냉도
        ↓
[출력] Verdict: FEASIBLE / FEASIBLE_WITH_WARNING / INFEASIBLE
       + 위반 원인 리스트 + 동작점 (i_d*, i_q*, v_d, v_q, T_load, 여유율)
        ↓
[시각화] dq 평면: 전류 원 · 전압 타원 · 상수 토크 쌍곡선 · 동작점 마커
```

### 4.1 결합의 수학적 성질

| 성질 | 내용 | 설계적 함의 |
|---|---|---|
| **단방향** | 열역학 → 전자기. 역방향 피드백 없음 | 반복 수렴 루프 불필요, 순수 함수 파이프라인으로 구현 가능 |
| **좁은 인터페이스** | 경계는 $(T_{load}, \omega_m)$ 2-튜플 | 두 도메인을 완전히 독립 테스트 가능 (모킹 용이) |
| **속도 비대칭성** | $T_{load}$ 는 $N$ 에 거의 무관, 전압 타원은 $1/\omega_e$ 로 축소 | 속도 스윕만으로 최대 가동 속도(feasible envelope)를 그릴 수 있음 |

> 단방향성은 **v1의 의도적 단순화**다. 실제로는 모터 손실 → 토출 가스 온도 상승 → 흡입 밀도 변화 → 유량 변화의 약한 되먹임이 존재한다. v2에서 고정점 반복으로 확장 가능하도록 인터페이스만 열어둔다.

---

## 5. 핵심 리스크 및 대응

| # | 리스크 | 영향 | 대응 |
|---|---|---|---|
| R1 | peak/RMS, pole/pole-pair 혼동 | 토크 $\sqrt2$ 또는 2배 오차 — **가장 흔하고 치명적** | 단일 규약 상수 + 단위 테스트로 고정 |
| R2 | CoolProp 미설치/네트워크 차단 | 실행 불가 | 의존성 명시, import 실패 시 명확한 안내 메시지 |
| R3 | R410A 혼합물 물성 오차 | 토크 수 % 오차 | 의사순수 유체 사용 + 5 % 허용오차 설정으로 흡수 |
| R4 | MTPA 해가 SPMSM에서 0/0 특이점 | 크래시 | $|L_q - L_d| < \epsilon$ 분기 처리 |
| R5 | 약계자 교점 탐색 수치 불안정 | 오판정 | 해석적 초기값 + Brent 법 + 수렴 실패 시 명시적 예외 |
| R6 | 효율 상관식 외삽 | 비물리적 결과($\eta>1$) | 클램프 + 외삽 경고 플래그 |
| R7 | 열역학 검증 기준값 부재 | 검증 조건 1 자기순환 논증 위험 | **독립 경로**(폴리트로픽 수계산)로 교차 검증 — §6 참조 |

---

## 6. 검증 전략의 논리적 기반

> 사용자가 요구한 "검증 조건 1"의 함정: CoolProp 로 계산한 값을 CoolProp 로 검증하면 **동어반복**이다.

**해결 — 독립 경로 교차 검증**:

1. **경로 A (구현)**: CoolProp HEOS → $h_{2s} - h_1$
2. **경로 B (독립 검산)**: 실가스 보정 폴리트로픽 수계산
   $$w \approx \frac{\gamma}{\gamma-1} Z R T_1 \left[PR^{(\gamma-1)/\gamma} - 1\right]$$
   §1.6 표의 값이 이 경로의 결과다.
3. 두 경로가 5 % 이내 일치 → 구현 신뢰. **경로 B는 근사이므로 허용오차 자체는 다소 느슨하게(≈10 %) 두고, 별도로 CoolProp 값을 골든 값으로 고정(pin)하여 회귀(regression) 방지용 엄격 테스트(0.1 %)를 추가**하는 이중 구조가 옳다.

**검증 조건 2 (전압 제한 트리거)** 는 §1.7의 통찰 덕분에 깔끔하다: 동일 사이클에서 속도만 $\times$ 10 하면 $T_{load}$ 는 거의 불변인데 전압 타원 반축은 $1/10$ 로 축소 → 반드시 `VOLTAGE_LIMIT` 이 발생해야 한다. 이는 **해석적으로 예측 가능한 결정론적 테스트**이므로 매직 넘버 없이 작성할 수 있다.

---

## 7. 참고 문헌 및 표준 (구현 시 확인 권장)

- W. Leonhard, *Control of Electrical Drives* — dq 모델 및 약계자 이론
- S. Morimoto et al., "Expansion of Operating Limits for PM Motor by Current Vector Control Considering Inverter Capacity", IEEE Trans. IA, 1990 — 전류/전압 제한 원·타원 및 MTPA 원전
- ASHRAE Handbook — Refrigeration, Compressor 장 — 체적/등엔트로피 효율 상관식
- I. Bell et al., "Pure and Pseudo-pure Fluid Thermophysical Property Evaluation and the Open-Source Thermophysical Property Library CoolProp", Ind. Eng. Chem. Res., 2014
- AHRI Standard 540 — 압축기 성능 10계수 다항식 모델 (효율 상관식 대안)
- JRA 4046 / ISO 917 — 압축기 시험 방법 및 토출 온도 제한 관행

---

## 8. 미해결 질문 (검토 시 확인 요청)

| # | 질문 | 기본 가정 |
|---|---|---|
| Q1 | 대상 압축기 타입은 스크롤인가 로터리인가? | 프리셋 둘 다 제공, 기본 스크롤 |
| Q2 | 모터 냉각 방식(토출 가스 냉각 / 흡입 가스 냉각)? | 토출 가스 냉각 가정 ($T_{magnet}\approx T_{dis}+\Delta$) |
| Q3 | $i_{max}$ 는 연속 정격인가 순시 정격인가? | 연속 정격 가정, UI에서 구분 입력 |
| Q4 | 자석 등급(N/H/SH)과 감자 한계 온도? | 파라미터 입력, 기본 120 °C |
| Q5 | 철손·동손을 토크 요구에 반영할 것인가? | v1 미반영 (샤프트 토크만 판정). 반영 시 효율 게이트 별도 추가 |
