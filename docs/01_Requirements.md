# Alpha Foundry 요구사항

상태: Approved  
기준 릴리스: MVP  
요구사항 키워드 `MUST`, `MUST NOT`, `SHOULD`는 각각 필수, 금지, 권고를 뜻한다.

## 1. 프로젝트 목적

Alpha Foundry는 자연어 연구 지시를 도메인별 연구질문, 가설, 실험, 전략으로 변환하고 검증 결과에 따라 전략을 등록하거나 탈락시키는 퀀트 연구 운영 시스템이다.

시스템의 핵심 가치는 높은 백테스트 수치가 아니라 다음 네 가지다.

- 도메인에 맞는 질문과 실행 모델 사용
- 근거와 실패를 포함한 연구 계보 보존
- LLM 생성과 결정론적 판정 분리
- 같은 입력과 버전으로 같은 실험 결과 재현

## 2. 개발 목표

| ID | 목표 | 측정 방법 |
| --- | --- | --- |
| GOAL-01 | 자연어 지시를 실행 가능한 연구 흐름으로 변환 | 질문 지정·영역 지시 E2E 통과 |
| GOAL-02 | 도메인별 계약을 유지 | 8개 payload contract test 통과 |
| GOAL-03 | 실험의 계보와 재현성 확보 | 동일 fingerprint 재실행 결과 일치 |
| GOAL-04 | 탐색 편향과 holdout 누수 방지 | 탐색 원장·sealed holdout 공격 테스트 통과 |
| GOAL-05 | AI와 사람이 안전하게 병렬 개발 | 문서 ID 기반 추적과 모듈 경계 검사 통과 |

## 3. Scope

### 3.1 In Scope — MVP

- 질문 지정 모드와 영역 지시 모드
- Factor, StatArb, Market Making, Structural Flow, Cross Venue, Derivatives, Event Fundamental, Time Series 질문 계약
- 자연어 Mandate 구조화, Capability 확인, 도메인 라우팅
- 근거·반대근거·실패기억 조회와 질문 생성 컨텍스트 구성
- 질문 감사, 가설 생성, 전략 컴파일, 실험 컴파일
- Factor와 StatArb 엔진의 완전한 수직 실행
- 나머지 6개 도메인의 계약 검증과 고정 fixture smoke 실행
- 비용·지연시간·체결·슬리피지 정책의 명시적 설정
- 검증 관문, 탐색 원장, 계보, sealed holdout 1회 제한
- Strategy 또는 Rejection Registry 기록
- API와 CLI의 동등한 application service 호출

### 3.2 Out of Scope — MVP

- 개방형 탐색 모드 실행
- 실거래 주문 전송과 자동 승격
- LLM이 생성한 임의 코드 실행
- 전 도메인의 생산급 시뮬레이터
- 멀티테넌시, 다중 Worker, 분산 이벤트 브로커
- 웹 UI, 모바일 UI
- 외부 사용자를 위한 공개 API 운영
- 실시간 시장데이터 수집과 정제

Out of Scope 항목의 도입 시점은 [03_DevelopmentPlan.md](./03_DevelopmentPlan.md)가 결정한다.

## 4. 시스템 개요

입력은 `ResearchMandate`다. 시스템은 사용 가능한 데이터·엔진·설정을 확인한 뒤 주 도메인을 선택한다. LLM은 주어진 근거와 제약 안에서 구조화 후보를 생성한다. 결정론적 코드는 후보의 schema, 예산, 데이터 가용성, 시간 누수, 정책 적합성을 검사한다. 승인된 후보만 실험 엔진으로 전달된다. 검증 결과는 전략 등록 또는 명시적 탈락으로 종료된다.

전체 컴포넌트와 흐름은 [02_Architecture.md](./02_Architecture.md)를 따른다.

## 5. 사용자 유형

| 사용자 | 책임 | MVP 권한 |
| --- | --- | --- |
| Researcher | 연구 지시 제출, 결과 조회 | mandate 생성·조회, 실험 실행·조회 |
| Reviewer | 질문·가설·검증 결과 검토 | 승인·거절, holdout 실행 승인 |
| Maintainer | 데이터·엔진·config 등록 | capability 변경, 실패 작업 복구 |
| AI Developer | 문서 계약 기반 구현 | 코드 변경 제안, 테스트 작성; 운영 승인 불가 |

