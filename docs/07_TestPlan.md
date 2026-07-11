# Alpha Foundry Test Plan

| 항목 | 값 |
|---|---|
| 문서 버전 | 1.0.0 |
| 기준일 | 2026-07-11 |
| 요구사항 기준 | [01_Requirements.md](01_Requirements.md) |
| 테스트 도구 | pytest, Hypothesis, Testcontainers, k6, fault-injection harness |

## 1. 목적과 품질 전략

테스트의 최우선 목적은 높은 coverage 숫자가 아니라 다음 치명적 오류를 배포 전에 차단하는 것이다.

1. look-ahead, survivorship, 수정 데이터, formation leakage
2. 수수료·cashflow·fill·position·P&L 부호 또는 보존식 오류
3. domain/payload/engine/validation/memory 경계 위반
4. sealed holdout 재접근 또는 결과 피드백 유출
5. 비결정적 fingerprint·실험·판정
6. 동시 job claim, 상태 전이, 승인, event의 이중 반영
7. artifact·migration·backup 손상과 복구 불능
8. 비밀·권한·tenant 경계 위반

테스트 피라미드는 pure unit/property를 가장 넓게 두고 contract/integration으로 경계를 검증하며 E2E는 사용자 흐름과 상태 불변식을 검증한다. 통계 구현은 독립 oracle과 알려진 생성 과정으로 검증한다.

## 2. 역할과 승인

| 역할 | 책임 |
|---|---|
| Module Owner | unit/property, module fixture, 결함 수정 |
| Platform/QA Engineer | integration, migration, API, performance, recovery automation |
| Human Quant Reviewer | 통계 oracle, accounting golden case, leakage 공격, domain acceptance |
| Security Reviewer | auth, tenant, secret, dependency/image, prompt injection |
| Tech Lead | release gate, coverage waiver, flaky test 승인 금지, 최종 traceability |

AI Agent는 test를 생성·수정할 수 있지만 golden numeric result, sealed guard, 사람 승인 test의 유일한 reviewer가 될 수 없다.

## 3. 테스트 환경

| 환경 | 목적 | 외부 의존성 |
|---|---|---|
| Unit | pure function·invariant | 없음 |
| Contract | JSON Schema, plugin, OpenAPI, event, import boundary | 없음 |
| Integration | repository, PostgreSQL, Azurite, OTel, fake OIDC | containerized local |
| E2E | API/CLI/worker/dispatcher 전체 흐름 | deterministic fake LLM + synthetic data |
| Provider smoke | 승인 LLM/data adapter 호환 | sandbox account, write 격리 |
| Performance | SLO와 engine benchmark | production-like staging |
| Recovery | process·dependency failure | isolated staging |
| Security | auth/RLS/secret/egress/image | isolated staging |

### 3.1 Reference hardware

Engine benchmark worker: 8 vCPU, 32 GiB RAM, 200 GiB local NVMe scratch. PostgreSQL: 4 vCPU, 16 GiB RAM, 1 ms 이하 same-region network. API는 2 replica, 각 2 vCPU/4 GiB. Blob과 DB는 staging에서 Production과 같은 managed class를 사용한다.

성능 결과에는 image digest, Git revision, migration head, dataset hash, hardware, concurrent job 수, warm/cold cache를 기록한다.

## 4. Test ID와 공통 규칙

| Prefix | 계층 |
|---|---|
| `T-UNIT-*` | 단위 |
| `T-CONTRACT-*` | schema/interface |
| `T-INT-*` | integration |
| `T-E2E-*` | end-to-end |
| `T-PROP-*` | 속성 기반 |
| `T-LEAK-*` | 누수·red-team |
| `T-STAT-*` | 통계 oracle |
| `T-PERF-*` | 성능 |
| `T-REC-*` | 복구·장애 |
| `T-SEC-*` | 보안 |
| `T-AC-*` | 인수 시나리오 orchestration |

모든 test는 seed, fixture version, 기대 상태 또는 tolerance를 명시한다. 실제 시각, public network, 실행 순서, 다른 test의 잔여 DB에 의존하지 않는다.

## 5. Unit Test

