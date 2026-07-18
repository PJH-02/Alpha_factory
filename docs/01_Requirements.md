# Alpha Foundry 요구사항

상태: Approved  
기준 릴리스: MVP  
요구사항 키워드 `MUST`, `MUST NOT`, `SHOULD`는 각각 필수, 금지, 권고를 뜻한다.

## 1. 프로젝트 목적

Alpha Foundry는 자연어 연구 지시를 하나의 도메인에 속하는 typed JSON 전략으로 변환하고, 결정론적 compiler·탐색·연구급 수치 엔진·검증을 거쳐 통과 전략만 공개하는 퀀트 연구 시스템이다.

핵심 가치는 높은 백테스트 수치나 optimizer의 baseline 우월성이 아니라 다음 네 가지다.

- claim 수준 근거, 정책, 데이터, 코드의 불변 계보
- LLM 생성과 결정론적 schema 검증·컴파일·판정의 분리
- 유한·결정론적 탐색 및 누수·다중 탐색 편향 통제
- 같은 입력과 고정 버전에서 재현 가능한 사용자 결과

## 2. Scope

### 2.1 In Scope — MVP

- 질문 지정과 영역 지시 `ResearchMandate`, 불변 KnowledgePack/claim, CapabilitySnapshot
- Factor, StatArb, Market Making, Structural Flow, Cross Venue, Derivatives, Event Fundamental, Time Series의 독립 도메인 계약
- 8개 도메인 각각의 typed JSON DSL/AST, code-owned compiler, point-in-time data schema, 연구급 수치 엔진, validation profile, synthetic golden fixture
- finite versioned universe에서 실행하는 bounded deterministic evolutionary search와 append-only candidate/trial lineage
- 명시적 비용·지연·체결·slippage·impact·borrow·funding·회계 정책 및 누락 정책·데이터의 preflight 거절
- purged/embargoed walk-forward, complete-ledger PBO, pre-validation 후 자동 sealed holdout
- SQLite, local artifact, 지속 단일 Worker, CLI·REST, 재현 가능한 HTML/JSON ResearchReport
- `PUBLISHED` 전략과 그 보고서만 조회하는 사용자 결과 경계

연구급 엔진은 명시적 수치 모델과 도메인 불변식을 뜻하며 운영급 시장 재현을 주장하지 않는다.

### 2.2 Out of Scope — MVP

- 무제한 또는 비결정론적 탐색, optimizer의 random/grid baseline 우월성 입증
- 자동 PDF·웹 수집, OCR, embedding/vector DB, LLM 생성 Python·SQL·shell 실행
- 실거래 주문 전송, 자동 운용 승격, 운영급 simulator
- 웹·모바일 UI, 다중 Worker, PostgreSQL, object storage, 멀티테넌시, 외부 공개 API
- crash한 `RUNNING` 작업의 stage 자동 재개
- cross-domain feature·strategy composition 및 Meta Portfolio는 후속 릴리스로 연기

한 StrategySpec은 정확히 하나의 primary domain에 속한다. 도메인 간 조합 package·API·schema와 Lab 내부 구현 공유는 만들지 않는다.

## 3. 시스템 결과 흐름

`ResearchMandate`는 근거와 CapabilitySnapshot을 고정한 generation request로 변환된다. LLM은 allowlist 안의 typed JSON 후보만 반환하고, 코드가 schema와 operator를 검사해 실행 계획을 컴파일한다. SearchRun은 고정된 finite universe를 중복 없이 탐색한다. 후보는 명시적 정책의 해당 도메인 엔진에서 실행되고, 사전 고정된 ValidationProfile의 hard gate·walk-forward·PBO를 통과해야 한다.

pre-validation을 통과하면 시스템은 sealed 데이터 접근 전에 holdout slot을 원자적으로 소비하고 lineage를 닫은 뒤 holdout을 자동 실행한다. 모든 검증을 통과한 revision만 원자적으로 `PUBLISHED`되어 사용자에게 HTML/JSON ResearchReport로 제공된다. 탈락 결과와 내부 계보는 Rejection Registry와 failure memory에만 남는다.

상세 컴포넌트·상태 전이·wire 계약은 [02_Architecture.md](./02_Architecture.md), [04_API.md](./04_API.md), [05_Database.md](./05_Database.md)의 권위에 따른다.