MVP 인증은 신뢰된 로컬 사용자로 제한한다. Production 인증·권한은 릴리스 범위에 따라 추가한다.

## 6. Use Case

### UC-001 질문 지정 연구

Researcher가 도메인, 질문, 데이터 요구, 예산을 제출한다. 시스템은 실행 가능성을 검증하고 가설·전략·실험을 생성하여 결과와 계보를 반환한다.

### UC-002 영역 지시 연구

Researcher가 연구 영역과 제약을 제출한다. 시스템은 Capability와 Knowledge를 근거로 질문 후보를 생성·감사하고 승인 후보를 실행한다.

### UC-003 근거 편입

Maintainer가 원문과 주장 정보를 등록한다. 이후 생성되는 질문은 사용한 근거와 반대근거의 ID를 기록한다.

### UC-004 Factor 수직 연구

시스템은 point-in-time panel에서 신호를 계산하고 포트폴리오를 구성하며 비용 적용 후 성과와 검증 결과를 생성한다.

### UC-005 StatArb 수직 연구

시스템은 동기화된 복수 자산 시계열에서 관계를 추정하고 순차적으로 포지션을 갱신하며 비용 적용 후 결과를 검증한다.

### UC-006 의도된 탈락

질문, 전략 또는 실험이 hard gate를 통과하지 못하면 시스템은 실패 단계, reason code, 근거를 기록하고 실행을 중단한다.

### UC-007 재현 실행

사용자가 기존 실험을 재실행하면 시스템은 고정된 데이터·config·코드·schema 버전을 사용해 같은 결과를 생성한다.

### UC-008 Sealed holdout

Reviewer 승인 후 계보당 한 번 sealed 구간을 평가한다. 결과 확인 뒤 같은 계보에서 재평가하거나 후보를 수정할 수 없다.

### UC-009 Strategy 등록

모든 필수 검증을 통과한 Strategy revision만 Registry에 등록된다. 자동 실거래 승격은 발생하지 않는다.

### UC-010 장애 복구

프로세스 종료 후 재시작하면 완료된 결과는 유지되고 대기 또는 실행 중이던 작업은 중복 결과 없이 재개되거나 명시적으로 실패 처리된다.

## 7. Functional Requirements

### 7.1 Mandate와 Capability

| ID | 요구사항 | Release |
| --- | --- | --- |
| FR-001 | 시스템은 질문 지정과 영역 지시 Mandate를 접수하고 고유 ID를 부여해야 한다. | MVP |
| FR-002 | Mandate는 mode, 목표, universe, 기간, 데이터 요구, 연구 예산, 금지 조건을 포함해야 한다. | MVP |
| FR-003 | 시스템은 실행 시작 시점의 데이터·엔진·config 가용성을 불변 snapshot으로 고정해야 한다. | MVP |
| FR-004 | 사용할 수 없는 데이터나 엔진이 필수이면 실행 전에 거절해야 한다. | MVP |
| FR-005 | 개방형 탐색 Mandate는 schema 검증까지 허용하고 실행은 거절해야 한다. | MVP |

### 7.2 Knowledge와 질문 생성

| ID | 요구사항 | Release |
| --- | --- | --- |
| FR-006 | 시스템은 원문, 주장, 반대관계, 실패기억을 독립 식별자로 관리해야 한다. | MVP |
| FR-007 | 생성 입력은 사용한 Knowledge ID와 Capability snapshot ID를 기록해야 한다. | MVP |
| FR-008 | LLM 출력은 등록된 schema로 검증되기 전에는 권위 상태에 반영되어서는 안 된다. | MVP |
| FR-009 | 영역 지시 모드는 최소 1개, 최대 `question_limit`개의 질문 후보를 생성해야 한다. | MVP |
| FR-010 | 질문 감사는 schema, 데이터 가용성, 반증 가능성, 예산, 누수 위험을 판정해야 한다. | MVP |
| FR-011 | 탈락 질문은 reason code와 감사 결과를 보존해야 한다. | MVP |

### 7.3 도메인과 연구 계약