### 5.1 Core와 Orchestration

| ID | 대상 | 핵심 case |
|---|---|---|
| T-UNIT-001 | UUID/typed ID | ID type 혼용 거부, canonical lowercase |
| T-UNIT-002 | UTC time | naive/DST ambiguous 거부, end-exclusive range |
| T-UNIT-003 | Decimal/unit | bps↔rate, money precision, `-0` normalization |
| T-UNIT-004 | Domain union | 8 valid, discriminator mismatch, extra field |
| T-UNIT-005 | Mandate invariant | preferred/excluded 교집합, horizon order, live=false |
| T-UNIT-006 | Config union | 모든 variant, static fee exact preservation, invalid unit |
| T-UNIT-007 | State machine | 모든 legal edge와 Cartesian illegal edge |
| T-UNIT-008 | Fingerprint | key-order invariance, component mutation sensitivity |
| T-UNIT-009 | Capability matching | exact version, range, missing list, ambiguity |
| T-UNIT-010 | Domain router | primary exactly one, exclusion, auxiliary interface |
| T-UNIT-011 | Generation allocation | largest-remainder 비율, candidate 1~1,000 |
| T-UNIT-012 | Hard gate | 10개 차단 조건 truth table, 상쇄 금지 |
| T-UNIT-013 | Pareto selector | dominated 제거, deterministic tie break, 가중합 미사용 |
| T-UNIT-014 | DAG | cycle, missing node, cross-program edge, topological order |
| T-UNIT-015 | Search budget | atomic charge, exact exhaustion, failure charge policy |
| T-UNIT-016 | Feedback view | train full, validation coarse, sealed hidden |
| T-UNIT-017 | Composition | 6 interface pass, arithmetic/unknown interface fail |
| T-UNIT-018 | Report mapper | `NOT_APPLICABLE`과 `NOT_COMPUTED` 구분 |

### 5.2 Engine와 Accounting

| ID | Engine | 최소 unit case |
|---|---|---|
| T-UNIT-020 | PanelPortfolio | PIT universe, lag, neutralization, weight sum, turnover |
| T-UNIT-021 | MultiLegSequential | formation boundary, hedge update, state entry/exit, leg imbalance |
| T-UNIT-022 | LOBDiscreteEvent | event ordering, queue ahead, partial fill, cancel race, inventory |
| T-UNIT-023 | StructuralEvent | availability, event cluster, auction, impact/reversal window |
| T-UNIT-024 | MultiVenueGraph | equivalence, cycle capacity, leg order, stale/outage policy |
| T-UNIT-025 | DerivativesCashflow | multiplier, funding/borrow, margin, liquidation, expiry, Greeks |
| T-UNIT-026 | PointInTimeEvent | initial/restatement, session mapping, cohorts, overlap |
| T-UNIT-027 | SequentialForecast | rolling fit, purge/embargo, calibration, sizing, stop |
| T-UNIT-028 | MetaAllocation | registered-only, covariance shrinkage, constraints, cost |
| T-UNIT-029 | Attribution | gross = component sum; net = gross - all costs |
| T-UNIT-030 | Risk | MaxDD, VaR, ES, exposure, concentration, confidence parameters |

각 engine은 최소 1개의 손계산 가능한 profit, loss, no-trade, partial failure case를 가진다.

## 6. Contract Test

| ID | 계약 | 합격 기준 |
|---|---|---|
| T-CONTRACT-001 | 8 QuestionPayload | domain별 valid/invalid/extra/missing fixture |
| T-CONTRACT-002 | 8 HypothesisPayload | question domain·field type·falsifier contract |
| T-CONTRACT-003 | 8 StrategyPayload | declarative only, plugin parameter schema, freeze |
| T-CONTRACT-004 | ResearchLab Protocol | 8 built-in lab method·schema·domain 일치 |
| T-CONTRACT-005 | BacktestEngine Protocol | validate/run/explain, deterministic synthetic result |
| T-CONTRACT-006 | Validation plugin | gate input/output와 rule version |
| T-CONTRACT-007 | Config policy | 모든 discriminator와 fingerprint 포함 |
| T-CONTRACT-008 | OpenAPI | runtime route/status/error/auth/header와 snapshot 일치 |
| T-CONTRACT-009 | Event | CloudEvents envelope, data schema, event catalog |
| T-CONTRACT-010 | CLI | application DTO·error code·default parity |
| T-CONTRACT-011 | Import boundary | architecture forbidden import 0건 |
| T-CONTRACT-012 | DB inventory | SQLAlchemy metadata, Alembic head, Database 문서 table 목록 일치 |
| T-CONTRACT-013 | Backward compatibility | 직전 minor consumer fixture가 current schema에서 통과 |

