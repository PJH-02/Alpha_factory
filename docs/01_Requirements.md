# Alpha Foundry 요구사항 명세

| 항목 | 값 |
|---|---|
| 문서 버전 | 1.0.0 |
| 기준일 | 2026-07-11 |
| 상태 | Baseline |
| 제품 릴리스 범위 | MVP, Beta, Production |
| 상위 문서 | [문서 체계](README.md) |

## 1. 목적

Alpha Foundry는 자연어 연구 지시를 도메인에 맞는 연구질문, 반증 가능한 가설, 실행 가능한 전략 명세, 재현 가능한 실험, 통계·실행·위험 검증, 등록 또는 탈락 보고서로 변환하는 도메인 분리형 퀀트 연구 운영체제다.

제품의 목적은 백테스트 수익률을 최대화하는 것이 아니라 다음을 동시에 만족하는 연구 과정을 만드는 것이다.

1. 질문·가설·전략·엔진·검증·기억이 동일한 도메인 경계를 따른다.
2. 데이터 시점, 비용, 체결, 지연시간, 탐색 횟수, 최종 holdout 접근을 감사할 수 있다.
3. LLM은 구조화·생성·해석을 담당하고 수치 계산, 상태 전이, 통과 판정은 검증 가능한 결정론적 절차가 담당한다.
4. 모든 성공과 실패를 계보로 보존해 같은 탐색을 이름만 바꾸어 반복하지 않는다.
5. 실거래 승격은 사람의 명시적 승인 없이는 일어나지 않는다.

## 2. 개발 목표

| 목표 | 성공 상태 |
|---|---|
| 도메인 정확성 | 8개 연구소가 서로 다른 질문·전략 계약과 검증 정책을 사용한다. |
| 연구 자동화 | 자연어 지시에서 감사된 Research Program DAG까지 사람의 수동 JSON 편집 없이 도달한다. |
| 재현성 | 동일 fingerprint의 실험은 허용 수치 오차 안에서 동일 결과와 동일 판정에 도달한다. |
| 누수 방지 | point-in-time, purge/embargo, formation/trading 분리, sealed holdout 정책 위반을 실행 전에 차단한다. |
| 실행 현실성 | 사용자 제공 비용·지연·체결 정책을 그대로 반영하고 gross/net 성과를 분리한다. |
| 위험 통합 | 성과와 함께 노출, turnover, MaxDD, VaR, Expected Shortfall, capacity, 실행 실패를 평가한다. |
| 감사 가능성 | 입력, 모델, prompt hash, 코드·데이터·config 버전, 상태 전이, 탐색 변경점, 사람 승인을 추적한다. |
| 확장성 | 새 연구소·엔진·검증기를 기존 도메인의 내부 코드를 변경하지 않고 계약 등록으로 추가한다. |

## 3. 범위

### 3.1 In Scope

- 질문 지정 모드(`QUESTION_SPECIFIED`)와 영역 지시 모드(`DOMAIN_MANDATE`)
- 개방형 탐색 모드(`OPEN_DISCOVERY`)의 입력·출력 계약, 권한, 예산 관문
- 팩터·포트폴리오, 통계적 상대가치, 마켓메이킹, 구조적 수급, 거래소·시장 간 차익거래, 파생상품 상대가치·캐리, 이벤트·펀더멘털, 시계열 방향성·레짐의 8개 연구소
- 독립 검증을 통과한 전략만 입력받는 메타 포트폴리오 배분기
- 지식 원문, Claim Card, 반대 근거, 방법, 실패 기억, 근거 bundle 관리
- 도메인 라우팅, 질문 생성·감사, Research Program DAG, 가설·전략 컴파일
- 도메인별 백테스트 엔진과 공통 엔진 계약
- 비용, 지연시간, 체결, 슬리피지, 시장충격, 차입비용, funding 정책
- G0~G10 검증 관문, 다중 탐색 원장, validation feedback 제한, sealed holdout
- 전략 등록, 탈락, Paper, Shadow, 사람 승인 상태의 기록
- API, CLI, 비동기 job, event, 보고서, 실험 산출물
- 재현성, 관측성, 접근 통제, 백업·복구, 배포 자동화

### 3.2 Out of Scope

- 주문을 실제 broker 또는 exchange에 전송하는 live execution engine
- LLM이 생성한 자유 형식 코드를 격리·검증 없이 실행하는 기능
- 가장 높은 Sharpe 하나를 자동으로 선택해 실거래로 승격하는 기능
- 모든 전략을 하나의 수식 DSL 또는 하나의 백테스터에 강제로 맞추는 기능
- 최종 holdout 결과를 보고 같은 계보를 수정한 뒤 다시 평가하는 기능
- 허용된 조합 인터페이스를 통하지 않는 도메인 간 신호 산술 결합
- 사용 권한 또는 라이선스가 없는 외부 시장 데이터의 수집·재배포
- 주문관리시스템, broker reconciliation, 자금 이체, custody, 회계 원장
- 사람이 승인했다는 사실을 대신 판단하는 AI 기능

## 4. 시스템 개요

제품의 논리 흐름은 다음과 같다.

```text
Research Mandate
→ Mandate Compilation
→ Domain Routing
→ Evidence Bundle
→ Question Generation and Audit
→ Research Program DAG
→ Hypothesis and Strategy Compilation
→ Static/Data Validation
→ Train/Validation/Robustness Evaluation
→ One-time Sealed Test
→ Register or Reject
→ Paper → Shadow → Human Approval Record
```