| ID | 요구사항 | Release |
| --- | --- | --- |
| FR-012 | 각 질문은 공통 외피와 정확히 하나의 도메인 payload를 가져야 한다. | MVP |
| FR-013 | Domain Router는 하나의 주 도메인과 0개 이상의 허용 보조 interface를 반환해야 한다. | MVP |
| FR-014 | 8개 Lab은 동일한 plugin interface를 구현하되 질문·전략 schema는 독립적으로 소유해야 한다. | MVP |
| FR-015 | 도메인 간 결합은 등록된 interface를 통한 입력 또는 Meta Portfolio 배분으로만 허용해야 한다. | MVP |
| FR-016 | 시스템은 질문에서 반증 가능한 가설과 도메인별 StrategySpec을 생성해야 한다. | MVP |
| FR-017 | Research Program은 작업 의존성과 예산 배분을 비순환 그래프로 표현해야 한다. | MVP |

### 7.4 실험 실행

| ID | 요구사항 | Release |
| --- | --- | --- |
| FR-018 | Experiment Compiler는 StrategySpec, 데이터 버전, 기간, 정책, seed를 실행 계약으로 고정해야 한다. | MVP |
| FR-019 | 비용·지연시간·체결·슬리피지·시장충격·차입·펀딩은 사용자 config를 그대로 적용해야 한다. | MVP |
| FR-020 | 시스템은 config에 없는 현실 값을 임의 추정하거나 대체해서는 안 된다. | MVP |
| FR-021 | Factor 연구는 Panel Portfolio Engine으로 실제 실행되어야 한다. | MVP |
| FR-022 | StatArb 연구는 Multi-Leg Sequential Engine으로 실제 실행되어야 한다. | MVP |
| FR-023 | 나머지 6개 도메인은 schema·engine routing·고정 fixture smoke 결과를 제공해야 한다. | MVP |
| FR-024 | 동일 fingerprint의 완료 실험은 중복 계산 없이 기존 결과를 반환해야 한다. | MVP |
| FR-025 | 모든 후보 변경과 실행은 탐색 원장에 append-only로 기록되어야 한다. | MVP |

### 7.5 검증과 결과

| ID | 요구사항 | Release |
| --- | --- | --- |
| FR-026 | Validation Registry는 공통 관문과 도메인 관문을 순서대로 실행해야 한다. | MVP |
| FR-027 | 시간 누수, 회계 불일치, schema 위반은 항상 hard fail이어야 한다. | MVP |
| FR-028 | sealed holdout은 Reviewer 승인 후 계보당 최대 한 번 실행되어야 한다. | MVP |
| FR-029 | LLM은 sealed holdout 데이터, metric, 상세 실패 원인을 입력으로 받아서는 안 된다. | MVP |
| FR-030 | 검증 통과 Strategy revision은 Strategy Registry에 등록되어야 한다. | MVP |
| FR-031 | 검증에서 탈락한 Strategy revision은 Rejection Registry와 실패기억에 reason code와 함께 기록되어야 한다. | MVP |
| FR-032 | 결과는 입력·데이터·config·schema·코드 버전과 artifact hash를 포함해야 한다. | MVP |

### 7.6 Interface와 작업

| ID | 요구사항 | Release |
| --- | --- | --- |
| FR-033 | API와 CLI는 동일한 application service를 호출하고 같은 검증 결과를 반환해야 한다. | MVP |
| FR-034 | 장시간 작업은 job ID와 상태를 제공하고 재시작 후 조회 가능해야 한다. | MVP |
| FR-035 | 취소 요청은 아직 시작하지 않은 작업을 취소하고 실행 중 작업에는 안전 지점에서 적용해야 한다. | MVP |
| FR-036 | 각 상태 변경은 actor, timestamp, 이전 상태, 새 상태, reason을 기록해야 한다. | MVP |

## 8. Non-Functional Requirements

