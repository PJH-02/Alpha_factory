# Alpha Foundry Test Plan

상태: Approved  
목표: 수치가 높아 보이는 결과보다 권위 계약, 재현성, complete lineage, 누수 방지와 `PUBLISHED` 공개 경계를 우선 검증한다.

## 1. 테스트 수준

| 수준 | 대상 | 외부 의존성 | 실행 시점 |
| --- | --- | --- | --- |
| Unit | canonical encoding, schema, compiler, search iterator, validation rule | 없음 | 모든 PR |
| Contract | Knowledge, LLM chain, DSL/compiler, 8 Lab/engine, API/report schema | fake/fixture | 모든 PR |
| Integration | SQLite, CAS, ownership/event, ledger, PBO, holdout, publication | 임시 local resource | 모든 PR |
| E2E | Mandate→`PUBLISHED` report 또는 internal rejection | fake LLM + synthetic data | 모든 PR |
| Numeric Oracle | 8개 engine 회계·fill·정책·누수 | 독립 계산 fixture | 모든 PR |
| Recovery/Security/Architecture | crash, disclosure, result visibility, no-composition | 격리 환경 | PR 및 release |

## 2. 공통 규칙

- Test ID는 `UT`, `CT`, `IT`, `E2E`, `NUM`, `SEC`, `REC`, `ARCH`, `AT` prefix를 사용한다.
- test는 Arrange–Act–Assert 구조와 하나의 실패 이유를 가진다.
- 시간, UUID, seed, provider 응답, capability/resource version은 고정한다.
- 실제 외부 LLM과 인터넷은 어떤 test에서도 사용하지 않는다. provider test는 fake adapter로 attempt와 호출 횟수를 검증한다.
- 숫자 tolerance는 field별로 명시한다. 기본 상대·절대 오차는 `1e-10`이다.
- canonical hash는 Windows/Linux에서 byte-for-byte 일치해야 하며, flaky test는 재실행으로 숨기지 않는다.
- fixture 또는 구현이 바뀌면 관련 Test ID, resource hash, expected artifact를 같은 변경에서 갱신한다.

## 3. 테스트 환경

| 환경 | 기준 |
| --- | --- |
| CI | Python 3.12, 최신 Ubuntu LTS와 Windows GitHub-hosted image, SQLite 3.45+, local temporary filesystem |
| Reference performance | 4 physical core 이상, 16GB RAM, SSD; network와 LLM 호출 제외 |
| Artifact | synthetic Parquet 데이터와 SHA-256 manifest, local CAS |
| Provider | ordered fake provider/model chain, no secret, deterministic timeout/error script |

성능·release evidence에는 CPU, RAM, OS, Python, code version, resource hash, fixture hash를 기록한다.

## 4. Unit Test

### 4.1 Canonical resource와 domain boundary

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| UT-CANON-001 | AF-CANON vector | 고정 encoded byte와 digest가 일치 |
| UT-CANON-002 | direct SearchSpec field mutation | 모든 semantic field 변경이 digest 변경 |
| UT-CANON-003 | transitive resource mutation | operator/profile/universe/dataset/policy/schema/code 의미 변경이 digest 변경 |
| UT-CANON-004 | non-semantic mutation | comment/display label 변경은 digest 불변 |
| UT-DOM-001 | ResearchMandate와 primary domain | 유효·무효 조합과 하나의 discriminator만 허용 |
| UT-DOM-002 | immutable revision | 원본 불변, 새 version/hash와 parent 생성 |
| UT-DSL-001 | typed AST/compiler | 유효 AST만 deterministic execution plan으로 컴파일 |
| UT-DSL-002 | generated-code rejection | unknown field, code·SQL·shell, unknown operator를 실행 전 거절 |
| UT-SCOPE-001 | domain isolation | 다른 Lab payload·internal import·composition schema를 거절 |

### 4.2 Finite deterministic search와 ledger

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| UT-SRCH-001 | finite universe/traversal | 같은 seed가 같은 duplicate-free traversal을 생성 |
| UT-SRCH-002 | finite mutation/crossover iterator | allowlist·grid 밖 후보가 없고 first-admissible-unseen만 선택 |
| UT-SRCH-003 | parent-pool snapshot | K=0/K=1/tie/mixed gate/completion-during-generation에서 immutable ranked pool 사용 |
| UT-SRCH-004 | ranking | frozen profile lexicographic order와 candidate hash tie-break 일치 |
| UT-SRCH-005 | normal stop | `PLATEAU`와 `UNIVERSE_EXHAUSTED`가 generation cap 없이 finite termination하고 interruption은 failure |
| UT-SRCH-006 | optimizer acceptance | valid candidate 생성과 finite termination만 optimizer 합격으로 판정; baseline 비교 없음 |
| UT-LEDGER-001 | trial lifecycle | contiguous start마다 정확히 하나의 terminal outcome |
| UT-LEDGER-002 | proposal/event sequence | invalid/outside/duplicate/direct fallback이 소비 순서대로 append-only 기록 |
| UT-LEDGER-003 | complete certificate | started=terminal, eligible=matrix, exact fold set; missing/NaN/duplicate는 `AF-PBO-INCOMPLETE` |

