# ADR-0003: 공통 protocol 위에 도메인별 백테스트 engine을 둔다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 관련 요구사항 | FR-039~053, FR-063, NFR-023~024 |

## Context

Panel portfolio, multi-leg convergence, LOB queue, mechanical event flow, multi-venue route, derivatives cashflow, filing availability, rolling forecast는 서로 다른 clock, state, accounting, failure model을 가진다. 하나의 bar engine이나 universal event engine에 모두 맞추면 중요한 상태를 버리거나 수많은 conditional branch가 생긴다.

## Decision

공통 `BacktestEngine.validate_inputs/run/explain` protocol과 공통 `ExperimentResult`를 사용하되 계산 engine은 도메인별로 분리한다.

```text
FACTOR_PORTFOLIO       → PanelPortfolioEngine
STAT_ARB               → MultiLegSequentialEngine
MARKET_STRUCTURE_MM    → LOBDiscreteEventEngine
MARKET_STRUCTURE_FLOW  → StructuralEventEngine
CROSS_VENUE            → MultiVenueGraphEngine
DERIVATIVES_RV          → DerivativesCashflowEngine
EVENT_FUNDAMENTAL      → PointInTimeEventEngine
TIME_SERIES            → SequentialForecastEngine
```

공유 가능한 것은 clock interface, money/position primitives, policy ports, artifact writer, metric types다. 체결·accounting state machine은 의미가 같다는 증거 없이 공유하지 않는다.

## 고려한 대안

| 대안 | 기각 이유 |
|---|---|
| 하나의 bar backtester | queue, partial leg, cashflow event, availability time 표현 불가 |
| 하나의 universal discrete-event simulator | 지나친 복잡도와 모든 domain의 lowest-common-denominator API |
| notebook별 독립 구현 | contract, audit, cost/risk, reproducibility가 분산 |
| 외부 backtest framework만 사용 | domain-specific accounting과 holdout/fingerprint 통제 부족 |

## Consequences

- engine별 calibration과 oracle test가 필요해 구현량이 늘어난다.
- 공통 결과·policy·registry 덕분에 validation과 Meta Portfolio는 일관된 입력을 받는다.
- 한 engine의 최적화가 다른 domain correctness를 변경하지 않는다.
- domain 비적용 metric은 null이 아닌 `NOT_APPLICABLE`로 명확해진다.

## 강제 방법

- StrategySpec domain과 engine supported_domains equality
- 8개 engine contract/golden accounting test
- P&L component reconciliation
- 잘못된 engine 선택 fixture의 preflight 실패

## 재검토 조건

공유 event kernel이 두 engine에서 같은 의미·clock·ordering·accounting을 갖는다는 property test가 확보되면 low-level library만 추출한다. engine 자체 통합은 별도 ADR을 요구한다.