## 4. 사용자와 Use Case

| 사용자 | 책임 | MVP 권한 |
| --- | --- | --- |
| Researcher | 연구 지시 제출, `PUBLISHED` 결과·보고서 조회 | mandate 제출·job 조회·공개 결과 조회 |
| Reviewer | versioned resource와 연구 품질 검토 | KnowledgePack, operator set, ValidationProfile 검토; runtime holdout 승인 권한 없음 |
| Maintainer | 데이터·엔진·설정 등록 및 실패 작업 재제출 | capability/resource 등록, job resubmit |
| AI Developer | 문서 계약 기반 구현 | 코드·테스트 제안; 운영 권위 상태 변경 불가 |

MVP 인증은 신뢰된 단일 조직의 로컬 또는 단일 서버 사용자로 제한한다.

- **UC-001 질문 지정/영역 지시 연구:** Researcher가 단일 도메인 mandate를 제출하면 시스템은 schema·capability를 검증하고 고정된 근거와 snapshot에서 후보를 생성한다.
- **UC-002 결정론적 탐색:** 시스템은 finite universe와 SearchSpec의 seed·operator allowlist만 사용해 후보를 생성·평가하고 모든 trial을 기록한다.
- **UC-003 8개 도메인 실행:** 선택된 StrategySpec은 해당 도메인의 실제 수치 엔진에서 명시 정책과 point-in-time 데이터로 실행된다.
- **UC-004 자동 holdout과 공개:** pre-validation 통과 시 holdout slot을 접근 전 소비하고 자동 평가한 뒤, 통과 revision과 HTML/JSON 보고서만 `PUBLISHED`한다.
- **UC-005 재현과 재시작:** 수락된 generation은 저장 artifact로 재생하며, 재시작 시 persisted `RUNNING` job은 `FAILED`가 되어 사용자가 새 job으로 재제출한다.

## 5. Functional Requirements

세부 field, 오류 및 영속 제약은 API·Database 문서를 중복하지 않는다. 아래 ID가 요구사항과 테스트의 추적 기준이다.

### 5.1 Knowledge, generation, DSL

| ID | 요구사항 |
| --- | --- |
| FR-KNOW-001 | KnowledgePack과 claim은 source, citation, domain, counterevidence, failure memory를 schema로 검증하고 immutable version 및 content hash를 가져야 한다. |
| FR-KNOW-002 | 수정은 새 version을 생성해야 하며, generation은 사용한 claim ID·version·hash를 고정해야 한다. |
| FR-KNOW-003 | 시스템은 결정론적 filter와 전문검색으로 claim을 선택해야 한다. |
| FR-KNOW-004 | ResearchMandate는 mode, 목표, 단일 primary domain, data requirement, budget, 금지 조건을 포함해야 한다. |
| FR-KNOW-005 | CapabilitySnapshot은 datasets, 정책, schema/code version 및 ordered provider/model chain을 고정해야 한다. |
| FR-KNOW-006 | 사용 불가 데이터·엔진·정책은 실행 전에 reason code와 필요한 항목을 반환하며 거절해야 한다. |
| FR-GEN-001 | 같은 KnowledgePack, pinned claims, CapabilitySnapshot, request는 같은 generation request hash를 가져야 한다. |
| FR-GEN-002 | provider/model fallback은 CapabilitySnapshot의 ordinal 순서로만 시도해야 한다. |
| FR-GEN-003 | 각 provider attempt의 ordinal, provider/model, request/response hash, 오류와 선택 여부를 provenance에 보존해야 한다. |
| FR-GEN-004 | LLM은 권위 상태를 변경하거나 sealed 정보·임의 실행 코드를 받거나 실행해서는 안 된다. |
| FR-GEN-005 | 수락된 typed JSON artifact가 있으면 재실행은 provider 호출 없이 그 artifact를 재생해야 한다. |
| FR-DSL-001 | 8개 도메인은 독립적인 discriminator와 typed JSON StrategySpec/AST schema를 소유해야 한다. |
| FR-DSL-002 | StrategySpec은 하나의 primary domain, operator-set version 및 schema hash를 명시해야 한다. |
| FR-DSL-003 | code-owned compiler만 schema와 allowlist operator를 실행 계획으로 변환할 수 있다. |
| FR-DSL-004 | unknown field, 잘못된 AST, allowlist 밖 operator, Python·SQL·shell 등 임의 코드 field는 엔진 실행 전에 거절해야 한다. |
| FR-DSL-005 | Lab은 공통 외피 외의 다른 Lab payload·내부 구현을 import하거나 수용해서는 안 된다. |
| FR-DSL-006 | operator set의 의미 변경은 새 version/hash를 만들고 StrategySpec 및 SearchSpec identity에 반영되어야 한다. |