각 lab contract fixture set은 다음 7개를 반드시 포함한다.

```text
valid question
invalid question
minimum dataset manifest
minimum strategy
minimum engine result
intended rejection
intended pass
```

## 7. Integration Test

### 7.1 PostgreSQL

| ID | Scenario | 기대 결과 |
|---|---|---|
| T-INT-001 | 8 worker concurrent `SKIP LOCKED` claim | job당 claim 하나 |
| T-INT-002 | expired lease와 heartbeat 경쟁 | active heartbeat는 회수 안 됨, expired만 재queue |
| T-INT-003 | 같은 idempotency key/same hash 20 concurrent | resource 하나, 동일 response |
| T-INT-004 | same key/different hash | 1 success, 나머지 conflict |
| T-INT-005 | holdout access 100 concurrent | 정확히 1 row와 1 job |
| T-INT-006 | optimistic version conflict | stale update 0 row, stable error |
| T-INT-007 | state transition trigger | illegal edge와 AI approval 거부 |
| T-INT-008 | program edge batch | cycle transaction 전체 rollback |
| T-INT-009 | RLS tenant isolation | 다른 tenant select/update/side-channel count 0 |
| T-INT-010 | outbox atomicity | state와 event 둘 다 commit 또는 둘 다 rollback |
| T-INT-011 | outbox duplicate delivery | consumer effect 한 번 |
| T-INT-012 | partition boundary | 월말 microsecond row가 정확한 partition |
| T-INT-013 | migration | empty/previous/current/representative DB upgrade |
| T-INT-014 | retention | legal hold와 dependency artifact 삭제 차단 |

### 7.2 Blob/Parquet/Data

| ID | Scenario | 기대 결과 |
|---|---|---|
| T-INT-020 | content-addressed concurrent upload | blob 하나, link 여러 개, hash 검증 |
| T-INT-021 | upload 후 DB rollback | 24시간 orphan 후보, 참조 없음 |
| T-INT-022 | corrupt byte/read | `CORRUPT`, cache/experiment 사용 차단 |
| T-INT-023 | manifest missing/extra part | dataset quarantine |
| T-INT-024 | Parquet decimal/timestamp round trip | precision·UTC microsecond 보존 |
| T-INT-025 | partition pruning | requested range 밖 part read 0 |
| T-INT-026 | dataset version revoked | 신규 experiment 차단, 과거 lineage 유지 |
| T-INT-027 | as-of join adapter | available_at boundary와 tolerance exact |

### 7.3 LLM와 Identity adapter

| ID | Scenario | 기대 결과 |
|---|---|---|
| T-INT-030 | valid strict output | typed resource와 provenance |
| T-INT-031 | invalid output then valid repair | repair 정확히 1회 |
| T-INT-032 | invalid twice | safe terminal failure, 자유 text 저장 안 함 |
| T-INT-033 | provider 429/5xx | bounded retry, budget/accounting 기록 |
| T-INT-034 | prompt injection in source | instruction 미실행, source data로만 유지 |
| T-INT-035 | expired/wrong audience token | 401 |
| T-INT-036 | AI token human-only action | 403 + audit log |

## 8. End-to-End Test