MVP는 공통 커널과 8개 도메인 계약을 제공하고 Factor/Portfolio 및 StatArb를 완전한 수직 경로로 실행한다. 나머지 6개 연구소는 MVP에서 유효한 질문·전략 계약, 엔진 계약, synthetic smoke execution까지 제공하며 Beta에서 실데이터 엔진을 완성한다. 단계별 범위는 [개발 계획](03_DevelopmentPlan.md)이 소유한다.

## 5. 사용자 유형과 권한

| 사용자 유형 | 책임 | 허용 작업 | 금지 작업 |
|---|---|---|---|
| Quant Researcher | 연구 지시, config, 데이터 선택, 결과 분석 | mandate 생성, program 실행, train/validation 조회, report 조회 | holdout 재개방, 사람 승인 위조 |
| Research Reviewer | 통계·경제·실행 검토 | 질문·validation 검토, holdout 실행 승인, 전략 등록 심사 | 실행 결과 수정, 원장 삭제 |
| Risk Approver | 위험 한도와 Shadow 승격 검토 | risk gate 승인·거부, Paper/Shadow 전이 승인 | 연구 결과 재계산, AI 주체에 권한 위임 |
| Platform Administrator | capability, 권한, 운영 관리 | 데이터·엔진·config 등록, job 복구, 보존 정책 집행 | 연구 성과 판정 변경 |
| AI Agent | 구조화·생성·감사 보조 | 허용된 schema 안에서 draft 생성, 정성 해석 | sealed 결과 접근, 승인, LIVE 전이, 원장 우회 |
| Service Worker | 결정론적 작업 실행 | 유효한 job claim, 계산, artifact 기록 | 임의 연구 생성, 권한 확대 |
| Auditor | 사후 감사 | lineage, 상태 전이, holdout, 승인, report 읽기 | 연구·설정 변경 |

권한의 기술 계약은 [API 명세](04_API.md), 주체·서비스 경계는 [아키텍처](02_Architecture.md)가 소유한다.

## 6. Use Case

### UC-001 질문 지정 연구

- 주체: Quant Researcher
- 사전 조건: 데이터·config·엔진 capability가 등록돼 있다.
- 기본 흐름: 사용자가 질문을 입력한다 → 시스템이 mandate를 구조화한다 → 도메인을 결정한다 → 근거와 반증 조건을 보완한다 → 질문을 감사한다 → 연구 프로그램을 만든다.
- 성공 결과: 감사 통과 질문과 실행 가능한 DAG가 생성된다.
- 실패 결과: 차단 관문, 누락 capability, 수정 가능한 입력 항목이 구조화된 거부 사유로 반환된다.

### UC-002 영역 지시 연구

- 주체: Quant Researcher, AI Agent
- 기본 흐름: 사용자가 시장·자산·horizon·연구 영역을 지시한다 → 시스템이 서로 다른 생성 정책으로 후보를 만든다 → 독립 심사기가 후보를 감사한다 → Pareto 비지배 후보를 예산 안에서 선택한다.
- 성공 결과: 질문마다 근거, 반대 근거, 가정, falsifier, negative control, 의사결정 효과가 존재한다.

### UC-003 지식 근거 편입

- 주체: Quant Researcher, Platform Administrator
- 기본 흐름: 원문을 등록한다 → 원문 위치가 있는 Claim Card를 만든다 → 모순·한계·적용 범위를 연결한다 → 도메인 namespace에 편입한다.
- 성공 결과: 질문과 보고서에서 원문 위치까지 역추적할 수 있다.

### UC-004 Factor/Portfolio 수직 연구

- 주체: Quant Researcher
- 기본 흐름: point-in-time panel과 factor 질문을 선택한다 → feature lag·중립화·portfolio mapping을 고정한다 → train/walk-forward/robustness/sealed 평가를 실행한다.
- 성공 결과: IC 계열, 포트폴리오 성과, 비용, 노출, capacity, 다중 탐색 보정이 포함된 보고서가 생성된다.

### UC-005 StatArb 수직 연구

- 주체: Quant Researcher
- 기본 흐름: formation 정보만으로 pair/basket을 선택한다 → hedge·spread·trade·multi-leg 정책을 고정한다 → walk-forward와 leg failure stress를 실행한다.
- 성공 결과: 관계 안정성, 정상성, 구조 단절, 실행·비용·unhedged risk를 분리한 보고서가 생성된다.

### UC-006 도메인 전용 연구

- 주체: Quant Researcher
- 기본 흐름: 6개 나머지 연구소 중 하나의 payload를 작성하거나 생성한다 → 해당 연구소만의 StrategySpec과 엔진을 선택한다 → 공통·도메인 검증을 실행한다.
- 성공 결과: 다른 도메인의 엔진 또는 기억을 사용하지 않은 결과가 생성된다.

### UC-007 실험 재현

- 주체: Research Reviewer, Auditor
- 기본 흐름: experiment fingerprint를 선택한다 → 동일한 코드·데이터·config·engine·seed를 복원한다 → 재실행한다.
- 성공 결과: 허용 오차 안의 동일 metric, artifact hash, 판정이 생성된다.

### UC-008 의도된 탈락