### 5.2 Finite deterministic search와 ledger

| ID | 요구사항 |
| --- | --- |
| FR-SRCH-001 | SearchRun은 domain·operator set에 맞는 versioned finite candidate universe만 사용해야 한다. |
| FR-SRCH-002 | SearchSpec은 universe, dataset, policy, schema, code, ValidationProfile, seed와 operator schedule을 시작 전에 고정해야 한다. |
| FR-SRCH-003 | mutation/crossover는 등록된 유한 allowlist와 parameter grid 안에서만 후보를 생성해야 한다. |
| FR-SRCH-004 | 후보 traversal은 seed 기반 결정론적 순서여야 하며 run 안에서 후보를 중복 제안하거나 시작해서는 안 된다. |
| FR-SRCH-005 | offspring generation은 시작 전에 고정된 parent-pool snapshot만 사용해야 하며 live ranking을 다시 조회해서는 안 된다. |
| FR-SRCH-006 | hard gate 통과 후보는 domain ValidationProfile의 lexicographic ranking과 고정 tie-break로 정렬해야 한다. |
| FR-SRCH-007 | optimizer의 기능 합격 기준은 유효 후보 생성과 유한 종료이며 baseline 우월성은 요구하지 않는다. |
| FR-SRCH-008 | optimizer가 생성한 각 후보는 독립적으로 walk-forward, PBO, sealed holdout을 모두 통과해야 공개될 수 있다. |
| FR-SRCH-009 | 정상 종료는 quantized improvement의 patience 소진(`PLATEAU`) 또는 universe 소진(`UNIVERSE_EXHAUSTED`)뿐이어야 한다. `max_generations` 같은 generation cap은 SearchSpec이나 정상 종료에 존재해서는 안 되며, 취소·중단은 typed failure다. |
| FR-LEDGER-001 | candidate, parent, mutation/crossover, proposal, trial, rejection, experiment는 append-only lineage/search ledger에 기록되어야 한다. |
| FR-LEDGER-002 | 각 started CandidateTrial은 하나의 terminal outcome을 가져야 하며 trial ledger position은 연속적이어야 한다. |
| FR-LEDGER-003 | 무효·universe 밖·중복 proposal과 선택된 direct fallback은 소비 순서대로 기록해야 한다. |
| FR-LEDGER-004 | PBO 입력은 선택 후보만이 아니라 complete eligible trial ledger여야 한다. |
| FR-LEDGER-005 | ledger 누락, 중복 fold, 비유한 score 또는 불완전 certificate는 `AF-PBO-INCOMPLETE`로 holdout과 publication을 막아야 한다. |
| FR-LEDGER-006 | 재제출은 기존 trial을 변경하지 않는 새 lineage를 생성해야 한다. |

### 5.3 Domain engine과 policy

모든 엔진은 해당 도메인 data schema, point-in-time availability, execution policy, accounting invariant, validation profile, golden fixture를 소유한다. 공통화는 실제로 동일한 primitive에 한정한다.