### 4.3 Validation, disclosure, job

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| UT-VAL-001 | purged/embargoed split | time overlap와 future information 차단 |
| UT-VAL-002 | hard gate order | common/domain gate 순서와 early hard fail 일치 |
| UT-VAL-003 | ValidationProfile immutability | 시작 후 gate/rank/fold/PBO/patience 변경 거절 |
| UT-PBO-001 | hand CSCV oracle | winners, losers, non-evaluable terminal을 포함한 PBO 결과 일치 |
| UT-HOLD-001 | disclosure allowlist | aggregate 허용 field만 있고 selector·row·fold·trace 없음 |
| UT-HOLD-002 | no-feedback boundary | report/disclosure를 knowledge·generation·search·ranking·PBO가 import하지 않음 |
| UT-JOB-001 | restart transition | persisted `RUNNING`은 항상 `FAILED`/`AF-JOB-INTERRUPTED`, auto-resume 없음 |
| UT-JOB-002 | immutable authority | completed fingerprint, consumed slot, closed lineage, `PUBLISHED`는 restart 후 불변 |

## 5. Contract Test

### 5.1 Knowledge와 ordered LLM chain

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| CT-KNOW-001 | KnowledgePack valid/invalid fixture | claim schema, citation, counterevidence, failure memory, immutable version/hash 검증 |
| CT-GEN-001 | generation request identity | 동일 pins/snapshot/request의 request hash와 selected JSON 고정 |
| CT-GEN-002 | ordered fallback | snapshot ordinal 순으로 provider/model을 시도하고 모든 attempt 기록 |
| CT-GEN-003 | malformed/unknown typed JSON | schema error, accepted artifact와 revision 미생성 |
| CT-GEN-004 | accepted artifact replay | duplicate/replay에서 실제 provider call 0회 |
| CT-GEN-005 | prompt boundary | 허용 evidence·capability·constraint만 포함하고 sealed detail·secret 0건 |
| CT-GEN-006 | provider failure | timeout/401/403/429/model unavailable가 다음 snapshot entry 또는 typed terminal failure로만 전이 |

### 5.2 DSL, Lab, engine

각 Lab에 동일한 parameterized suite를 적용하며 domain name을 fixture·test output에 명시한다.

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| CT-DSL-001 | 8 typed StrategySpec valid fixture | Factor, StatArb, Market Making, Structural Flow, Cross Venue, Derivatives, Event Fundamental, Time Series가 각각 통과 |
| CT-DSL-002 | 8 typed StrategySpec invalid fixture | missing field, discriminator mismatch, unknown field, invalid AST가 field path와 함께 실패 |
| CT-DSL-003 | compiler allowlist | 8개 compiler가 allowlist 밖 operator·code field를 실행 전 거절 |
| CT-ENG-001 | deterministic engine input | 각 domain에서 동일 input/seed의 result hash 일치 |
| CT-ENG-002 | policy/data preflight | 모든 domain이 필수 cost·latency·fill·borrow·funding/data 누락을 reason code와 함께 거절 |
| CT-ENG-003 | engine artifacts | 각 domain이 result, diagnostics, policy/dataset/config/code/schema provenance를 생성 |
| CT-ENG-004 | cross-domain isolation | 다른 Lab 내부 import/payload와 composition surface 0건 |

### 5.3 Interface와 report

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| CT-API-001 | endpoint/schema table | OpenAPI route와 request/response schema 완전 일치 |
| CT-API-002 | CLI/REST command mapping | 동일 JSON input이 동일 command fingerprint와 `data` schema 반환 |
| CT-API-003 | visibility | Researcher strategy/result/report query가 `PUBLISHED` aggregate만 반환 |
| CT-API-004 | no composition surface | composition/Meta Portfolio endpoint·schema 없음 |
| CT-RPT-001 | canonical JSON ResearchReport | strategy, evidence IDs, attempts, versions, lineage, metrics, decision, artifact hashes 포함 |
| CT-RPT-002 | deterministic HTML | canonical JSON의 escaped deterministic rendering이며 hashable artifact 생성 |
| CT-RPT-003 | report redaction | disclosure allowlist 밖 sealed selector·row·fold·trace·reconstructive value 0건 |