- 주체: Research Reviewer
- 기본 흐름: 누수·비용 후 손실·관계 단절·위험 한도 위반 중 하나가 발생한다 → 치명적 gate가 실패한다 → 이후 승격을 차단한다.
- 성공 결과: 실패 gate, 근거, 적용 데이터, 재시도 가능 여부가 있는 Rejection Report가 생성된다.

### UC-009 Sealed holdout 평가

- 주체: Research Reviewer
- 사전 조건: 전략과 모든 파라미터가 고정됐고 G0~G8이 통과했다.
- 기본 흐름: 승인된 계보가 holdout access를 요청한다 → 시스템이 접근 횟수와 계보를 검사한다 → 1회 실행한다 → 생성기에는 결과를 숨긴다.
- 성공 결과: 결과를 본 뒤 같은 계보를 수정하거나 재접근할 수 없다.

### UC-010 전략 등록과 승격

- 주체: Research Reviewer, Risk Approver
- 기본 흐름: 검증 통과 전략을 등록한다 → Paper와 Shadow 상태에서 운영 증거를 축적한다 → 사람이 전이를 승인 또는 거부한다.
- 성공 결과: AI가 승인 주체가 될 수 없고 모든 전이가 감사된다.

### UC-011 운영 복구

- 주체: Platform Administrator, Service Worker
- 기본 흐름: worker가 중단된다 → 미완료 job lease가 만료된다 → 안전한 job만 재시도된다 → 중복 산출물은 멱등 처리된다.
- 성공 결과: 상태·원장·holdout 접근 횟수가 이중 반영되지 않는다.

### UC-012 Capability 변경

- 주체: Platform Administrator
- 기본 흐름: 데이터·엔진·config·compute capability 버전을 등록한다 → snapshot을 만든다 → 새 연구는 고정 snapshot을 참조한다.
- 성공 결과: 이미 실행된 실험의 capability 의미는 변경되지 않는다.

## 7. Functional Requirements

### 7.1 Mandate와 Capability

| ID | 요구사항 |
|---|---|
| FR-001 | 시스템은 `QUESTION_SPECIFIED`, `DOMAIN_MANDATE`, `OPEN_DISCOVERY` autonomy mode를 구분해야 한다. MVP는 앞의 두 모드를 실행하고 세 번째 모드는 계약 검증과 명시적 release-disabled 결과를 제공해야 한다. |
| FR-002 | mandate는 시장, 자산군, venue, instrument, holding horizon, 데이터·지식·config·engine 참조, 후보·trial·revision·holdout 예산, 위험 한도를 구조화해야 한다. |
| FR-003 | mandate 생성 주체, 생성 시각, 자연어 원문 hash, schema version, 상위 mandate 계보를 변경 불가능한 provenance로 남겨야 한다. |
| FR-004 | 시스템은 자연어 mandate를 구조화된 DomainMandate로 컴파일하고 누락·모순 필드를 실행 전에 보고해야 한다. |
| FR-005 | 시스템은 정확히 하나의 primary domain과 0개 이상의 허용된 auxiliary interface를 결정해야 하며 단순 신뢰도 부족을 임의의 다중 도메인으로 처리하면 안 된다. |
| FR-006 | preferred/excluded domain과 시장 범위를 라우팅에 강제하고 제외된 도메인으로의 자동 fallback을 금지해야 한다. |
| FR-007 | 데이터 필드·시점·빈도·point-in-time 품질, 엔진 profile, config model, compute budget을 버전 고정된 Capability Snapshot으로 제공해야 한다. |
| FR-008 | 질문 또는 전략에 필요한 capability가 없으면 job을 만들기 전에 차단하고 부족한 항목을 기계 판독 가능한 목록으로 반환해야 한다. |

### 7.2 Knowledge와 Evidence

| ID | 요구사항 |
|---|---|
| FR-009 | 원문, Source Card, Claim Card, Method Card, Data Card, Failure Card를 서로 다른 개체로 관리해야 한다. |
| FR-010 | Claim Card는 source, page/section 또는 동등한 exact location, 표본 범위, 가정, 한계, 지식 이용 가능 시점을 가져야 한다. |
| FR-011 | 지지·반대·모순 관계를 방향성과 관계 유형을 보존해 연결해야 한다. |
| FR-012 | 질문 생성 입력은 사용된 claim, 반대 claim, 관측 근거, 실패 기억이 고정된 Evidence Bundle이어야 한다. |
| FR-013 | 공유 통계·실행·위험 지식과 도메인 전용 지식·기억을 구분하고 도메인 전용 기억의 교차 사용을 기본 거부해야 한다. |
| FR-014 | Wiki 요약은 탐색 보조물로만 취급하고 최종 근거는 원문과 Claim Card 위치로 역추적해야 한다. |
| FR-015 | knowledge policy의 이용 가능 날짜를 적용해 해당 날짜 이후 지식을 사용하는 retrospective 연구와 당시 이용 가능 지식만 쓰는 historical 연구를 구분해야 한다. |

### 7.3 질문 생성과 감사