| ID | 요구사항 |
| --- | --- |
| FR-ENG-001 | Factor는 Panel Portfolio Engine으로 실제 수치 실행해야 한다. |
| FR-ENG-002 | StatArb는 Multi-Leg Sequential Engine으로 실제 수치 실행해야 한다. |
| FR-ENG-003 | Market Making은 inventory·fill·adverse-selection 회계를 포함한 실제 수치 실행해야 한다. |
| FR-ENG-004 | Structural Flow는 event-impact·execution policy를 포함한 실제 수치 실행해야 한다. |
| FR-ENG-005 | Cross Venue는 multi-venue clock·latency·route 정책을 포함한 실제 수치 실행해야 한다. |
| FR-ENG-006 | Derivatives는 cash-flow·funding·borrow·margin 정책을 포함한 실제 수치 실행해야 한다. |
| FR-ENG-007 | Event Fundamental은 point-in-time event availability를 보존하는 실제 수치 실행해야 한다. |
| FR-ENG-008 | Time Series는 walk-forward-compatible 시계열 실행과 누수 방지 정책을 포함한 실제 수치 실행해야 한다. |
| FR-POL-001 | 비용·지연·체결·slippage·impact·borrow·funding·회계 정책은 적용 가능한 경우 명시적으로 versioned resource에 존재해야 한다. |
| FR-POL-002 | 필수 data field, capability 또는 현실 정책이 없으면 시스템은 임의 추정·묵시적 기본값 없이 preflight에서 거절해야 한다. |
| FR-POL-003 | engine 결과는 정책, dataset, config, code 및 schema version/hash를 provenance에 포함해야 한다. |

### 5.4 Validation, holdout, publication

| ID | 요구사항 |
| --- | --- |
| FR-VAL-001 | domain별 ValidationProfile은 SearchRun 전에 version/hash와 함께 고정되어야 한다. |
| FR-VAL-002 | pre-validation은 hard gate와 purged/embargoed walk-forward를 실행해 시간 중첩·future information을 차단해야 한다. |
| FR-VAL-003 | ValidationReport는 gate 결과, metric, threshold, reason과 결정된 validation decision을 보존해야 한다. |
| FR-VAL-004 | 시간 누수, 회계 불일치, schema 위반은 항상 hard fail이어야 한다. |
| FR-PBO-001 | PBO는 frozen profile의 CSCV/fold 정의와 complete ledger certificate에서 계산해야 한다. |
| FR-PBO-002 | eligible, matrix, started, terminal trial 집합과 fold 집합은 certificate의 exact equality를 충족해야 한다. |
| FR-PBO-003 | PBO는 누락 score·fold를 추정하거나 선택 후보만으로 계산해서는 안 된다. |
| FR-PBO-004 | PBO와 pre-validation의 minimum eligible count 미충족은 holdout을 막아야 한다. |
| FR-PBO-005 | PBO input, result와 profile hash는 lineage에 연결되어야 한다. |
| FR-PROFILE-001 | SearchRun 이후 ValidationProfile의 gate, ranking, fold, PBO, patience, holdout·disclosure 정책은 변경할 수 없다. |
| FR-PROFILE-002 | profile의 의미 변경은 새 version/hash를 만들고 새 SearchRun만 사용할 수 있다. |
| FR-PROFILE-003 | profile은 holdout selector와 상세 metric을 generation·search·LLM 입력에 노출해서는 안 된다. |
| FR-HOLD-001 | pre-validation 통과 시 sealed holdout은 Reviewer 승인 없이 자동으로 시작해야 한다. |
| FR-HOLD-002 | sealed data 접근 전에 하나의 transaction이 unused holdout slot을 소비하고 lineage를 닫아야 한다. |
| FR-HOLD-003 | slot 소비 후 성공·실패·crash와 무관하게 같은 lineage는 재시도·재개·재접근할 수 없어야 한다. |
| FR-HOLD-004 | holdout disclosure는 frozen field allowlist의 aggregate decision/metric/threshold로 제한해야 한다. |
| FR-HOLD-005 | disclosure/reporting은 knowledge, generation, search, ranking, PBO 또는 lineage 변경 서비스로 역류해서는 안 된다. |
| FR-PUB-001 | 모든 validation gate와 holdout을 통과한 Strategy revision만 one atomic publication으로 `PUBLISHED`될 수 있다. |
| FR-PUB-002 | Researcher의 strategy 목록, 상세 조회, export와 report는 `PUBLISHED` aggregate만 반환해야 한다. |
| FR-PUB-003 | publication은 HTML/JSON report artifact와 registry entry를 함께 보존하거나 전부 rollback해야 한다. |
| FR-PUB-004 | `PUBLISHED` authority는 이후 실패·재시작으로 demote되지 않아야 한다. |
| FR-REJ-001 | 탈락 전략은 사용자 통과 목록에 나타나지 않고 Rejection Registry와 failure memory에 reason·lineage를 보존해야 한다. |
| FR-REJ-002 | preflight·validation·publication 거절은 typed non-retryable reason을 보존해야 한다. |
| FR-REJ-003 | 내부 rejection 조회는 Researcher의 공개 결과 surface와 분리해야 한다. |