| ID | 요구사항 | 합격 기준 |
| --- | --- | --- |
| NFR-001 | 재현성 | 같은 fingerprint와 환경에서 주요 metric 허용 오차 `1e-10` 이내 |
| NFR-002 | 결정론 | seed와 정렬 순서를 고정하고 비결정 원천을 결과에 기록 |
| NFR-003 | 데이터 무결성 | point-in-time 및 시간대 규칙 위반 테스트 100% 차단 |
| NFR-004 | 신뢰성 | 프로세스 강제 종료 후 완료 artifact 손실 0건 |
| NFR-005 | 성능 | 10만 행 Factor fixture를 기준 개발 장비에서 60초 이내 처리 |
| NFR-006 | API 응답 | job 생성·조회 p95 500ms 이하, 엔진 시간 제외 |
| NFR-007 | 보안 | 비밀값·원문 데이터·sealed selector가 log에 노출되지 않음 |
| NFR-008 | 관측성 | 모든 job log가 correlation_id, job_id, stage, duration을 포함 |
| NFR-009 | 유지보수성 | 순환 import 0건, 모듈 public interface 외 import 0건 |
| NFR-010 | 테스트 | core·compiler·validation statement coverage 85% 이상 |
| NFR-011 | 호환성 | 지원 OS에서 동일 fixture 결과 일치: Windows, Linux |
| NFR-012 | 문서 일치 | 공개 API와 schema가 생성된 OpenAPI 및 문서와 일치 |

## 9. Constraints

- MVP 기간은 4 개발일이며 전 도메인 생산급 엔진 완성을 목표로 하지 않는다.
- 도메인별 질문과 전략을 단일 범용 수식으로 축소하지 않는다.
- LLM이 생성한 Python, SQL, shell을 실행하지 않는다.
- 최종 전략 승격에는 사람의 승인이 필요하다.
- 입력 config는 시스템의 현실 추정보다 우선한다.
- 데이터 license와 외부 LLM provider 약관을 준수해야 한다.

## 10. Assumptions

- MVP 데이터는 정제된 fixture 또는 사용자가 제공한 point-in-time dataset이다.
- MVP는 신뢰된 단일 조직의 로컬 또는 CI 환경에서 실행된다.
- 외부 LLM provider는 JSON schema 기반 structured output 또는 동등한 검증 경로를 제공한다.
- Factor와 StatArb 예제는 재배포 가능한 synthetic fixture를 사용한다.
- 한 job의 MVP 입력은 단일 개발 장비 메모리에서 처리 가능하다.

## 11. Acceptance Criteria

| ID | 인수 조건 | 관련 요구사항 |
| --- | --- | --- |
| AC-001 | 질문 지정 Factor 예제가 Mandate부터 Registry까지 완료된다. | FR-001, FR-016, FR-021, FR-026, FR-030 |
| AC-002 | 영역 지시 StatArb 예제가 근거 포함 질문 생성부터 결과까지 완료된다. | FR-006~FR-011, FR-022 |
| AC-003 | 8개 도메인 payload의 유효·무효 fixture contract test가 통과한다. | FR-012~FR-014, FR-023 |
| AC-004 | static cost 10bp 입력 시 엔진이 정확히 10bp를 적용한다. | FR-019, FR-020 |
| AC-005 | 미래 시점 데이터가 포함된 입력이 실행 전에 차단된다. | FR-010, FR-027, NFR-003 |
| AC-006 | 같은 실험을 두 번 요청하면 같은 experiment ID와 결과 hash가 반환된다. | FR-024, NFR-001 |
| AC-007 | 계보의 두 번째 sealed holdout 요청이 차단된다. | FR-028, FR-029 |
| AC-008 | 강제 종료 후 재시작해 완료 결과를 조회하고 미완료 job을 복구한다. | FR-034, NFR-004 |
| AC-009 | API와 CLI로 동일 입력을 실행했을 때 같은 fingerprint를 생성한다. | FR-033 |
| AC-010 | 모든 필수 테스트와 문서 일치 검사가 CI에서 통과한다. | NFR-009~NFR-012 |

## 12. 참조

- 구현 구조: [02_Architecture.md](./02_Architecture.md)
- 릴리스와 작업 순서: [03_DevelopmentPlan.md](./03_DevelopmentPlan.md)
- 외부 계약: [04_API.md](./04_API.md)
- 영속 계약: [05_Database.md](./05_Database.md)
- 검증 방법: [07_TestPlan.md](./07_TestPlan.md)
- 중요한 설계 근거: [ADR](./ADR/)
