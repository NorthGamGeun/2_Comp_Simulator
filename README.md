# 압축기 가동 타당성 판별 시뮬레이터 (compsim)

주어진 냉매·냉동 사이클 조건에서 요구되는 **기계적 부하 토크**를, 압축기 구동용
PMSM 이 전압/전류 제한 및 열적 한계 내에서 실제로 낼 수 있는지 사전 검증한다.

- 설계 개념 정리: [`research.md`](research.md)
- 구현 계획서: [`plan.md`](plan.md)

## 설치

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[all]"
```

`CoolProp` 은 **설계 판단에 필수**다. 설치되어 있지 않으면 근사 백엔드
(`ReferenceGasBackend`, 오차 약 ±10 %)로 폴백하며, 결과에 항상
`REFERENCE_BACKEND_IN_USE` 경고가 붙는다.

```bash
pip install CoolProp                       # 또는
conda install -c conda-forge coolprop
python tools/pin_golden.py --backend coolprop   # CoolProp 골든 값 생성 (설치 후 1회)
```

## 실행

```bash
# GUI
python -m compsim.ui

# CLI
python -m compsim.cli --demo
python -m compsim.cli --demo --rpm 12000 --sweep 2000:16000:1000 --max-speed
python -m compsim.cli --demo --show-motor          # Ke ↔ λpm 환산 확인
python -m compsim.cli --demo --refrigerant R290    # R32 | R410A | R290
python -m compsim.cli --config examples/case_r32_standard.json --json
```

종료 코드: `0` 동작 가능 / `2` 동작 불가.

## 테스트

```bash
pytest -q                     # 정상 환경
python run_tests.py           # pytest 미설치 환경용 폴백 러너
python /path/to/cov.py        # 커버리지 (아래 참고)
```

## 구조

```
src/compsim/
  units.py          SI 규약 · 단위 변환 (이 모듈 밖에서 kPa/rpm/mH 를 다루지 않음)
  models.py         전 계층 공용 frozen dataclass
  numerics.py       brentq / 황금분할 (scipy 대체, 의존성 최소화)
  thermo/           냉매 물성 · 효율 상관식 · 사이클 → T_load
  motor/            PMSM dq 모델 · 구속 조건 기하 · MTPA/약계자
  feasibility/      열적 게이트 · 판정 오케스트레이션
  ui/               adapters·viewmodel (PyQt 무의존) + main_window (PyQt)
```

### 파이프라인

```
CycleSpec + CompressorSpec ──[thermo]──▶ LoadPoint(T_load, ω_e)
LoadPoint + MotorSpec      ──[motor]───▶ OperatingPoint(i_d, i_q, v_d, v_q)
전부                       ──[feasibility]──▶ Verdict
```

두 도메인은 `(T_load, ω_m)` 2-튜플로만 통신한다. 단방향이므로 반복 수렴이 없고,
각 도메인을 독립적으로 테스트할 수 있다.

## 핵심 규약 (어기면 조용히 틀린다)

| 항목 | 규약 |
|---|---|
| dq 변환 | **amplitude-invariant (peak)** — 토크식 계수 3/2, `i_max`/`v_max` 는 peak |
| `p` | **극쌍수(pole pairs)**. 8극 모터는 `p=4` |
| $L_d$, $L_q$, $R_s$ | **상(phase)** 값. LCR 선간 측정값이면 2로 나눌 것 |
| $K_e$ | **Vrms/krpm, 선간(line-to-line) 기본**. 상 기준과 √3 배 차이 |
| 단위 | 코어는 전량 SI. 변환은 `units.py` 와 `ui/adapters.py` 에서만 |
| 전압 한계 | 판정은 `Rs` 포함 정확식, 시각화는 `Rs` 무시 근사 타원 |

## 지원 냉매

| 냉매 | 비열비 γ | 토출 온도 | 체적 능력 | 안전 등급 |
|---|---|---|---|---|
| R32 | 1.29 | 높음 (열적 게이트 주의) | 높음 | A2L |
| R410A | 1.19 | 중간 | 가장 높음 | A1 |
| R290 | 1.13 | **가장 낮음** | **가장 낮음 (R32 의 약 절반)** | **A3 가연성** |

R290 으로 동등 냉방 능력을 내려면 행정체적을 약 1.9 배로 키워야 한다.

> ⚠️ R290(프로판)은 A3 등급 가연성 냉매다. 본 도구는 열역학·전자기 타당성만
> 판별하며 충전량 제한(IEC 60335-2-40) 등 안전 규격은 다루지 않는다.

데이터시트 전류가 RMS 기준이면 UI 의 "전류 정격이 RMS 기준" 체크박스를 켜거나
`units.peak_from_rms()` 로 변환해 주입한다.

모터 자속은 **역기전력 상수 $K_e$ [Vrms/krpm]** 로 입력하며, 쇄교자속 $\lambda_{pm}$ 은
프로그램이 환산해 표시한다. 환산 결과는 `--show-motor` 로 확인한다.

```bash
python -m compsim.cli --demo --show-motor
```

## 검증 구조

정확성과 회귀 감지를 분리한다 — 이 구분이 무너지면 "틀린 값이 고정되어 영원히
통과하는" 함정에 빠진다.

| 목적 | 수단 | 허용오차 |
|---|---|---|
| 정확성 | 독립 경로(리터럴 물성 상수로 테스트가 직접 계산) 교차 검증 | 1-A 10 %, 1-B 5 % |
| 회귀 감지 | `tests/golden/*.json` 고정 값 | 1e-6 |
| 물리 일관성 | 단조성·항등성 등 불변식 (P1~P8) | 해석적 |

`tools/pin_golden.py` 로 골든 값을 갱신한다.

## 한계 (v1 비범위)

과도 응답, 모터 손실의 사이클 되먹임, 크랭크 각도별 순시 토크 리플, FEA 기반
감자 판별, 인젝션/2단 압축, 온도 의존 파라미터는 v1 범위 밖이다. 본 도구는
**스크리닝(screening) 도구**이며 인증 도구가 아니다.
