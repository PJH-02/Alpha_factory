# ADR-0008: 도메인 결합은 제한된 interface와 Meta Portfolio에서만 수행한다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 관련 요구사항 | FR-030~032, FR-053, FR-063~066, NFR-015 |

## Context

Factor rank, spread z-score, order-book imbalance, funding rate를 임의로 곱하거나 더하면 후보 공간이 폭발하고 경제적 의미·데이터 시점·engine·실패 기억이 섞인다. 성과가 좋아 보여도 어떤 구성요소가 기여했는지, 어떤 holdout 예산을 사용했는지, 어떤 실행 모델이 맞는지 판별하기 어렵다. 반면 독립 전략의 risk/capital 결합은 필요하다.

## Decision

도메인 간 사용은 다음 interface 중 하나로만 허용한다.

```text
UNIVERSE_FILTER
REGIME_GATE
RISK_NEUTRALIZER
EXECUTION_ADAPTER
CAPITAL_ALLOCATOR
HEDGE_ADAPTER
```

결합은 source/target strategy, 경제 메커니즘, 독립 검증, ablation experiment, frozen parameters, 새 search event를 가진 `CompositionSpec`으로 기록한다. 검증된 전략의 return·exposure·capacity를 자본 수준에서 합치는 작업은 Meta Portfolio가 담당한다. 생성 단계에서 domain payload나 memory를 직접 합치지 않는다.

## 고려한 대안

| 대안 | 기각 이유 |
|---|---|
| arbitrary feature arithmetic | 탐색 폭발, 해석·engine·시점 불일치 |
| domain 결합 전면 금지 | 유용한 universe/regime/hedge/execution 연결과 portfolio allocation 불가 |
| 모든 결합을 하나의 super-lab에서 처리 | domain 경계와 memory 격리 상실 |
| 결과 상관만으로 Meta 결합 | exposure, capacity, tail, venue 집중을 무시 |

## Consequences

- 새로운 결합 유형은 즉시 수식에 넣지 못하고 interface contract와 ADR이 필요하다.
- ablation과 독립 검증 비용이 늘지만 selection bias와 attribution 혼동을 줄인다.
- Meta Portfolio는 alpha를 생성하지 않고 검증된 strategy resource만 입력받는다.
- 각 interface는 최소 정보 DTO를 가져 domain 내부 구현 누출을 줄인다.

## 강제 방법

- CompositionSpec discriminator와 allowlist
- cross-lab import 금지
- source strategy validation status guard
- ablation/frozen/search event FK와 contract test
- illegal arithmetic composition property test

## 재검토 조건

새 결합이 세 개 이상의 독립 연구에서 반복되고 기존 6개 interface로 의미를 보존할 수 없으면 새 interface를 ADR로 추가한다. 과거 성과 개선만으로 추가하지 않는다.
