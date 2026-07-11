원 기획서에는 참고문헌 7개가 있으며, 설계 근거·알고리즘 구현·검증 방법을 서로 다른 강도로 지원합니다. 논문 내용을 그대로 구현 사양으로 취급하면 안 되고, 아래처럼 “참조 범위”를 제한하는 것이 좋습니다.

## 참고문헌별 적용 위치

| 적용 부분                | 참고문헌                   | 참조할 핵심 내용                                                             | 구현 시 제한                 |
| -------------------- | ---------------------- | --------------------------------------------------------------------- | ----------------------- |
| Factor 후보 탐색과 실패기억   | AlphaMemo              | 탐색 원장, 부모-자식 계보, AST 차이 기반 edit motif, 실패 패턴 veto                     | 공식형 Factor에만 적용         |
| StatArb pair 탐색      | MTRGL                  | 시간에 따라 변하는 관계를 temporal graph로 표현하고 link prediction으로 pair 후보 생성      | 백테스트·포지션 회계 근거는 아님      |
| 다중 탐색 과적합 검증         | PBO                    | CSCV를 이용해 선택된 백테스트의 과적합 확률 측정                                         | DSR은 별도 논문 필요           |
| Market Making Engine | LOB Simulation Review  | queue priority, hidden order, auction, market impact를 포함한 사건 기반 시뮬레이션 | 특정 시뮬레이터를 그대로 복제할 필요 없음 |
| Market Making 상태·보상  | Market Making via RL   | inventory risk와 비대칭 보상, 사건 기반 상태·행동                                   | latency 근거로 사용하면 안 됨    |
| LLM 질문·가설 생성         | KG-CoI                 | 구조화 지식으로 생성 근거를 제한하고 별도 hallucination 검증 수행                           | 금융 연구 성능을 입증한 논문은 아님    |
| Knowledge Plane      | Vector RAG vs LLM Wiki | 단일 사실 검색과 논문 간 종합에서 서로 다른 검색 구조 사용                                    | 작은 실험이므로 일반 법칙으로 단정 금지  |

---

## 1. AlphaMemo

