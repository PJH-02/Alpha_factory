# Alpha Foundry Test Plan

상태: Approved  
목표: 수치가 높아 보이는 결과보다 계약, 재현성, 누수 방지, 실패 기록을 우선 검증한다.

## 1. 테스트 수준

| 수준 | 대상 | 외부 의존성 | 실행 시점 |
| --- | --- | --- | --- |
| Unit | value object, policy, compiler, validation rule | 없음 | 모든 PR |
| Contract | 8 Lab, Engine, LLM port, API schema | fake/fixture | 모든 PR |
| Integration | SQLite, filesystem, migration, provider adapter | 임시 local resource | 모든 PR |
| E2E | Mandate→Registry/Rejection | fake LLM + synthetic data | 모든 PR |
| Numeric Oracle | engine 회계·통계 | 독립 계산 fixture | 모든 PR |
| Recovery/Security/Performance | 운영 특성 | 격리 환경 | nightly/release |

## 2. 공통 규칙

- Test ID는 `UT`, `CT`, `IT`, `E2E`, `NUM`, `SEC`, `REC`, `PERF`, `AT` prefix를 사용한다.
- test는 Arrange–Act–Assert 구조와 하나의 실패 이유를 가진다.
- 시간, UUID, random seed, LLM 응답은 고정한다.
- 실제 외부 LLM과 인터넷은 release test에서도 사용하지 않는다.
- flaky test는 재실행으로 숨기지 않고 release gate에서 제외한 뒤 P1 defect로 처리한다.
- 숫자 tolerance는 field별로 명시한다. 기본 상대·절대 오차는 `1e-10`이다.

## 3. 테스트 환경

### MVP CI

- Python 3.12
- Ubuntu 최신 LTS와 Windows 최신 GitHub-hosted image
- SQLite 3.45+
- 2 vCPU, 7GB RAM 기준
- local temporary filesystem

### Reference Performance

- 4 physical core 이상
- 16GB RAM
- SSD
- network와 LLM 호출은 benchmark에서 제외

성능 결과에는 CPU, RAM, OS, Python, code version, fixture hash를 기록한다.

## 4. Unit Test

### 4.1 Domain과 Orchestration

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| UT-DOM-001 | Mandate mode별 필수 field | 모든 유효·무효 조합 예상 결과 |
| UT-DOM-002 | domain/payload discriminator | 불일치 100% 거절 |
| UT-DOM-003 | 상태 전이 | 허용 edge만 성공 |
| UT-DOM-004 | revision | 원본 불변, parent/hash 생성 |
| UT-DOM-005 | 기간 경계 | `[start,end)` 일관성 |
| UT-APP-001 | budget 차감 | 한도 초과 전 실행 차단 |
| UT-APP-002 | Program DAG | cycle과 dangling node 거절 |
| UT-APP-003 | fingerprint | key 순서와 입력 객체 순서에 독립 |
| UT-APP-004 | cache decision | 성공 동일 fingerprint만 재사용 |
| UT-APP-005 | cancellation | 안전 지점 이후 stage 미실행 |

### 4.2 정책과 검증

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| UT-POL-001 | static cost | 입력 bps 그대로 계산 |
| UT-POL-002 | unsupported policy | capability error |
| UT-POL-003 | decimal serialization | round-trip 동일 |
| UT-VAL-001 | 미래 정보 | hard fail |
| UT-VAL-002 | 회계 불일치 | hard fail |
| UT-VAL-003 | 공통→domain gate 순서 | 공통 fail 뒤 domain 미실행 |
| UT-VAL-004 | reason code | 모든 fail에 code 존재 |
| UT-VAL-005 | sealed feedback redaction | 상세 metric·selector 미노출 |

## 5. Contract Test

### 5.1 Lab Contract

8개 Lab 각각에 같은 parameterized suite를 적용한다.

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| CT-LAB-001 | plugin registration | domain key 1개당 plugin 1개 |
| CT-LAB-002 | valid question fixture | schema 통과 |
| CT-LAB-003 | missing required field | field path와 함께 실패 |
| CT-LAB-004 | unknown field | `extra_forbidden` 실패 |
| CT-LAB-005 | question→hypothesis | 반증 조건 유지 |
| CT-LAB-006 | hypothesis→strategy | domain과 engine key 유지 |
| CT-LAB-007 | validation rules | 공통 rule 뒤 domain rule 반환 |
| CT-LAB-008 | cross-domain isolation | 다른 Lab 내부 import·payload 수용 금지 |

### 5.2 Engine Contract

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| CT-ENG-001 | 같은 input/seed | result hash 동일 |
| CT-ENG-002 | input immutability | 실행 전후 config 동일 |
| CT-ENG-003 | required artifacts | RESULT와 DIAGNOSTICS 생성 |
| CT-ENG-004 | invalid config | engine 실행 전 표준 오류 |
| CT-ENG-005 | cancellation | 중간 artifact가 성공으로 등록되지 않음 |