### 5.5 Interface, report, job

| ID | 요구사항 |
| --- | --- |
| FR-IFACE-001 | CLI와 REST는 같은 application service로 mandate, search, job, `PUBLISHED` result/report command를 제공해야 한다. |
| FR-IFACE-002 | 같은 입력은 같은 command fingerprint와 구조화 결과를 만들어야 한다. |
| FR-IFACE-003 | 공개 interface는 cross-domain composition endpoint나 schema를 제공해서는 안 된다. |
| FR-RPT-001 | `PUBLISHED` 결과에는 canonical JSON ResearchReport와 그것에서 결정론적으로 만든 HTML report를 제공해야 한다. |
| FR-RPT-002 | 보고서는 strategy, evidence IDs, provider attempts, dataset/config/code/schema versions, lineage, metrics, validation decision, artifact hashes를 포함해야 한다. |
| FR-RPT-003 | 보고서는 holdout disclosure allowlist 밖 selector·row·fold·trace·재구성 가능한 값을 포함해서는 안 된다. |
| FR-RPT-004 | JSON은 report의 권위 artifact이고 HTML은 escaped deterministic rendering이어야 한다. |
| FR-RPT-005 | report artifact는 validation과 publication identity에 연결되어야 한다. |
| FR-RPT-006 | 생성·보고서 조회는 사용자 결과에 internal rejection 또는 non-`PUBLISHED` intermediate를 섞어서는 안 된다. |
| FR-JOB-001 | 장시간 작업은 durable job ID, 상태, actor, 시간, reason과 immutable result link를 기록해야 한다. |
| FR-JOB-002 | 프로세스 시작 시 persisted `RUNNING` job은 예외 없이 `FAILED`와 `AF-JOB-INTERRUPTED`로 전이해야 한다. |
| FR-JOB-003 | `RUNNING` job은 자동 stage 재개하지 않으며 사용자는 새 job으로 재제출해야 한다. |
| FR-JOB-004 | 완료 fingerprint는 재계산되지 않고 consumed holdout과 closed lineage는 재사용·재개방되지 않아야 한다. |
| FR-JOB-005 | 정상 publication과 해당 job의 `SUCCEEDED` 전이는 하나의 atomic outcome이어야 한다. |

## 6. Non-Functional Requirements

| ID | 요구사항 | 합격 기준 |
| --- | --- | --- |
| NFR-DET-001 | canonical identity와 resource hash | Windows/Linux에서 고정 fixture hash가 정확히 일치 |
| NFR-DET-002 | 수치 재현성 | 주요 metric이 `1e-10` 이내로 일치 |
| NFR-DET-003 | 결정론적 복구 | restart, replay, retry fixture가 불변 fingerprint·holdout·`PUBLISHED` authority를 보존 |
| NFR-SEC-001 | 비밀·sealed 보호 | secret, raw sealed selector, 금지된 disclosure가 log·prompt·report에 없음 |
| NFR-REL-001 | 로컬 신뢰성 | SQLite/local artifact/단일 worker에서 partial authority 결과 없이 fail closed |
| NFR-MNT-001 | 유지보수성 | Lab public contract 밖 cross-import 0건, composition package/API/schema 0건 |
| NFR-TST-001 | 검증 범위 | core/compiler/search/validation focused coverage 85% 이상과 8 domain golden/E2E |

## 7. Constraints와 Assumptions

- 개발 기간을 4일로 제한하지 않는다. 검증 가능한 범위 완성이 일정 단축보다 우선한다.
- 도메인별 계약과 엔진을 단일 범용 수식으로 축소하지 않는다.
- LLM은 deterministic code가 소유하는 state, compiler, budget, fingerprint, engine, validation, registry 또는 publication을 변경하지 않는다.
- 입력은 사용자 제공 point-in-time dataset 또는 synthetic fixture이고, 실제 수익·실거래 성과를 보장하지 않는다.
- provider가 없는 CI에서는 fake adapter를 사용한다.
- DSR은 직접 근거가 추가되기 전 필수 gate가 아니다.