| ID | 흐름 | 완료 상태 |
|---|---|---|
| T-E2E-001 | 질문 지정 mandate → compiled question → DAG | PROGRAM_READY |
| T-E2E-002 | 영역 지시 → 5 questions → 7 audits → top 3 DAG | PROGRAM_READY |
| T-E2E-003 | evidence source → claim → contradiction → question report | exact locator 역추적 |
| T-E2E-004 | Factor natural language → experiment → validation → report | VALIDATED 또는 의도된 REJECTED |
| T-E2E-005 | StatArb natural language → formation/trading → report | VALIDATED 또는 의도된 REJECTED |
| T-E2E-006 | MM synthetic program | 전용 engine/result/validation |
| T-E2E-007 | Structural Flow synthetic program | 전용 engine/result/validation |
| T-E2E-008 | Cross Venue synthetic program | 전용 engine/result/validation |
| T-E2E-009 | Derivatives synthetic program | 전용 engine/result/validation |
| T-E2E-010 | Event/Fundamental synthetic program | 전용 engine/result/validation |
| T-E2E-011 | Time Series synthetic program | 전용 engine/result/validation |
| T-E2E-012 | fatal gate bad strategy | REJECTED + complete Rejection Report |
| T-E2E-013 | same fingerprint API/CLI repeat | same experiment id/hash/decision |
| T-E2E-014 | G0~G8 → sealed → register | 1 access, hidden generator view |
| T-E2E-015 | PAPER → SHADOW → two-person live record | actor/role guard와 audit |
| T-E2E-016 | worker kill during experiment | lease recovery, effect exactly once |
| T-E2E-017 | 2 tenant identical strategy | 저장·query·artifact authorization 격리 |
| T-E2E-018 | 3 registered strategies → Meta allocation | constraints·attribution·report |
| T-E2E-019 | Open Discovery data profile → budget-bound questions → program | Beta에서 PROGRAM_READY, MVP에서는 명시적 release-disabled 오류 |

E2E의 LLM은 golden structured response를 반환하는 deterministic adapter다. 실제 provider는 별도 smoke test로 schema 호환만 확인하고 release correctness gate의 수치 근거로 사용하지 않는다.

## 9. Quant·통계 Oracle Test

### 9.1 공통 진단

| ID | 생성 과정·fixture | 기대 |
|---|---|---|
| T-STAT-001 | fixed-seed AR(1), phi=0.5, n=5,000 | ADF 5% reject, KPSS 5% non-reject |
| T-STAT-002 | fixed-seed random walk, n=5,000 | ADF 5% non-reject, KPSS 5% reject |
| T-STAT-003 | white noise vs AR(1) residual | Ljung–Box가 전자는 non-reject, 후자는 reject |
| T-STAT-004 | Gaussian vs fixed t(3) sample | QQ/tail과 AD가 heavy tail을 구분 |
| T-STAT-005 | moving-block dependent returns | IID bootstrap보다 block CI가 더 넓은 golden case |
| T-STAT-006 | sparse two-regime Bernoulli | 명시 Beta prior posterior가 closed form과 일치 |
| T-STAT-007 | overlapping label | purged/embargo index가 모든 overlap 제거 |
| T-STAT-008 | 100 null strategies + 1 signal | DSR/PBO/SPA reference implementation과 tolerance 일치 |

통계 검정의 p-value 자체가 환경에 따라 임계값 근처가 되지 않도록 fixed seed와 큰 separation을 사용한다. 기대 p-value를 소수점 전체로 고정하지 않고 decision과 reference tolerance를 함께 검증한다.

### 9.2 Domain oracle

| ID | Domain | Golden case |
|---|---|---|
| T-STAT-020 | Factor | 신호와 미래 수익의 완전 rank 관계, known IC/quantile monotonicity |
| T-STAT-021 | Factor | 산업 dummy를 제거한 residual exposure가 0 tolerance 안 |
| T-STAT-022 | StatArb | 알려진 beta의 cointegrated pair와 independent random pair |
| T-STAT-023 | StatArb | OU half-life가 생성 parameter confidence band 안 |
| T-STAT-024 | MM | 5-event L3 queue에서 exact fill/markout/inventory P&L |
| T-STAT-025 | Structural Flow | known event effect와 placebo zero effect, overlapping cluster |
| T-STAT-026 | Cross Venue | 3-edge cycle의 fee/depth 후 exact route P&L과 failed leg loss |
| T-STAT-027 | Derivatives | funding calendar·margin·liquidation·expiry 손계산 ledger |
| T-STAT-028 | Event | after-close filing의 next-open eligibility와 CAR |
| T-STAT-029 | Time Series | calibrated probability sample의 Brier/log-loss와 sizing |
| T-STAT-030 | Meta | 2-asset diagonal covariance의 closed-form ERC/CVaR bounds |