### 5.3 LLM Contract

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| CT-LLM-001 | structured valid output | candidate JSON 반환 |
| CT-LLM-002 | malformed output | schema error, revision 미생성 |
| CT-LLM-003 | timeout/provider error | 표준 provider error |
| CT-LLM-004 | prompt content | 허용 evidence·capability·constraint만 포함 |
| CT-LLM-005 | sealed redaction | selector·metric 상세 0건 |

### 5.4 API Contract

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| CT-API-001 | endpoint table | OpenAPI route와 완전 일치 |
| CT-API-002 | envelope | 모든 response가 공통 meta/error 준수 |
| CT-API-003 | unknown request field | 400 `AF-SCHEMA-001` |
| CT-API-004 | idempotency | 같은 key/body 같은 job 반환 |
| CT-API-005 | CLI mapping | CLI JSON과 HTTP `data` schema 일치 |

## 6. Integration Test

### 6.1 SQLite와 Migration

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| IT-DB-001 | 빈 DB migration | 최신 version 도달 |
| IT-DB-002 | 직전 fixture upgrade | row/hash 손실 0건 |
| IT-DB-003 | FK/UNIQUE/CHECK | 위반 insert 전부 실패 |
| IT-DB-004 | optimistic update | stale row_version 실패 |
| IT-DB-005 | fingerprint race | experiment 1개만 생성 |
| IT-DB-006 | holdout race | lineage access 1개만 생성 |

### 6.2 Artifact

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| IT-ART-001 | atomic write | 성공 파일 hash 일치 |
| IT-ART-002 | write interruption | 최종 path와 DB reference 없음 |
| IT-ART-003 | dedup | 같은 content가 같은 path 재사용 |
| IT-ART-004 | restore verification | 누락·변조 artifact 탐지 |

### 6.3 Job

| ID | 검증 | 합격 조건 |
| --- | --- | --- |
| IT-JOB-001 | FIFO claim | oldest queued job 실행 |
| IT-JOB-002 | startup recovery | running job이 queued로 복구 |
| IT-JOB-003 | completed recovery | 완료 job 재실행 없음 |
| IT-JOB-004 | cancel | queued 즉시, running 안전 지점 취소 |

## 7. Numeric Oracle Test

### 7.1 Panel Portfolio Engine

| ID | Fixture | 합격 조건 |
| --- | --- | --- |
| NUM-FAC-001 | 3일·4자산 손계산 signal | rank와 weight 정확히 일치 |
| NUM-FAC-002 | 월말 rebalance 경계 | 미래 row 접근 0건 |
| NUM-FAC-003 | long/short cash ledger | NAV identity 오차 `<=1e-10` |
| NUM-FAC-004 | static 10bp cost | 거래대금×10bp 정확히 차감 |
| NUM-FAC-005 | missing/delisted asset | 명시 정책대로 처리 |

### 7.2 Multi-Leg Sequential Engine

| ID | Fixture | 합격 조건 |
| --- | --- | --- |
| NUM-SA-001 | 2-leg 완전 선형 series | hedge ratio oracle 일치 |
| NUM-SA-002 | entry/exit crossing | position transition 일치 |
| NUM-SA-003 | 비동기 timestamp | 허용 synchronization만 사용 |
| NUM-SA-004 | leg별 cost | gross-net identity 일치 |
| NUM-SA-005 | relation breakdown | domain rejection 발생 |

## 8. E2E Test

| ID | 시나리오 | 합격 조건 |
| --- | --- | --- |
| E2E-001 | 질문 지정 Factor | Mandate→VALIDATED Registry |
| E2E-002 | 영역 지시 Factor | fake LLM 후보→감사→결과 |
| E2E-003 | 질문 지정 StatArb | 전용 engine 결과 |
| E2E-004 | 6개 domain smoke | 올바른 plugin/engine route |
| E2E-005 | 질문 감사 실패 | Rejection + failure memory |
| E2E-006 | validation 실패 | 성공 Registry 미생성 |
| E2E-007 | duplicate experiment | 같은 experiment ID/hash |
| E2E-008 | sealed evaluation | 첫 요청 성공, 둘째 충돌 |
| E2E-009 | CLI/API parity | 같은 fingerprint와 status |
| E2E-010 | open discovery | 생성 가능, run은 release error |

## 9. 누수·속성·Red-Team Test

| ID | 공격 | 합격 조건 |
| --- | --- | --- |
| SEC-DATA-001 | 미래 `available_at` claim | generation context에서 제외 |
| SEC-DATA-002 | 미래 재무 row | experiment hard fail |
| SEC-DATA-003 | split 뒤 수정된 config | 새 lineage/fingerprint |
| SEC-LLM-001 | prompt injection in source | command로 실행되지 않음 |
| SEC-LLM-002 | 코드·SQL 출력 | schema 거절 |
| SEC-LLM-003 | sealed 결과 요청 | adapter 입력 전 redaction |
| SEC-LOG-001 | secret marker | log 검색 결과 0건 |
| SEC-PATH-001 | `../` artifact path | 저장 경로 이탈 차단 |
| PROP-001 | 임의 valid state sequence | 불법 terminal reversal 0건 |
| PROP-002 | 임의 decimal policy | gross-net accounting invariant |
| PROP-003 | JSON key permutation | fingerprint 동일 |