| ID | 요구사항 |
|---|---|
| FR-016 | ResearchQuestion은 공통 외피와 `domain` 판별자로 선택되는 정확히 하나의 도메인 payload를 가져야 하며 알 수 없는 필드를 거부해야 한다. |
| FR-017 | 8개 연구소 각각은 독립된 질문 schema, 질문 패턴, 가설 schema, 전략 schema, 탐색 연산자, 검증기, 기억 schema를 소유해야 한다. |
| FR-018 | 영역 지시 모드는 문헌 직접 파생 20%, 문헌 모순 해결 20%, 데이터 이상 20%, 실패 조건 반전 15%, 시장·빈도·자산 이전 10%, 구조 단순화 10%, 사용자 아이디어 확장 5%의 목표 비율로 후보 생성 슬롯을 배정해야 한다. 반올림 잔여 슬롯은 비율 소수부가 큰 순서로 배정해야 한다. |
| FR-019 | 모든 질문은 지지·반대 근거, 가정, falsifier, negative control, 데이터·엔진 요구, 지원·기각 시 결정, 연구 예산, 생성 provenance를 가져야 한다. |
| FR-020 | 도메인, 경제 메커니즘, 통계·식별, 데이터, 실행, 중복, 반대 논거의 7개 독립 심사 결과를 생성해야 한다. |
| FR-021 | 반증 불가, 검증 데이터 없음, 엔진 없음, 시점 미정, 결정 영향 없음, 중복, 불법 도메인 결합, 예산 초과, sealed 데이터 요구, 출처·가정 혼합 중 하나라도 참이면 질문을 차단해야 한다. |
| FR-022 | 절대 관문을 통과한 질문은 근거 품질, 식별 가능성, 반증 가능성, 신규성, 결정 영향, 단순성, 실행 가능성 벡터로 비교하며 하나의 가중 합계만으로 순위를 결정하면 안 된다. |
| FR-023 | 질문·전략의 정규화 hash, 의미 유사도, 계보, 경제 메커니즘을 함께 사용해 이름만 다른 중복 후보를 탐지해야 한다. |
| FR-024 | 후보, parameter set, trial, lineage revision 예산을 실행 전과 각 생성 라운드에서 원자적으로 차감해야 한다. |
| FR-025 | 감사 통과 후보만 Research Program에 들어가며 탈락 후보의 사유와 근거도 보존해야 한다. |

### 7.4 Program, Hypothesis, Strategy

| ID | 요구사항 |
|---|---|
| FR-026 | Research Program은 질문, 데이터 준비, 실험, 검증, 보고 작업을 유향 비순환 그래프로 표현해야 한다. |
| FR-027 | 프로그램은 cycle, 없는 선행 노드, capability 불일치, 예산 초과, sealed 단계 선행 조건 위반을 실행 전에 거부해야 한다. |
| FR-028 | HypothesisSpec은 예상 방향, 경제적·통계적 메커니즘, 반증 기준, alternative explanation, negative control을 구조화해야 한다. |
| FR-029 | StrategySpec은 domain, schema version, 입력 데이터, 신호·상태·행동, 포지션·위험, 실행 policy, engine profile을 명시하고 자유 형식 실행 코드를 포함하면 안 된다. |
| FR-030 | question, hypothesis, strategy, engine, validation, memory의 domain이 일치하지 않으면 compile 또는 run을 차단해야 한다. |
| FR-031 | 도메인 간 결합은 `UNIVERSE_FILTER`, `REGIME_GATE`, `RISK_NEUTRALIZER`, `EXECUTION_ADAPTER`, `CAPITAL_ALLOCATOR`, `HEDGE_ADAPTER` 중 하나의 명시적 interface로만 허용해야 한다. |
| FR-032 | 결합 전략은 각 구성요소의 독립 검증, 결합 메커니즘, ablation, 사전 고정 파라미터, 새 search event를 요구해야 한다. |
| FR-033 | lab·engine·validation plugin은 버전 고정 schema와 capability를 등록하고 호환되지 않는 버전을 동시에 선택하지 못하게 해야 한다. |

### 7.5 Experiment, Config, Engine

| ID | 요구사항 |
|---|---|
| FR-034 | 비용 모델은 static, timestamped schedule, plugin을 지원하고 지연·체결·슬리피지·시장충격·차입·funding은 명시된 허용 policy 중 하나로 선택해야 한다. |
| FR-035 | 사용자가 제공한 유효한 maker/taker, 지연, 체결, 슬리피지, 충격, 차입, funding 값을 시스템이 추정값으로 대체하거나 자동 보정하면 안 된다. |
| FR-036 | 모든 experiment config는 단위, 적용 범위, 결측 처리, fallback 금지 여부를 포함하고 참조 대상 version을 고정해야 한다. |
| FR-037 | strategy, dataset version, engine id/version, config, code revision, seed를 canonical serialization한 hash를 experiment fingerprint로 만들어야 한다. |
| FR-038 | 동일 fingerprint의 완료 결과가 있으면 검증된 cache 결과를 반환하고 구성요소가 하나라도 바뀌면 새 실험으로 기록해야 한다. |
| FR-039 | Engine Registry는 StrategySpec의 domain과 required capability를 모두 만족하는 하나의 engine profile을 선택하거나 명시적 모호성 오류를 반환해야 한다. |
| FR-040 | 모든 engine은 입력 검증, 실행, attribution의 공통 계약을 구현하고 자신의 supported domain과 version을 선언해야 한다. |
| FR-041 | engine은 입력 데이터의 정렬, 시점, timezone, 중복, 결측, point-in-time 경계를 검사하고 미래 정보 가능성을 발견하면 실행을 차단해야 한다. |
| FR-042 | ExperimentResult는 성과, 비용, 노출, 실행, 위험, artifact 참조를 공통 형식으로 반환하고 도메인 비적용 필드는 `not_applicable`로 구분해야 한다. |
| FR-043 | P&L, position, order, fill, diagnostic, model, report artifact는 content hash와 생성 provenance를 가져야 한다. |
| FR-044 | 무작위 절차는 명시 seed를 사용하고 입력 순서, 수치 정밀도, 병렬 축약 정책을 fingerprint와 결과에 기록해야 한다. |