Oracle 구현은 production function을 import하지 않는다. 손계산 CSV/JSON, 독립 수식 notebook의 frozen output, 검증된 외부 library 중 하나를 source로 기록한다.

## 10. Property·누수·Red-Team Test

### 10.1 Property

| ID | 불변식 |
|---|---|
| T-PROP-001 | 미래 available_at row를 추가해도 과거 signal/result가 바뀌지 않는다. |
| T-PROP-002 | fee/slippage/impact를 증가시키면 같은 fills의 net P&L은 증가하지 않는다. |
| T-PROP-003 | position과 fill이 0이면 gross P&L·거래비용은 0이다. |
| T-PROP-004 | fill quantity 합은 order quantity를 넘지 않는다. |
| T-PROP-005 | cash + marked position + cumulative withdrawal/deposit가 ledger equity와 일치한다. |
| T-PROP-006 | 모든 leg position 합과 portfolio exposure가 일치한다. |
| T-PROP-007 | 동일 input/seed는 순서가 다른 worker에서도 같은 logical result다. |
| T-PROP-008 | fingerprint 구성요소 하나 변경은 다른 hash를 만든다. |
| T-PROP-009 | illegal domain composition은 payload 변형과 관계없이 실패한다. |
| T-PROP-010 | hard risk limit 위반 뒤 신규 risk-increasing order가 없다. |
| T-PROP-011 | artifact serialization round trip이 schema·decimal·time을 보존한다. |
| T-PROP-012 | DAG topological execution은 독립 node scheduling 순서와 무관한 결과를 만든다. |

### 10.2 Leakage attack

| ID | 공격 | 기대 차단점 |
|---|---|---|
| T-LEAK-001 | 미래 가격 column 삽입 | G2 data timing |
| T-LEAK-002 | latest restated financial을 과거 row로 복사 | Dataset/Point-in-time validator |
| T-LEAK-003 | 전체 기간으로 pair 선택 | formation boundary validator |
| T-LEAK-004 | full sample scaler/model fit | pipeline fit-scope validator |
| T-LEAK-005 | validation exact metric을 generator context에 inject | feedback view type/authorization |
| T-LEAK-006 | sealed result를 alias resource로 조회 | sealed repository/RLS |
| T-LEAK-007 | holdout 후 strategy 이름만 변경 | lineage/content/search hash guard |
| T-LEAK-008 | timezone 제거·현지시각 혼합 | schema/time validator |
| T-LEAK-009 | cost config 누락 후 zero fallback | config schema |
| T-LEAK-010 | Factor payload를 StatArb domain으로 전송 | discriminator contract |
| T-LEAK-011 | Factor memory를 MM generator에 전달 | namespace authorization |
| T-LEAK-012 | Paper/Shadow 결과를 sealed 이전 validation에 backfill | information-boundary validator |

### 10.3 Security red team

- prompt source 안의 system instruction·schema escape
- oversize/deeply nested JSON과 regex denial of service
- JWT alg confusion, wrong issuer/audience/key-id rotation
- tenant ID body spoof, UUID enumeration, artifact URL reuse
- SQL/path/template/shell injection
- malicious Parquet metadata와 decompression bomb
- model/pickle deserialization 시도
- log forging, secret in exception, prompt exfiltration
- AI actor가 human role claim을 body로 주입
- event replay와 aggregate version skip

## 11. 장애 복구 테스트