논문: [AlphaMemo: Structured Search-Process Memory for Self-Evolving Alpha Mining Agents](https://arxiv.org/html/2606.20625v1)

### 논문에서 참조할 내용

AlphaMemo는 최종 Factor 결과만 기억하지 않고 다음 탐색 과정을 구조화해 저장합니다.

* 평가된 Factor를 node로 관리
* 부모 Factor에서 자식 Factor가 만들어진 과정을 edge로 관리
* 부모·자식 AST 차이에서 실제 수정 패턴인 `edit motif` 추출
* 수정 패턴별 성공·실패 결과 축적
* 관측 수가 적거나 결과가 불안정하면 memory를 사용하지 않음
* 성공 기억은 soft preference로만 사용
* 반복적으로 실패한 고신뢰 패턴은 veto 가능
* 모든 시도를 검색 원장에 남기고 final test 구간은 탐색에서 제외

논문은 이 구조를 Formula Alpha Mining에 적용하며, 일반적인 Pair·LOB·Event 전략에 그대로 적용하는 방법은 제시하지 않습니다.

### Alpha Foundry에서 참조할 위치

* `Factor Lab`
* `Search Event Ledger`
* `Failure Memory`
* Factor 후보 revision
* Factor AST canonicalization
* 중복 Factor 탐지
* 탐색 예산 내 후보 선택

### 문서에 넣을 수 있는 표현

> Factor Lab의 반복 탐색은 AlphaMemo의 구조화된 검색 원장을 참고한다. 평가된 Factor를 node로, 부모-자식 수정 관계를 edge로 기록하고 AST 차이에서 수정 motif를 추출한다. 성공 기억은 후보 우선순위에만 사용하고 고신뢰 실패 패턴만 veto할 수 있다.

### 구현 단계

MVP에서는 다음만 반영하는 것이 적절합니다.

* `parent_id`
* `content_hash`
* `search_event`
* `failure_memory`
* 모든 평가 후보 기록

Residual memory, confidence gate, AST edit motif, asymmetric veto의 완전 구현은 Beta 범위가 적절합니다.

---

## 2. MTRGL

논문: [MTRGL: Effective Temporal Correlation Discerning through Multi-modal Temporal Relational Graph Learning](https://arxiv.org/html/2401.14199v2)

### 논문에서 참조할 내용

MTRGL은 Pair Trading 후보 탐색을 temporal graph link prediction 문제로 변환합니다.

* node: 기업, 자산 또는 시장
* node feature: 섹터, 거래량, 시장점유율 같은 이산·연속 정보
* edge: 특정 기간에 관측된 자산 간 관계
* edge event: 시간에 따라 생성·변경되는 상관 관계
* 입력: 가격 시계열과 이산 feature
* memory-based temporal graph network로 과거·현재 관계 상태 유지
* 출력: 향후 관계가 유지되거나 생성될 가능성이 높은 자산 pair

즉, StatArb 탐색 공간은 Factor의 수식 AST가 아니라 시간에 따라 변하는 자산 관계 그래프가 될 수 있다는 근거입니다.

### Alpha Foundry에서 참조할 위치

* `StatArb Lab`
* Pair·basket 후보 생성기
* 관계 상태 저장
* 시간별 pair universe 생성
* Pair selection의 point-in-time 검사

### 문서에 넣을 수 있는 표현

> StatArb Lab은 자산 관계를 정적인 전체 기간 상관계수로 고정하지 않는다. MTRGL을 참고해 자산을 node, 시점별 관계를 edge event로 표현하는 temporal relation model을 후보 생성 방식 중 하나로 지원한다.

### 주의사항

이 논문은 다음 항목의 근거가 아닙니다.

* Pair의 실제 주문 실행
* 여러 pair의 포지션 충돌 해결
* hedge ratio 회계
* 비용·차입·노출 합산
* Multi-Leg Sequential Engine 설계 전체

특히 “같은 종목이 여러 pair에 포함되면 신호를 단순 합산해서는 안 된다”는 합리적인 시스템 설계 판단이지만 MTRGL이 직접 증명한 결론은 아닙니다. 이 부분은 Alpha Foundry 자체 불변식으로 명시해야 합니다.

---

## 3. Probability of Backtest Overfitting

논문: [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

### 논문에서 참조할 내용

이 논문은 일반적인 단일 holdout만으로 투자 백테스트의 과적합을 충분히 판정하기 어렵다고 보고, `CSCV`를 이용해 PBO를 계산하는 방법을 제안합니다.

핵심 입력은 “선택된 최종 전략” 하나가 아니라 탐색 중 평가한 모든 후보의 성과입니다.

적용 흐름은 다음과 같습니다.

1. 같은 연구 계보에서 실행한 모든 후보를 기록한다.
2. 관측 기간을 여러 대칭 조합으로 나눈다.
3. 각 조합에서 In-Sample 최적 후보를 선택한다.
4. 해당 후보의 Out-of-Sample 상대 순위를 측정한다.
5. IS에서 선택된 후보가 OOS에서 열등해지는 빈도로 PBO를 계산한다.

### Alpha Foundry에서 참조할 위치

* `Search Event Ledger`
* `Validation Registry`
* multiple-testing gate
* 계보별 전체 trial 저장
* sealed holdout 이전의 과적합 진단
* Beta의 full multiple-testing suite

### 문서에 넣을 수 있는 표현

> 계보 내 최종 후보만 보존해서는 탐색 과적합을 측정할 수 없다. PBO 계산을 위해 선택되지 않은 후보를 포함한 모든 trial의 OOS 성과와 선택 순서를 탐색 원장에 기록한다.

### 중요한 인용 오류

원 기획서는 다음 문장에서 `[3]` 하나로 DSR과 PBO를 모두 인용합니다.

> DSR은 비정규 수익률과 다중 탐색에 따른 선택 편향을 보정하며, PBO는…

하지만 참고문헌 `[3]`은 PBO와 CSCV 논문입니다. DSR의 직접 근거가 아닙니다.

DSR에는 별도 참고문헌을 추가해야 합니다.

* David H. Bailey, Marcos López de Prado
* *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality*
* [DOI 10.3905/jpm.2014.40.5.094](https://doi.org/10.3905/jpm.2014.40.5.094)

따라서 문서에서는 `PBO`와 `DSR`을 서로 다른 Validation Rule로 관리해야 합니다.

---

## 4. Limit Order Book Simulations: A Review

논문: [Limit Order Book Simulations: A Review](https://arxiv.org/html/2402.17359v1)

### 논문에서 참조할 내용

이 리뷰는 Market Making과 주문장 전략을 bar 기반 백테스터로 처리하면 안 되는 이유를 제공합니다.

주문장 시뮬레이션이 고려해야 하는 대표 요소는 다음과 같습니다.

* 가격-시간 우선순위와 queue position
* order submission, cancellation, partial fill
* hidden order
* open·intraday·close auction
* trading halt
* dark pool
* agent 간 상호작용
* exogenous order에 대한 price impact
* 실제 시장의 stylized facts와 simulator 출력 비교
* simulator parameter sensitivity

논문은 LOB simulator가 전략 훈련과 백테스트에 유용하지만, simulator가 실제 시장을 완전히 대표하지 못하므로 simulator 자체의 편향도 검증해야 한다고 지적합니다.

### Alpha Foundry에서 참조할 위치

* `Market Making Lab`
* `LOB Discrete Event Engine`
* `FillModel`
* `QueueModel`
* `MarketImpactModel`
* simulator calibration test
* Market Making 도메인 검증

### 문서에 넣을 수 있는 표현

> Market Making Engine은 bar 단위 체결을 사용하지 않는다. LOB simulation review에서 분류한 queue priority, hidden liquidity, auction, cancellation, price impact를 독립 정책으로 모델링하고 empirical stylized facts로 simulator를 검증한다.

### ABIDES 직접 인용 권장

원 기획서는 이 리뷰를 통해 ABIDES를 간접 인용하고 있습니다. 메시지 기반 사건 처리와 네트워크 latency가 실제 설계 근거라면 ABIDES 원 논문을 직접 추가하는 편이 정확합니다.

* [ABIDES: Towards High-Fidelity Market Simulation for AI Research](https://arxiv.org/abs/1904.12066)

ABIDES에서 직접 참고할 부분은 다음입니다.

* agent와 exchange 간 message 기반 사건 처리
* individual agent별 configurable network latency
* 다수 agent와 exchange agent 구성
* ITCH/OUCH 형태의 시장 message protocol
* market-impact 실험 구조

---

## 5. Market Making via Reinforcement Learning

논문: [Market Making via Reinforcement Learning](https://arxiv.org/abs/1804.04216)

### 논문에서 참조할 내용

이 논문의 핵심은 “Market Making의 본질적인 위험은 inventory risk”라는 점입니다.

구체적으로 다음을 참고할 수 있습니다.

* 사건이 발생할 때 행동하는 event-driven agent
* bid·ask quote distance와 skew를 행동으로 표현
* inventory 상·하한
* inventory가 한도를 넘으면 중립 방향 주문만 허용
* 단순 PnL reward가 학습 불안정을 만들 수 있음
* inventory risk를 제어하는 비대칭 보상
* queue cancellation 위치를 알 수 없을 때의 확률적 가정
* 단순 benchmark와 비교한 OOS 평가

### Alpha Foundry에서 참조할 위치

* `MarketMakingQuestionPayload`
* inventory policy
* quote action schema
* reward/evaluation metric
* simulator baseline
* inventory hard limit
* queue cancellation policy

### 문서에 넣을 수 있는 표현

> Market Making 전략 평가는 PnL만 사용하지 않는다. Inventory exposure와 quote skew를 독립적으로 기록하고, inventory 한도 위반은 수익성과 무관하게 hard fail로 처리한다.

### 원 기획서의 과도한 주장

원 기획서는 해당 논문이 inventory와 latency를 상태·보상에 명시적으로 포함한다고 서술합니다. 이 논문이 직접적으로 강하게 지원하는 것은 inventory risk, reward 설계, event-driven LOB simulation입니다. Latency 설계 근거는 ABIDES를 사용하는 것이 더 정확합니다.

또한 논문의 RL 정책을 Alpha Foundry의 기본 정책으로 채택할 필요는 없습니다. 구현에서는 RL 정책을 비교 가능한 plugin 중 하나로 두고, deterministic baseline과 먼저 비교해야 합니다.

---

## 6. KG-CoI

논문: [Improving Scientific Hypothesis Generation with Knowledge Grounded Large Language Models](https://arxiv.org/abs/2411.02382)

### 논문에서 참조할 내용

이 논문은 `KG-CoI` 구조를 제안합니다.

* 외부 구조화 지식을 LLM 입력에 포함
* 가설 생성 과정을 `Chain of Ideas`로 구조화
* 생성된 reasoning을 Knowledge Graph와 비교
* 근거 없는 연결이나 hallucination을 별도 module이 검사
* 생성과 검증을 한 번의 자유형 응답으로 합치지 않음

### Alpha Foundry에서 참조할 위치

* `Knowledge Plane`
* `Research Generator`
* `Question Auditor`
* evidence ID
* claim relation
* 생성기와 감사기의 분리
* hallucination reason code

### 문서에 넣을 수 있는 표현

> Research Generator는 Knowledge Plane이 제공한 Claim과 relation만 근거로 후보를 생성한다. 생성 결과는 권위 상태가 아니며, 독립 Question Auditor가 주장별 evidence 연결과 실행 가능성을 검증한다.

### 주의사항

이 논문은 과학 가설 생성 일반 실험이며 퀀트 연구에서의 유효성을 입증한 것은 아닙니다. 따라서 다음은 Alpha Foundry 자체 golden set으로 평가해야 합니다.

* 금융 가설 정확성
* 데이터 가용성 판단
* 시간 누수 탐지
* 반증 가능성
* 실제 StrategySpec 변환 가능성

---

## 7. Vector RAG vs LLM-Compiled Wiki

논문: [Vector RAG vs LLM-Compiled Wiki](https://arxiv.org/html/2605.18490v1)

### 논문에서 참조할 내용

이 연구의 핵심 결론은 Wiki와 Vector RAG 중 하나가 항상 우월하지 않다는 것입니다.

연구 결과는 대략 다음과 같습니다.

* Wiki는 여러 논문의 결과를 연결하는 데 강함
* RAG는 단일 사실 또는 특정 source lookup에 적합
* Wiki가 query 비용 면에서 반드시 저렴하지 않음
* claim 단위 인용 근거성은 Wiki가 더 좋았음
* decomposition 기반 RAG는 비교적 낮은 비용으로 Wiki의 종합 능력 상당 부분을 회복
* 구조화, citation grounding, 비용을 모두 지배하는 방식은 없었음

다만 실험 규모는 24개 논문과 13개 질문으로 작고, LLM judge를 사용한 2026년 preprint입니다. 보편적인 architecture benchmark로 취급하면 안 됩니다.

### Alpha Foundry에서 참조할 위치

* `Knowledge Plane`
* raw source retrieval
* Wiki compilation
* query router
* claim-level citation verification
* Knowledge 비용 계측
* Knowledge A/B test

### 권장 구조

```text
단일 주장·원문 조회
    → Raw Source / Vector Retrieval

여러 논문 비교·충돌·연결
    → Compiled Wiki 또는 Decomposition Retrieval

최종 답변
    → Claim별 Source ID 검증
```

### 문서에 넣을 수 있는 표현

> Knowledge Plane은 Wiki와 raw retrieval 중 하나를 전역 기본값으로 고정하지 않는다. 단일 근거 조회는 raw retrieval을 사용하고, 논문 간 종합은 compiled Wiki 또는 decomposition retrieval을 사용한다. 최종 Claim은 원문 locator로 다시 검증한다.

---

## 추가해야 할 직접 참고문헌

원 기획서의 7개만으로는 두 부분의 인용이 불완전합니다.

### AlphaEval

직접 논문: [AlphaEval: A Comprehensive and Efficient Evaluation Framework for Formula Alpha Mining](https://arxiv.org/abs/2508.13174)

현재 기획서는 AlphaEval 설명에도 `[1] AlphaMemo`를 사용합니다. AlphaMemo가 AlphaEval을 참고문헌으로 언급하기는 하지만 AlphaEval의 직접 근거로는 부족합니다.

AlphaEval에서 참조할 다섯 평가 축은 다음입니다.

1. Predictive power
2. Temporal stability
3. Robustness to perturbation
4. Financial logic
5. Diversity

Alpha Foundry에서는 Factor 후보의 저비용 사전 선별에 사용할 수 있습니다. 다만 AlphaEval은 backtest-free evaluation이므로 최종 포트폴리오 백테스트와 비용·회계 검증을 대체해서는 안 됩니다. 특히 `Financial Logic`은 LLM 평가이므로 hard gate보다는 Reviewer 참고 점수로 사용하는 것이 안전합니다.

### Deflated Sharpe Ratio

PBO와 별도 참고문헌으로 등록해야 합니다.

DSR은 다음 입력을 요구하도록 Validation 계약을 설계해야 합니다.

* 관측 Sharpe
* 전체 trial 수 또는 유효 독립 trial 수
* trial Sharpe 분산
* return skewness
* return kurtosis
* sample length

이를 위해 모든 탐색 trial을 Search Ledger에 남겨야 합니다.

---

## 문서별 최종 배치 권장안

| 문서                      | 넣을 참고문헌                                                      |
| ----------------------- | ------------------------------------------------------------ |
| `01_Requirements.md`    | 논문 세부내용을 넣지 않고 근거 문서만 참조                                     |
| `02_Architecture.md`    | AlphaMemo, MTRGL, LOB Review, KG-CoI, Wiki vs RAG            |
| `03_DevelopmentPlan.md` | AlphaMemo 고급 memory와 실제 graph/LOB engine을 Beta 작업 근거로 사용     |
| `05_Database.md`        | Search Ledger·Failure Memory 구조는 AlphaMemo ADR 참조            |
| `07_TestPlan.md`        | PBO, DSR, AlphaEval, LOB stylized facts, inventory risk test |
| `ADR-0002`              | AlphaMemo와 MTRGL을 이용한 domain schema 분리 근거                    |
| `ADR-0003`              | LOB Review, ABIDES, Market Making RL을 이용한 전용 engine 근거       |
| `ADR-0004`              | KG-CoI를 이용한 LLM 생성·검증 분리 근거                                  |
| `ADR-0006`              | PBO와 DSR을 이용한 계보·전체 trial·sealed holdout 근거                  |

핵심적으로 논문은 “이 구조가 유일한 정답”이라는 근거가 아니라, 도메인별 모델을 분리하고 탐색 기록·검증·LLM 경계를 두어야 한다는 설계 근거로 사용하는 것이 적절합니다.