### 7.6 도메인별 실행

| ID | 요구사항 |
|---|---|
| FR-045 | Factor/Portfolio는 point-in-time universe, 상장폐지·corporate action, feature lag, neutralization, score-to-weight, turnover·비용, exposure, capacity, long-only/long-short, benchmark-relative 평가를 지원해야 한다. 검증은 Pearson/Rank IC, ICIR, 분위수 단조성, Fama–MacBeth 또는 횡단면 회귀, block bootstrap, 시기·산업 안정성, 기존 팩터 대비 증분 설명력을 포함해야 한다. |
| FR-046 | StatArb는 formation/trading 분리, pair/basket, rolling hedge, spread state, structural break, pair reselection, partial fill, leg imbalance, borrow·short availability, 중복 노출을 지원해야 한다. 검증은 ADF+KPSS, Engle–Granger/Johansen 중 관계에 맞는 검정, hedge 안정성, ACF/PACF/Ljung–Box, half-life, QQ/AD, random-pair placebo, leg failure와 walk-forward를 포함해야 한다. |
| FR-047 | Market Making은 L2/L3 사건, price-time priority, queue ahead, partial fill, cancel race, market/order/cancel latency, self-order, inventory, markout, synthetic stress를 지원해야 한다. simulator는 spread/depth/arrival/cancel/return/impact/fill-time와 paper fill로 calibration하고 전략은 spread capture, inventory P&L, adverse selection, fill/markout/queue loss, inventory duration, latency P50/P95/P99 stress를 보고해야 한다. |
| FR-048 | Structural Flow는 정보 공개 시점, 예상 거래 시점, event window, 동시 이벤트, 경매, 예상 수급량, impact/reversal, capacity, placebo를 지원해야 한다. 검증은 timestamp audit, pre-trend, 날짜·종목 placebo, sign reversal, CAR·abnormal volume, clustered error, 수급 규모 단조성, impact/reversal 분리와 희소 표본 Bayesian hierarchical 경로를 포함해야 한다. |
| FR-049 | Cross Venue는 instrument equivalence, venue graph, route/cycle, 독립 latency, depth·fee, prepositioned balance, partial route, outage, rate limit, transfer·settlement, counterparty risk를 지원해야 한다. 검증은 causality·stale quote, realized route P&L, 모든 leg 비용, unhedged duration, 잔고 기회비용, outage/rate-limit/default stress, depth capacity와 새 venue·asset·기간 OOS를 포함해야 한다. |
| FR-050 | Derivatives RV는 multiplier, mark/index/trade price, funding·borrow·roll, margin·liquidation, collateral, expiry·exercise, Greeks, hedge rebalance, cashflow attribution을 지원해야 한다. 검증은 no-arbitrage bound, cashflow reconciliation, convergence/carry/hedge P&L 분리, margin·liquidation·collateral shock, Greek drift, gap/jump와 capital efficiency를 포함해야 한다. |
| FR-051 | Event/Fundamental은 fiscal period, filed time, available time, 최초·정정 lineage, 장중·장후 거래 가능 시점, 중복 사건, matched control, CAR, extraction evidence를 지원해야 한다. 검증은 point-in-time, pre-trend, placebo, clustered error, overlap, extraction/source consistency, recent decay, survivorship와 delisting을 포함해야 한다. |
| FR-052 | Time Series는 rolling/expanding fit, overlapping-label purge와 embargo, probabilistic forecast, regime state, calibration, position sizing, sequential cost를 지원해야 한다. 검증은 predictor ADF/KPSS의 적용성, residual ACF/PACF/Ljung–Box, QQ/AD, calibration curve, Brier/log loss, regime 안정성, threshold plateau, turnover와 risk를 포함해야 한다. |
| FR-053 | Meta Portfolio는 독립 검증된 전략의 기대수익 분포, 수익 이력, 노출, capacity, turnover, liquidity, drawdown, tail loss, 자본 요구, venue 집중, 상관 불확실성을 입력받고 전략 생성에는 참여하지 않아야 한다. equal risk contribution, volatility scaling, constrained mean-variance, CVaR optimization, robust covariance, Bayesian shrinkage와 strategy·venue·asset·domain concentration limit을 지원해야 한다. |

### 7.7 Validation과 Holdout