| ID | 주입 | 합격 기준 |
|---|---|---|
| T-REC-001 | engine phase 중 worker `SIGKILL` | 90초 내 lease 회수, 같은 job/experiment, 이중 artifact 없음 |
| T-REC-002 | API commit 직전 process kill | state+outbox 둘 다 없음 또는 둘 다 있음 |
| T-REC-003 | PostgreSQL connection 60초 차단 | API safe 503, worker lease 회수, corruption 0 |
| T-REC-004 | managed DB failover | RPO≤5분, RTO≤60분, fingerprint/holdout 일치 |
| T-REC-005 | Blob write timeout/partial object | DB dangling link 0, retry 또는 orphan cleanup |
| T-REC-006 | LLM 429/5xx/timeout | 지정 backoff·횟수, deterministic job 영향 없음 |
| T-REC-007 | outbox consumer 30분 중단 | 복구 후 순서 gap 탐지·중복 effect 0 |
| T-REC-008 | duplicate event 100회 | consumer effect 1회 |
| T-REC-009 | artifact byte corruption | checksum fail, cache quarantine, alert |
| T-REC-010 | 다음 달 partition 없음 | readiness degraded, ingest 차단, 기존 read 지속 |
| T-REC-011 | clock skew ±5분 worker | DB clock 기준 lease, double claim 없음 |
| T-REC-012 | sealed worker kill | access는 1개 유지, same access-bound job만 재개 |
| T-REC-013 | backup isolated restore | DB·blob hash·migration·holdout integrity, RPO/RTO 충족 |

Recovery test는 Production 데이터에 실행하지 않고 production-like isolated environment에서 분기별 수행한다.

## 12. 성능 테스트

### 12.1 API와 Control Plane

| ID | 부하 | 임계값 |
|---|---|---|
| T-PERF-001 | metadata GET 50 RPS, 25 users, 15분 | p95≤300ms, p99≤750ms, error<0.1% |
| T-PERF-002 | mutation/enqueue 50 RPS, 10분 | p95≤500ms, p99≤1s, duplicate 0 |
| T-PERF-003 | 8 workers, queue 10,000 jobs | claim p95≤100ms, oldest-age가 지속 감소 |
| T-PERF-004 | outbox 100,000 pending | publish lag p95≤5s, dead-letter 0 |
| T-PERF-005 | 1,000-node/5,000-edge DAG | validate+toposort≤2s, peak RSS≤512MiB |
| T-PERF-006 | 100,000 claims scoped retrieval | p95≤1s, RLS와 full-text result exact |

### 12.2 Engine benchmark

| ID | Fixture | Cold-cache 한도 | Peak RSS |
|---|---|---:|---:|
| T-PERF-020 | Factor panel 1M rows × 50 columns, 20 years monthly | 180s | 12GiB |
| T-PERF-021 | StatArb 10M aligned observations, 100 candidate pairs | 300s | 16GiB |
| T-PERF-022 | LOB replay 20M events, 2 quote policies | 600s | 16GiB |
| T-PERF-023 | CrossVenue 5 venues, 1M synchronized snapshots, 500 routes | 300s | 16GiB |
| T-PERF-024 | Event 2M events/returns joins | 240s | 12GiB |
| T-PERF-025 | TimeSeries 2M rows, 10 walk-forward folds, linear baseline | 300s | 16GiB |

시간 한도는 correctness를 낮추는 approximation을 허용하지 않는다. 실패 시 profiler artifact를 생성하고 partition/lazy execution/algorithm을 개선한다. Production candidate는 기준 대비 20% 이상 regression이면 release를 차단한다.

## 13. 보안 테스트

| ID | 검증 |
|---|---|
| T-SEC-001 | role/actor action matrix 전체 조합 |
| T-SEC-002 | PostgreSQL RLS cross-tenant select/update/delete/aggregate |
| T-SEC-003 | sealed DB role과 artifact scope |
| T-SEC-004 | OIDC signature/issuer/audience/expiry/JWKS rotation |
| T-SEC-005 | secret scanner source/config/log/report/prompt artifact |
| T-SEC-006 | dependency CVE, SBOM, image signature, non-root container |
| T-SEC-007 | egress allowlist와 metadata service 차단 |
| T-SEC-008 | artifact authorization expiry·reuse·tenant spoof |
| T-SEC-009 | migration role와 application role separation |
| T-SEC-010 | audit append-only와 privileged action reason |

High/Critical dependency vulnerability, secret 검출, RLS bypass, AI human-action 성공은 즉시 P0 release blocker다.