## 8. Acceptance Criteria

| ID | 인수 조건 | 관련 요구사항 |
| --- | --- | --- |
| AC-001 | 유효·무효 KnowledgePack fixture가 claim schema, immutable version/hash, citation, counterevidence, failure memory 검증을 각각 통과·실패한다. | FR-KNOW-001~006 |
| AC-002 | 같은 KnowledgePack, CapabilitySnapshot, request에서 generation request hash와 선택 JSON artifact가 고정된다. | FR-GEN-001 |
| AC-003 | 등록된 ordered provider/model chain이 순서대로 fallback하고 모든 attempt가 provenance에 남는다. | FR-GEN-002~004 |
| AC-004 | 수락된 generation artifact를 재생할 때 실제 LLM 호출이 0회다. | FR-GEN-005 |
| AC-005 | 8개 도메인의 valid/invalid typed StrategySpec contract test가 통과한다. | FR-DSL-001~003 |
| AC-006 | allowlist 밖 operator, 잘못된 AST, 임의 코드 field가 실행 전에 거절된다. | FR-DSL-004~006 |
| AC-007 | 같은 seed의 finite SearchRun이 같은 lineage, parent-pool, 순위와 stop reason을 만들며 후보를 중복 실행하지 않는다. | FR-SRCH-001~008 |
| AC-008 | `PLATEAU`와 `UNIVERSE_EXHAUSTED` 종료 branch가 모두 finite termination으로 검증된다. | FR-SRCH-009 |
| AC-009 | 모든 trial이 complete append-only ledger에 있고 PBO 입력·certificate에서 누락되지 않는다. | FR-LEDGER-001~006 |
| AC-010 | 8개 도메인 각각이 synthetic fixture, golden accounting/fill, 비용·지연 민감도, 시간 누수 공격, API/CLI E2E를 통과한다. | FR-ENG-001~008 |
| AC-011 | 필수 data 또는 비용·지연·fill·borrow·funding 정책 누락은 preflight reason code와 함께 거절된다. | FR-POL-001~003 |
| AC-012 | purged/embargoed walk-forward split이 시간 중첩과 future information을 차단한다. | FR-VAL-001~004 |
| AC-013 | PBO가 선택 후보가 아닌 complete eligible trial ledger에서 계산되고 불완전 certificate를 거절한다. | FR-PBO-001~005 |
| AC-014 | ValidationProfile은 SearchRun 시작 후 변경할 수 없고 semantic 변경은 새 hash/run을 요구한다. | FR-PROFILE-001~003 |
| AC-015 | holdout slot이 sealed data 접근 전에 소비되고 lineage가 닫히며 crash·실패 후에도 재사용되지 않는다. | FR-HOLD-001~005 |
| AC-016 | 모든 gate 통과 전략만 `PUBLISHED` Registry와 사용자 조회에 나타나며 publication이 report와 원자적이다. | FR-PUB-001~004 |
| AC-017 | 탈락 전략은 사용자 통과 목록에 나타나지 않고 internal rejection reason·lineage가 보존된다. | FR-REJ-001~003 |
| AC-018 | 동일 입력의 CLI와 REST가 같은 command fingerprint와 결과를 만든다. | FR-IFACE-001~003 |
| AC-019 | HTML/JSON report가 필수 provenance·metrics·validation 결정·artifact hash를 포함하고 sealed detail을 노출하지 않는다. | FR-RPT-001~006 |
| AC-020 | restart 시 모든 persisted `RUNNING` job이 `FAILED`가 되고 completed fingerprint·consumed holdout·`PUBLISHED` authority가 유지된다. | FR-JOB-001~005 |
| AC-021 | Windows와 Linux에서 canonical hash는 정확히, 주요 metric은 허용 오차 내 동일하다. | NFR-DET-001~003 |

## 9. 참조

- 구현 구조: [02_Architecture.md](./02_Architecture.md)
- 릴리스와 작업 순서: [03_DevelopmentPlan.md](./03_DevelopmentPlan.md)
- 외부 계약: [04_API.md](./04_API.md)
- 영속 계약: [05_Database.md](./05_Database.md)
- 검증 방법: [07_TestPlan.md](./07_TestPlan.md)
- 중요한 설계 근거: [ADR](./ADR/)