## 6. Integration Test

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| IT-DB-001 | empty/upgrade migration | 최신 version 도달, existing row/hash 손실 0건 |
| IT-GEN-001 | generation ownership race | request/ordinal마다 한 owner/provider call, duplicate는 existing owner 반환 |
| IT-GEN-002 | interrupted generation | dangling attempt가 typed interruption으로 terminalize되고 새 resubmit만 새 owner 사용 |
| IT-SRCH-001 | persisted parent-pool | generation 시작 뒤 completion이 같은 generation parent pool을 변경하지 않음 |
| IT-SRCH-002 | search replay | 동일 seed/resource의 ledger, rank, stop reason, parent-pool digest 일치 |
| IT-LEDGER-001 | complete-ledger PBO | exact trial/fold equality를 만족한 matrix만 PBO에 전달 |
| IT-HOLD-001 | pre-access holdout race | transaction이 sealed read 전에 하나의 slot만 consume하고 lineage를 close |
| IT-HOLD-002 | holdout crash | consume 뒤 crash/fail에서도 두 번째 access·retry·reopen 불가 |
| IT-PUB-001 | atomic publication | report artifact, registry, `PUBLISHED`, job `SUCCEEDED`가 함께 commit 또는 rollback |
| IT-PUB-002 | invalid publication | validation/lineage/disclosure/input-hash 오류가 typed rejection이며 Publication 미생성 |
| IT-REJ-001 | internal rejection retention | rejected strategy reason/lineage는 internal registry에만 존재 |
| IT-JOB-001 | blanket restart recovery | 모든 persisted `RUNNING` job이 `FAILED`; legacy `PUBLISHED`+`RUNNING`은 `PUBLISHED`+`FAILED` |
| IT-JOB-002 | resubmit invariants | completed fingerprint 재계산, consumed holdout 재사용, closed lineage reopen 없음 |

## 7. Numeric Oracle와 8-domain E2E

각 domain은 독립 손계산 또는 별도 oracle로 golden accounting/fill, 명시 cost·latency sensitivity, point-in-time leakage 공격을 검증한다. `NUM-ENG-*`와 `E2E-DOM-*`는 같은 domain fixture를 공유하되 expected result는 engine 구현으로 생성하지 않는다.

| Domain | Numeric ID | Numeric oracle | E2E ID |
| --- | --- | --- | --- |
| Factor | NUM-ENG-001 | panel signal/rank/weight, long-short NAV, cost | E2E-DOM-001 |
| StatArb | NUM-ENG-002 | hedge ratio, multi-leg transition, leg cost | E2E-DOM-002 |
| Market Making | NUM-ENG-003 | fill, inventory, adverse selection, fee | E2E-DOM-003 |
| Structural Flow | NUM-ENG-004 | event impact, execution latency, accounting | E2E-DOM-004 |
| Cross Venue | NUM-ENG-005 | venue clock, route, partial fill, latency | E2E-DOM-005 |
| Derivatives | NUM-ENG-006 | cash-flow, funding, borrow, margin | E2E-DOM-006 |
| Event Fundamental | NUM-ENG-007 | event availability, event-time execution, accounting | E2E-DOM-007 |
| Time Series | NUM-ENG-008 | walk-forward state, rebalance, leakage-free accounting | E2E-DOM-008 |

모든 `E2E-DOM-*`는 mandate→typed DSL/compiler→finite search candidate→engine→pre-validation→holdout→`PUBLISHED` JSON/HTML report 흐름을 통과한다. invalid candidate와 validation failure는 `PUBLISHED` 결과가 아니라 internal rejection으로 끝나야 한다.

## 8. Security, architecture, recovery

| ID | 공격 또는 장애 | 합격 조건 |
| --- | --- | --- |
| SEC-DSL-001 | LLM의 Python·SQL·shell·unknown operator output | compiler 이전 schema/allowlist 거절, 실행 0회 |
| SEC-DATA-001 | future `available_at`, future row, post-split config | generation/engine/validation이 누수를 hard fail |
| SEC-HOLD-001 | report·LLM에 sealed detail 요청 | adapter/report가 redaction하고 feedback path 0건 |
| SEC-PUB-001 | rejected/intermediate ID를 Researcher query | not found 또는 공개 결과가 아닌 typed response; detail 누출 0건 |
| ARCH-001 | composition package/API/schema와 Lab cross-import | architecture checker와 import/schema scan이 0건을 증명 |
| REC-GEN-001 | provider call 전/후와 accepted artifact rename 전 crash | duplicate call 없이 attempt state와 replay contract 보존 |
| REC-SRCH-001 | trial start/terminal 사이 crash | dangling start terminalize, SearchRun failed, 새 resubmit lineage 생성 |
| REC-HOLD-001 | slot consume 직후 crash | sealed access 재시도 없이 slot/lineage closed 유지 |
| REC-PUB-001 | JSON/HTML staging 또는 publication commit 중 crash | partial public artifact·registry 없음, retryable state만 가능 |
| REC-JOB-001 | process restart | every persisted `RUNNING`→`FAILED`; no stage resume; `PUBLISHED` demotion 없음 |