## 14. Acceptance Test

| Test | Acceptance Criteria | 실행 evidence |
|---|---|---|
| T-AC-001 | AC-001 | 두 autonomy mode E2E resource bundle |
| T-AC-002 | AC-002 | 8 union contract report |
| T-AC-003 | AC-003 | 40 question fixture와 field completeness report |
| T-AC-004 | AC-004 | missing capability structured rejection |
| T-AC-005 | AC-005 | mandate-to-DAG trace |
| T-AC-006 | AC-006 | Factor JSON/Markdown report와 oracle diff |
| T-AC-007 | AC-007 | StatArb JSON/Markdown report와 oracle diff |
| T-AC-008 | AC-008 | 6 synthetic E2E report bundle |
| T-AC-009 | AC-009 | static fee serialization/fingerprint/cost ledger |
| T-AC-010 | AC-010 | repeat-run fingerprint result comparison |
| T-AC-011 | AC-011 | leakage attack matrix 100% blocked |
| T-AC-012 | AC-012 | property/contract failure evidence |
| T-AC-013 | AC-013 | 100 concurrent holdout request transaction log |
| T-AC-014 | AC-014 | generator data-flow snapshot/redaction test |
| T-AC-015 | AC-015 | intended Rejection Report |
| T-AC-016 | AC-016 | AI actor 403 + audit row |
| T-AC-017 | AC-017 | worker-kill recovery trace |
| T-AC-018 | AC-018 | API/CLI parity snapshot |
| T-AC-019 | AC-019 | intentional drift CI failures |
| T-AC-020 | AC-020 | signed performance/recovery/security/coverage report |

## 15. 테스트 데이터 전략

### 15.1 Fixture 계층

| 계층 | 내용 | 보존 |
|---|---|---|
| Tiny exact | 2~100 rows/events, 손계산 결과 | Git JSON/CSV |
| Statistical synthetic | fixed-seed DGP, 5k~1M rows | generator code + parameter + expected summary |
| Domain synthetic | 8 engine별 정상/실패 market path | Parquet manifest + hash |
| Anonymized replay | 구조와 분포를 보존한 비식별 샘플 | restricted artifact |
| Performance scale | deterministic generated large dataset | generator + manifest; bytes 재생성 가능 |
| Incident regression | 최소 재현 case | secret/PII 제거 후 Git 또는 restricted artifact |

### 15.2 규칙

- fixture마다 `fixture_id`, version, schema, seed, generator revision, content hash, expected invariants가 있다.
- survivorship, delisting, revision, missing, duplicate, stale quote, DST, corporate action, partial fill, outage를 의도적으로 포함한다.
- Production 원본 row를 unit/CI에 복사하지 않는다.
- golden update는 production code 변경과 같은 PR에서 자동 승인하지 않는다. Human Quant Reviewer가 변경 이유와 독립 계산을 확인한다.
- golden file 전체 재생성으로 작은 의도 변경을 숨기지 않고 semantic diff를 첨부한다.

## 16. 요구사항 추적성

### 16.1 Functional Requirements

| FR | 주요 test |
|---|---|
| FR-001~008 | T-UNIT-005,009~010; T-E2E-001~002,019; T-AC-001,004 |
| FR-009~015 | T-INT-020~027; T-E2E-003; T-LEAK-002,011 |
| FR-016~025 | T-UNIT-004,011~013,015; T-CONTRACT-001; T-AC-002~003 |
| FR-026~033 | T-UNIT-014,017; T-CONTRACT-002~005,011; T-LEAK-010~011 |
| FR-034~044 | T-UNIT-006,008; T-PROP-002,007~008,011; T-AC-009~010 |
| FR-045 | T-UNIT-020; T-STAT-020~021; T-E2E-004 |
| FR-046 | T-UNIT-021; T-STAT-022~023; T-E2E-005 |
| FR-047 | T-UNIT-022; T-STAT-024; T-E2E-006 |
| FR-048 | T-UNIT-023; T-STAT-025; T-E2E-007 |
| FR-049 | T-UNIT-024; T-STAT-026; T-E2E-008 |
| FR-050 | T-UNIT-025; T-STAT-027; T-E2E-009 |
| FR-051 | T-UNIT-026; T-STAT-028; T-E2E-010 |
| FR-052 | T-UNIT-027; T-STAT-029; T-E2E-011 |
| FR-053 | T-UNIT-028; T-STAT-030; T-E2E-018 |
| FR-054~064 | T-UNIT-012,016,029~030; T-STAT-001~008; T-LEAK-001~012; T-E2E-012~014 |
| FR-065~070 | T-INT-005,007; T-E2E-012,014~015; T-SEC-001,010 |
| FR-071~076 | T-CONTRACT-008~010; T-INT-001~011; T-E2E-013,016; T-REC-001~012 |