## 10. 장애 복구 테스트

| ID | 장애 지점 | 합격 조건 |
| --- | --- | --- |
| REC-001 | LLM 응답 전 process 종료 | candidate revision 없음 |
| REC-002 | engine artifact 임시 쓰기 중 종료 | 성공 metadata 없음 |
| REC-003 | artifact rename 후 DB commit 전 종료 | orphan cleanup 가능 |
| REC-004 | result commit 후 process 종료 | 재시작 시 중복 engine 실행 없음 |
| REC-005 | sealed access 예약 후 종료 | 두 번째 access 차단 |
| REC-006 | SQLite backup 복원 | FK와 artifact hash 100% 검증 |

## 11. 성능 테스트

| ID | 부하 | 목표 |
| --- | --- | --- |
| PERF-001 | job create 1,000회 | p95 500ms 이하, 오류 0.1% 미만 |
| PERF-002 | job query 10 concurrent clients | p95 500ms 이하 |
| PERF-003 | Factor 100,000 rows | 60초 이하, peak RSS 4GB 이하 |
| PERF-004 | StatArb 1,000,000 aligned points | 120초 이하, peak RSS 4GB 이하 |
| PERF-005 | 10,000 experiment lookup | fingerprint query p95 50ms 이하 |

성능 실패는 기능 test 성공으로 상쇄하지 않는다.

## 12. Acceptance Test

| ID | Acceptance Criteria | 실행 Test |
| --- | --- | --- |
| AT-001 | AC-001 | E2E-001 |
| AT-002 | AC-002 | E2E-002, E2E-003 |
| AT-003 | AC-003 | CT-LAB-001~008 |
| AT-004 | AC-004 | UT-POL-001, NUM-FAC-004 |
| AT-005 | AC-005 | UT-VAL-001, SEC-DATA-002 |
| AT-006 | AC-006 | UT-APP-003~004, E2E-007 |
| AT-007 | AC-007 | IT-DB-006, E2E-008, REC-005 |
| AT-008 | AC-008 | IT-JOB-002~003, REC-001~006 |
| AT-009 | AC-009 | CT-API-005, E2E-009 |
| AT-010 | AC-010 | 전체 MVP release gate |

## 13. 테스트 데이터 전략

```text
tests/fixtures/
├── contracts/
│   ├── questions/<domain>/valid.json
│   └── questions/<domain>/invalid_*.json
├── factor/
│   ├── tiny_panel.parquet
│   └── expected_result.json
├── statarb/
│   ├── tiny_legs.parquet
│   └── expected_result.json
├── llm/
│   ├── valid_candidates.json
│   └── malformed_candidates.json
├── leakage/
└── migrations/
```

- fixture는 synthetic 또는 재배포 허용 데이터만 사용한다.
- 각 fixture는 schema version, 생성 script version, SHA-256, 예상 시간 범위를 manifest에 기록한다.
- expected result를 engine 자체 코드로 생성하지 않는다.
- 무효 fixture는 실패해야 하는 정확한 path와 reason code를 함께 기록한다.

## 14. CI와 Release Gate

### Pull Request

- format, lint, type
- unit, contract, integration, E2E
- migration empty/upgrade
- OpenAPI diff와 architecture import test
- core·compiler·validation coverage 85% 이상

### Nightly

- property 10,000 cases
- recovery fault injection
- full numeric oracle
- performance baseline
- secret and dependency scan

### MVP Release

- PR와 nightly suite 모두 성공
- AT-001~AT-010 성공
- Windows/Linux 결과 hash 일치
- P0/P1 defect 0건
- flaky test 0건
- 빈 환경 install→migrate→Factor/StatArb demo 성공

### Beta/Production 추가 Gate

Beta는 다중 Worker, PostgreSQL migration, 실제 domain engine test를 추가한다. Production은 인증·권한, backup/restore, 부하, 장애 주입, 보안 scan과 사람 승인을 모두 요구한다.

## 15. 결함 분류

| 등급 | 예 | Release 처리 |
| --- | --- | --- |
| P0 | 데이터 손상, holdout 누출, 잘못된 전략 승인 | 즉시 중단, release 금지 |
| P1 | 재현 실패, 회계 오류, 복구 실패 | release 금지 |
| P2 | 비핵심 API 오류, 성능 목표 미달 | owner와 수정 release 확정 |
| P3 | 문구·개발 편의 문제 | backlog 허용 |

P0/P1 수정은 재현 test, 원인, 영향 범위, 회귀 test를 포함해야 한다.