| ID | 요구사항 |
|---|---|
| FR-054 | 검증은 G0 schema/type, G1 evidence, G2 data timing, G3 design, G4 engine/accounting, G5 domain statistics, G6 execution/cost/risk, G7 multiple testing, G8 robustness/regime, G9 sealed test, G10 paper/shadow 순서로 수행해야 한다. |
| FR-055 | 치명적 위반은 다른 점수로 상쇄할 수 없고 해당 gate 이후의 승격 경로를 차단해야 한다. |
| FR-056 | 정상성이 연구 가정인 predictor 또는 residual에는 ADF와 KPSS를 함께 적용하고, 의존성에는 ACF/PACF/Ljung–Box, 분포에는 QQ/Anderson–Darling을 적용해야 한다. |
| FR-057 | 정규성을 입증하지 못한 표본에는 비모수 또는 명시적 heavy-tail 방법을 사용하고 희소·레짐 표본에는 사전분포를 기록한 Bayesian 방법을 우선 평가해야 한다. |
| FR-058 | 시계열 평가는 walk-forward를 기본으로 하고 label overlap에는 purge와 embargo를 적용하며 횡단면·event 표본의 의존 구조에 맞는 bootstrap 또는 clustered error를 사용해야 한다. |
| FR-059 | 질문, 가설, prompt, model, feature, window, threshold, universe, 비용, 기간, portfolio mapping, engine/fill 변경을 각각 search event로 기록해야 한다. |
| FR-060 | validation 결과는 생성기에 정성·구간화 진단만 제공하고 정확한 최적 방향을 노출하지 않으며 sealed 결과는 생성기에 제공하지 않아야 한다. |
| FR-061 | sealed holdout은 lineage당 최대 1회이고 G0~G8 통과, 파라미터 고정, 사람 reviewer 승인 후에만 접근해야 한다. |
| FR-062 | sealed 결과를 조회한 lineage는 질문·가설·전략·config를 수정해 재평가할 수 없으며 수정은 holdout을 공유하지 않는 새 연구 mandate로 시작해야 한다. |
| FR-063 | 모든 전략은 gross/net P&L, fee·slippage·impact·borrow·funding, turnover, gross/net exposure, concentration, MaxDD, VaR, Expected Shortfall, capacity와 도메인별 실행 실패를 보고해야 한다. |
| FR-064 | 후보 수와 선택 절차에 따라 DSR, PBO, White Reality Check 또는 Hansen SPA 중 적용 가능한 검정을 실행하고 적용하지 않은 검정은 사유를 기록해야 한다. |

### 7.8 Ledger, Memory, Registry, Report

| ID | 요구사항 |
|---|---|
| FR-065 | Experiment Ledger는 모든 생성·감사·compile·실험·validation·feedback·holdout·승격 사건과 parent lineage를 append-only로 기록해야 한다. |
| FR-066 | Search Memory는 성공, 실패, 중복, 변경 motif, 표본 context, confidence를 도메인별 schema로 저장해야 한다. |
| FR-067 | Strategy Registry는 `DRAFT`, `REJECTED`, `VALIDATED`, `REGISTERED`, `PAPER`, `SHADOW`, `HUMAN_APPROVED`, `LIVE_RECORDED`, `RETIRED` 상태를 구분해야 한다. `LIVE_RECORDED`는 외부 실행 시스템의 상태를 기록할 뿐 주문을 전송하지 않는다. |
| FR-068 | 통과 보고서와 Rejection Report는 입력 계보, 근거, config, 데이터, 엔진, gate 결과, metric, risk, 비용, 한계, 재현 명령 정보를 포함해야 한다. |
| FR-069 | engine attribution은 gross P&L을 신호·carry·hedge·inventory·execution·cost 중 해당 도메인 성분으로 합계 일치하게 분해해야 한다. |
| FR-070 | `SHADOW → HUMAN_APPROVED`와 `HUMAN_APPROVED → LIVE_RECORDED` 전이는 AI 또는 Service Worker가 요청·승인할 수 없고 사람 주체와 사유를 요구해야 한다. |

### 7.9 Interface와 Job

| ID | 요구사항 |
|---|---|
| FR-071 | API와 CLI는 동일 application service를 호출해 동일한 validation, 권한, 상태 전이, 오류 코드를 사용해야 한다. |
| FR-072 | 질문 생성, program build/run, experiment run, validation, report build와 같은 장시간 작업은 비동기 job으로 수락해야 한다. |
| FR-073 | job은 queued, claimed, running, cancelling, succeeded, failed, cancelled 상태, 진행률, heartbeat, attempt, 오류, 결과 참조를 제공해야 한다. |
| FR-074 | 생성·실행·전이 요청은 멱등 키를 지원해 같은 주체·경로·payload의 재전송이 중복 개체나 holdout 접근을 만들지 않아야 한다. |
| FR-075 | 상태 변경과 job·experiment·validation·strategy 사건을 버전된 event schema로 발행하고 소비자의 중복 처리를 허용해야 한다. |
| FR-076 | 권한 변경, holdout, 승인, config·capability 변경, job 복구, 보존·삭제 작업은 주체·시각·이전값·이후값·사유와 함께 감사 가능해야 한다. |

## 8. Non-Functional Requirements