## 9. Acceptance traceability

| AT ID | Acceptance Criteria | 실행 Test |
| --- | --- | --- |
| AT-001 | AC-001 | CT-KNOW-001 |
| AT-002 | AC-002 | CT-GEN-001, UT-CANON-001~004 |
| AT-003 | AC-003 | CT-GEN-002, CT-GEN-006, IT-GEN-001~002 |
| AT-004 | AC-004 | CT-GEN-004, REC-GEN-001 |
| AT-005 | AC-005 | CT-DSL-001~002 |
| AT-006 | AC-006 | UT-DSL-002, CT-DSL-003, SEC-DSL-001 |
| AT-007 | AC-007 | UT-SRCH-001~004, IT-SRCH-001~002 |
| AT-008 | AC-008 | UT-SRCH-005~006 |
| AT-009 | AC-009 | UT-LEDGER-001~003, IT-LEDGER-001, REC-SRCH-001 |
| AT-010 | AC-010 | NUM-ENG-001~008, E2E-DOM-001~008 |
| AT-011 | AC-011 | CT-ENG-002 |
| AT-012 | AC-012 | UT-VAL-001~002, SEC-DATA-001 |
| AT-013 | AC-013 | UT-PBO-001, UT-LEDGER-003, IT-LEDGER-001 |
| AT-014 | AC-014 | UT-VAL-003, UT-CANON-003 |
| AT-015 | AC-015 | IT-HOLD-001~002, REC-HOLD-001 |
| AT-016 | AC-016 | IT-PUB-001~002, CT-API-003 |
| AT-017 | AC-017 | IT-REJ-001, SEC-PUB-001 |
| AT-018 | AC-018 | CT-API-001~002, E2E-DOM-001~008 |
| AT-019 | AC-019 | CT-RPT-001~003, IT-PUB-001, SEC-HOLD-001 |
| AT-020 | AC-020 | UT-JOB-001~002, IT-JOB-001~002, REC-JOB-001 |
| AT-021 | AC-021 | UT-CANON-001~004, IT-SRCH-002, cross-OS golden suite |

## 10. 테스트 데이터 전략

```text
tests/fixtures/
├── canonical/
├── knowledge/
├── generation/
├── search/
├── validation/
├── holdout/
├── publication/
├── domains/
│   ├── factor/
│   ├── statarb/
│   ├── market_making/
│   ├── structural_flow/
│   ├── cross_venue/
│   ├── derivatives/
│   ├── event_fundamental/
│   └── time_series/
└── migrations/
```

- 모든 fixture는 synthetic 또는 재배포 허용 point-in-time 데이터이며 schema/resource version, 생성 script version, SHA-256, expected time range를 manifest에 기록한다.
- 각 domain fixture는 valid/invalid StrategySpec, policy/data omission, golden accounting/fill, latency/cost sensitivity, future-information 공격을 가진다.
- Search fixture는 finite universe, operator grid, seed traversal, parent-pool, plateau/exhaustion, complete/incomplete PBO matrix를 가진다.
- Holdout/report fixture는 pre-access consume race, crash, disclosure allowlist, `PUBLISHED` visibility를 가진다.
- expected result는 대상 engine, compiler, PBO 구현으로 생성하지 않는다.

## 11. CI와 Release Gate

### Pull Request

- locked dependency/toolchain 확인, format/lint/type
- unit, contract, integration, numeric, E2E, security, architecture focused suite
- migration empty/upgrade, OpenAPI/document/traceability/architecture checker
- canonical/semantic mutation, parent-pool, complete-ledger PBO, holdout/publication/job recovery focused checks
- core/compiler/search/validation focused coverage 85% 이상

### Release

- 모든 PR suite와 fault-injection/recovery, full 8-domain numeric/E2E, performance baseline 성공
- AT-001~AT-021 성공
- Windows/Linux canonical hash 정확 일치 및 주요 metric `1e-10` 이내
- `PUBLISHED` visibility/no-feedback/no-composition architecture evidence
- P0/P1 defect 0건, flaky test 0건
- 빈 환경 install→migrate→8 domain fixture→HTML/JSON report pipeline evidence

## 12. 결함 분류

| 등급 | 예 | Release 처리 |
| --- | --- | --- |
| P0 | data corruption, holdout leakage/reuse, non-`PUBLISHED` 공개, arbitrary code 실행 | 즉시 중단, release 금지 |
| P1 | hash 재현 실패, accounting/PBO 오류, job restart·publication atomicity 실패 | release 금지 |
| P2 | 비핵심 API 오류, 성능 목표 미달 | owner와 수정 release 확정 |
| P3 | 문구·개발 편의 문제 | backlog 허용 |

P0/P1 수정은 재현 test, 원인, 영향 범위, 회귀 test를 포함해야 한다.