### 16.2 Non-Functional Requirements

| NFR | 주요 test |
|---|---|
| NFR-001 | T-UNIT-008; T-PROP-007~008; T-E2E-013 |
| NFR-002 | T-PROP-001; T-LEAK-001~012 |
| NFR-003 | T-INT-010~011; T-SEC-010 |
| NFR-004~007 | T-PERF-001~006; T-REC-001~004 |
| NFR-008~010 | T-SEC-001~009 |
| NFR-011~012 | observability integration assertions + T-REC suite |
| NFR-013 | CI coverage gate |
| NFR-014~016 | T-CONTRACT-008,011~013; deployment smoke |
| NFR-017 | T-REC-004,005,013 |
| NFR-018 | T-INT-001~014; T-PROP-004~006 |
| NFR-019~020 | T-UNIT-015; T-INT-033; resource-limit E2E; T-PERF suite |
| NFR-021 | T-AC-019 |
| NFR-022~023 | T-UNIT-002~003; T-INT-024,027; T-STAT-024~027 |
| NFR-024~025 | T-E2E-004~012; report schema/snapshot tests |

## 17. CI와 Release Gate

### 17.1 Pull request

- format, lint, mypy, import contracts
- unit, contract, property의 bounded examples
- PostgreSQL/Azurite integration
- migration empty/previous upgrade
- OpenAPI/JSON Schema/event/docs drift
- secret, dependency, license scan
- 변경 module coverage와 전체 threshold

목표 실행 시간은 15분 이하다. test를 빼서 시간 목표를 맞추지 않고 shard와 cache를 사용한다.

### 17.2 Nightly

- 전체 property 10,000 examples
- 8 E2E domain suite
- leakage/red-team 전체
- statistical DGP 전체 seed set
- performance smoke 25% scale
- latest supported dependency compatibility

### 17.3 Release candidate

- Production-size performance
- worker/DB/blob/LLM/outbox fault injection
- isolated backup restore
- full security and RLS
- all acceptance tests
- signed image/SBOM/schema/migration/report bundle

### 17.4 Pass/Fail

다음 중 하나면 release 실패다.

- P0/P1 open defect 1건 이상
- critical invariant branch coverage 100% 미만
- core branch coverage 85% 또는 전체 line coverage 80% 미만
- flaky retry가 필요한 correctness/security test
- leakage, holdout, RLS, approval, accounting test 실패
- performance threshold 또는 RPO/RTO 미충족
- document/schema/migration/OpenAPI drift
- High/Critical 취약점 또는 secret 검출

## 18. 결함 분류와 처리

| 등급 | 예 | 처리 |
|---|---|---|
| P0 | holdout 재접근, tenant 유출, 주문·P&L 중대 오류, secret 노출 | 즉시 배포 중지·incident, 24시간 내 containment |
| P1 | look-ahead, wrong engine/domain, 상태 이중 반영, 복구 불능 | release blocker, root cause와 regression test |
| P2 | 일부 report 누락, 비치명 성능 저하, 불명확 오류 | milestone 전 수정 또는 명시 승인 |
| P3 | 문구·비핵심 UX | backlog, 다음 patch 검토 |

P0/P1 수정에는 최소 재현 fixture, 원인, 영향받은 fingerprint/lineage 탐색, 데이터·전략 quarantine 결정, regression test가 필수다. 과거 잘못된 결과는 덮어쓰지 않고 `QUARANTINED` 상태와 superseding report를 남긴다.