| ID | 분류 | 요구사항·측정 기준 |
|---|---|---|
| NFR-001 | 결정론 | 동일 fingerprint의 metric은 기준 실행 대비 `rtol ≤ 1e-10`, `atol ≤ 1e-12`를 만족하고 gate 판정과 artifact logical hash가 일치해야 한다. |
| NFR-002 | 인과성 | 공식 leakage 공격 fixture 100%를 실행 전에 차단해야 하며 미래 timestamp를 이용한 계산 경로가 없어야 한다. |
| NFR-003 | 감사성 | 영속 상태 변경, search event, holdout, 사람 승인 기록의 누락률은 0%여야 한다. |
| NFR-004 | 가용성 | Production API의 월간 가용성은 계획 점검을 제외하고 99.5% 이상이어야 한다. |
| NFR-005 | API 성능 | 50 RPS, 25 동시 사용자, Production 기준에서 10 MiB 이하 payload의 metadata read p95는 300 ms, write/enqueue p95는 500 ms 이하여야 한다. 계산 job 시간은 별도 benchmark로 관리한다. |
| NFR-006 | Job 복구 | worker heartbeat 상실 후 90초 안에 lease를 회수하고 재시도 가능한 job을 다시 queued로 만들어야 한다. |
| NFR-007 | 확장성 | API와 worker를 독립적으로 1개에서 8개 replica까지 늘려도 중복 claim과 이중 상태 전이가 없어야 한다. |
| NFR-008 | 인증·권한 | 모든 비공개 요청은 검증된 사람 또는 service identity를 가져야 하며 최소 권한과 role separation을 적용해야 한다. |
| NFR-009 | 비밀 보호 | source, log, report, LLM prompt, artifact metadata에 secret 원문이 존재하는 검출 건수는 0이어야 한다. |
| NFR-010 | 암호화 | Production의 네트워크 전송과 영속 저장은 암호화되고 key rotation과 접근 로그가 제공돼야 한다. |
| NFR-011 | 관측성 | 모든 request와 job은 correlation id, actor, resource id, fingerprint를 연결한 구조화 log·metric·trace를 가져야 한다. |
| NFR-012 | 경보 | queue 지연, job 실패율, lease 회수, holdout 거부, DB 오류, artifact 불일치, API latency SLO 위반에 경보가 발생해야 한다. |
| NFR-013 | 테스트성 | core·orchestration·ledger·holdout·accounting 모듈 branch coverage 85% 이상, 전체 line coverage 80% 이상, 안전 불변식 branch coverage 100%를 만족해야 한다. |
| NFR-014 | 호환성 | 같은 major API와 schema version 안의 변경은 기존 consumer fixture를 깨지 않아야 하며 breaking change는 새 major version을 사용해야 한다. |
| NFR-015 | 유지보수성 | 도메인 간 직접 내부 import와 순환 의존은 0건이어야 하고 새 lab 등록에 기존 lab 파일 수정이 요구되면 안 된다. |
| NFR-016 | 이식성 | 같은 container image와 config contract로 local 개발 환경과 Azure Production 환경에서 기능·schema contract test가 통과해야 한다. |
| NFR-017 | 복구 | metadata RPO는 5분 이하, artifact RPO는 15분 이하, 서비스 RTO는 60분 이하이며 분기별 복구 시험으로 검증해야 한다. |
| NFR-018 | 데이터 무결성 | PK/FK, 상태 전이, idempotency, holdout 횟수, artifact hash 불변식 위반을 영속 계층에서 허용하면 안 된다. |
| NFR-019 | 제어된 AI | 생성 라운드, lineage revision, trial, token, wall-clock budget을 모두 강제하고 종료 조건 없는 agent loop를 허용하면 안 된다. |
| NFR-020 | 자원 격리 | job별 CPU, memory, wall-clock, output 크기 한도를 적용하고 초과 job은 다른 job 또는 API를 중단시키지 않아야 한다. |
| NFR-021 | 문서 일치 | 문서 링크, FR/NFR/AC ID, OpenAPI snapshot, JSON Schema, migration head, event schema drift 검사 실패 시 main merge를 차단해야 한다. |
| NFR-022 | 시간 정확성 | 영속·인터페이스 timestamp는 UTC offset을 포함하고 naive datetime 입력을 100% 거부해야 한다. |
| NFR-023 | 수치 정확성 | 통화·수량·가격은 계약별 precision을 보존하며 비용·cashflow reconciliation 오차는 최소 통화 단위의 절반을 넘으면 실패해야 한다. |
| NFR-024 | 설명 가능성 | 모든 최종 gate 판정은 입력 근거, 계산 artifact, rule version, 임계값을 연결해야 하며 LLM의 설명만으로 판정할 수 없다. |
| NFR-025 | 보고서 접근성 | 모든 보고서는 기계 판독 JSON과 사람이 읽는 Markdown을 함께 제공하고 표·수식·시간·단위가 동일해야 한다. |

## 9. Constraints

1. MVP 달력 기간은 연속 4개 개발일이며 생산급 전체 범위의 완료를 의미하지 않는다.
2. MVP의 완전 수직 엔진은 Factor/Portfolio와 StatArb 두 개다.
3. 나머지 6개 연구소는 MVP에서 계약·schema·synthetic smoke path를 완료하고 Beta milestone에 따라 실데이터 기능을 완성한다.
4. 모든 모델·데이터·문헌은 사용자가 보유한 라이선스와 접근 권한 범위에서만 사용한다.
5. 비용·지연·체결 입력은 사용자의 유효한 config가 권위값이며 시스템이 현실성 추정치로 덮어쓰지 않는다.
6. 연구 시스템은 외부 live execution system에 주문을 보내지 않는다.
7. sealed holdout 정책은 관리자도 과거 접근 기록을 삭제해 초기화할 수 없다.
8. API·DB·engine·validation contract 변경은 문서와 테스트를 같은 변경 집합에서 갱신해야 한다.

## 10. Assumptions

1. 사용자는 분석에 필요한 시장 데이터, corporate action, fee/funding/borrow 자료의 합법적 접근권을 보유한다.
2. Dataset Manifest는 데이터 이용 가능 시각, timezone, 수정 정책, survivorship 특성, 품질 flag를 제공한다.
3. 각 실험은 하나의 고정 Capability Snapshot을 사용한다.
4. Research Reviewer와 Risk Approver는 AI Agent와 구분되는 사람 identity다.
5. 외부 LLM 장애 시 이미 compile된 결정론적 실험은 계속 실행할 수 있다.
6. Production reference workload와 hardware는 [테스트 계획](07_TestPlan.md)의 benchmark fixture로 고정한다.
7. 연구 결과는 미래 수익을 보장하지 않으며 등록은 연구 품질 통과를 의미할 뿐 투자 승인을 의미하지 않는다.
8. historical knowledge mode가 지정되지 않으면 `MODERN_KNOWLEDGE_RETROSPECTIVE`로 명시 기록한다.

## 11. Acceptance Criteria

| ID | 인수 조건 | 관련 요구사항 |
|---|---|---|
| AC-001 | 두 입력 모드 각각의 자연어 fixture가 수동 JSON 편집 없이 valid DomainMandate와 primary domain을 만든다. | FR-001~FR-008 |
| AC-002 | 8개 domain별 valid question fixture는 통과하고 payload를 바꿔 끼운 fixture와 extra field fixture는 실패한다. | FR-016, FR-017 |
| AC-003 | 각 domain mandate에서 5개 질문을 생성하고 모든 질문이 근거·반증·negative control·결정 효과·예산을 가진다. | FR-018~FR-025 |
| AC-004 | capability가 없는 질문은 program node가 만들어지기 전에 정확한 누락 목록과 함께 거부된다. | FR-007, FR-008, FR-027 |
| AC-005 | mandate → evidence → questions → audit → 상위 3개 질문 → DAG가 하나의 E2E 실행으로 완료된다. | FR-009~FR-027 |
| AC-006 | Factor 예제가 point-in-time lag, IC, 중립화, 비용·노출·risk를 포함한 최종 보고서까지 실행된다. | FR-035, FR-045, FR-054~FR-064 |
| AC-007 | StatArb 예제가 formation/trading 분리, hedge, 정상성·구조 단절, multi-leg 비용·실패를 포함한 보고서까지 실행된다. | FR-046, FR-054~FR-064 |
| AC-008 | 나머지 6개 도메인에서 question → strategy → 해당 synthetic engine result → domain validation 경로가 각각 실행된다. | FR-047~FR-052 |
| AC-009 | static maker/taker config 값을 입력한 실험의 비용 계산과 fingerprint에 같은 값이 그대로 나타난다. | FR-034~FR-038 |
| AC-010 | 동일 fingerprint를 두 번 실행하면 두 번째 요청이 기존 결과를 반환하고 metric·판정·artifact logical hash가 일치한다. | FR-037, FR-038, FR-044, NFR-001 |
| AC-011 | 미래 가격, 수정 재무정보, 전체 기간 pair 선택, timezone 혼용 fixture가 모두 G2 이전에 차단된다. | FR-041, FR-054, NFR-002 |
| AC-012 | 비용 증가 시 net P&L 증가, fill 초과, domain 불일치, 불법 조합 fixture가 모두 property/contract test에서 실패한다. | FR-030~FR-032, FR-042, FR-063 |
| AC-013 | 같은 lineage의 두 번째 sealed access가 동시 요청에서도 정확히 하나만 성공하고 다른 하나는 거부된다. | FR-061, FR-062, NFR-018 |
| AC-014 | validation과 sealed metric이 Question Generator 입력에서 제외됐음을 data-flow test로 증명한다. | FR-060~FR-062 |
| AC-015 | 의도된 실패 전략이 `REJECTED`로 끝나고 완전한 Rejection Report를 생성한다. | FR-055, FR-067, FR-068 |
| AC-016 | AI identity가 `HUMAN_APPROVED` 또는 `LIVE_RECORDED` 전이를 요청하면 권한 오류가 발생하고 audit event가 남는다. | FR-070, FR-076 |
| AC-017 | worker 강제 종료 후 90초 안에 job이 회수되고 중복 experiment·artifact·ledger event 없이 완료된다. | FR-072~FR-075, NFR-006 |
| AC-018 | API와 CLI로 같은 명령을 실행했을 때 같은 application error code와 resource state가 반환된다. | FR-071 |
| AC-019 | schema·OpenAPI·migration·문서 drift를 의도적으로 만들면 CI가 main merge를 차단한다. | NFR-021 |
| AC-020 | 성능, 복구, 보안, coverage 시험이 NFR-004~NFR-020의 임계값을 모두 충족한다. | NFR-004~NFR-020 |

## 12. 문서 참조와 추적성

| 관심사 | 권위 문서 |
|---|---|
| 컴포넌트, 상태, 흐름, 배포 | [02_Architecture.md](02_Architecture.md) |
| 구현 단계와 작업 패키지 | [03_DevelopmentPlan.md](03_DevelopmentPlan.md) |
| 요청·응답·오류·이벤트 schema | [04_API.md](04_API.md) |
| 테이블·컬럼·키·보존·migration | [05_Database.md](05_Database.md) |
| 구현·리뷰 규칙 | [06_CodingGuidelines.md](06_CodingGuidelines.md) |
| 검증 fixture와 합격 기준 | [07_TestPlan.md](07_TestPlan.md) |
| 설계 선택의 근거 | [ADR](ADR/) |

FR/NFR에서 코드 모듈과 테스트로 이어지는 상세 매핑은 [테스트 계획의 추적성 표](07_TestPlan.md#16-요구사항-추적성)가 소유한다.
