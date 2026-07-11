# Alpha Foundry 문서 체계

| 항목       | 값                                                |
| -------- | ------------------------------------------------ |
| 문서 세트 버전 | 1.0.0                                            |
| 기준일      | 2026-07-11                                       |
| 상태       | Baseline                                         |
| 대상       | 사람 개발자, 퀀트 연구자, 검토자, ChatGPT, Codex, Claude Code |

이 디렉터리는 Alpha Foundry의 제품·설계·구현·검증에 관한 Single Source of Truth다. 각 사실은 한 문서만 소유하며, 다른 문서는 ID 또는 상대 링크로 참조한다. 코드, 마이그레이션, OpenAPI, 테스트가 문서와 다르면 CI가 실패해야 하며 어느 한쪽을 임의로 우선하지 않는다.

## 1. 프로그램 전체 구조

다음 그림은 Alpha Foundry의 실행 구조와 데이터 흐름을 한 장으로 요약한다. 실선은 코드가 수행하는 결정론 처리이며, 점선은 LLM 호출 구간이다. LLM은 질문·가설·설명 초안 생성에만 관여하고, 상태 전이·수치 계산·검증·영속 저장은 코드 영역에서만 수행한다.

```mermaid
flowchart LR
    classDef human fill:#fff7ed,stroke:#f97316,color:#111827
    classDef code fill:#eff6ff,stroke:#2563eb,color:#111827
    classDef llm fill:#f5f3ff,stroke:#7c3aed,color:#111827,stroke-dasharray: 6 4
    classDef engine fill:#ecfdf5,stroke:#059669,color:#111827
    classDef store fill:#f8fafc,stroke:#475569,color:#111827
    classDef event fill:#fefce8,stroke:#ca8a04,color:#111827
    classDef guard fill:#fee2e2,stroke:#dc2626,color:#111827

    subgraph USERS["사용자와 외부 진입점"]
        direction TB
        U1["Researcher<br/>Mandate·Data Universe·Config 입력"]
        U2["Reviewer<br/>승인·반려·Holdout 공개 승인"]
        U3["Operator<br/>배포·복구·감사 확인"]
        U4["External Client<br/>API·CLI 자동화"]
    end

    subgraph CODE["CODE AREA: 결정론 실행과 권위 상태<br/>시작: Web/API/CLI 요청 수신<br/>종료: DB transaction, event outbox, artifact 기록, HTTP/CLI 응답"]
        direction LR

        subgraph ENTRY["Interface Layer"]
            direction TB
            E1["Web UI"]
            E2["REST API<br/>FastAPI"]
            E3["CLI<br/>Typer"]
            E4["AuthN/AuthZ<br/>OIDC·RBAC"]
            E5["Request Validator<br/>OpenAPI·Pydantic"]
        end

        subgraph APP["Application Services"]
            direction TB
            A1["Mandate Service<br/>research goal·universe·constraints 접수"]
            A2["Question Service<br/>domain question lifecycle"]
            A3["Hypothesis Service<br/>claim·rationale·evidence 연결"]
            A4["Strategy Service<br/>strategy spec·engine contract 생성"]
            A5["Experiment Service<br/>job 생성·config fingerprint"]
            A6["Validation Service<br/>G0~G10 gate 실행"]
            A7["Registry Service<br/>승인 전략·실험 결과 등록"]
            A8["Review Service<br/>사람 승인·감사 로그"]
        end

        subgraph LLMADAPTER["LLM Adapter: 코드가 통제하는 호출 전후 처리"]
            direction TB
            P1["Prompt Builder<br/>권한·context·domain schema 조립"]
            P2["Redaction Guard<br/>secret·PII·sealed holdout 제거"]
            P3["Schema Enforcer<br/>JSON Schema·domain discriminator 검증"]
            P4["Draft Normalizer<br/>초안 ID·version·provenance 부여"]
            P5["Rejection Handler<br/>schema 위반·근거 부족·재시도 제한"]
        end

        subgraph JOBS["Durable Job Runtime"]
            direction TB
            J1["Job Queue<br/>PostgreSQL lease·heartbeat"]
            J2["Experiment Runner<br/>deterministic execution"]
            J3["Cache Resolver<br/>fingerprint hit/miss"]
            J4["Artifact Writer<br/>report·metrics·trace 저장"]
            J5["Outbox Publisher<br/>transactional event 발행"]
        end

        subgraph POLICIES["Execution Policies"]
            direction TB
            C1["Cost Model<br/>commission·fees·borrow·funding"]
            C2["Latency Model<br/>signal delay·venue delay"]
            C3["Fill Model<br/>queue·partial fill·slippage"]
            C4["Risk Model<br/>exposure·turnover·drawdown"]
            C5["Calendar/Universe<br/>trading days·assets·venues"]
        end

        subgraph ENGINES["Domain Labs and Engines"]
            direction TB
            G1["Factor/Portfolio Engine<br/>factor exposure·optimizer"]
            G2["StatArb Engine<br/>spread·cointegration·residual checks"]
            G3["Market Making Engine<br/>inventory·quote·queue simulation"]
            G4["Structural Flow Engine<br/>order flow·liquidity imbalance"]
            G5["Cross Venue Engine<br/>basis·latency·transfer constraint"]
            G6["Derivatives RV Engine<br/>vol surface·greeks·calendar spread"]
            G7["Event/Fundamental Engine<br/>event window·surprise·claim mapping"]
            G8["Time Series Engine<br/>forecast·regime·feature lag"]
            G9["Meta Portfolio Engine<br/>approved strategy composition"]
        end

        subgraph DATA["Storage and Source of Truth"]
            direction TB
            D1[("PostgreSQL<br/>metadata·state machines·jobs·ledger·registry")]
            D2[("Parquet Lake<br/>market data·features·backtest slices")]
            D3[("Object Storage<br/>artifacts·reports·large traces")]
            D4[("Git Repository<br/>code·docs·config templates·migrations")]
            D5[("Schema Registry<br/>API/event/domain payload versions")]
        end

        subgraph OUTPUT["Outputs"]
            direction TB
            O1["Question Set<br/>domain-discriminated candidates"]
            O2["Hypothesis Record<br/>claims·evidence·risk notes"]
            O3["Strategy Program<br/>engine-bound executable spec"]
            O4["Experiment Result<br/>metrics·diagnostics·lineage"]
            O5["Validation Report<br/>G0~G10 pass/fail"]
            O6["Strategy Registry Entry<br/>approved·rejected·deprecated"]
        end

        subgraph CONTROL["Hard Guards"]
            direction TB
            X1["LLM 직접 DB write 금지"]
            X2["LLM 숫자·성과·gate 판정 금지"]
            X3["sealed holdout 1회 공개와 감사 로그"]
            X4["모든 상태 변경은 idempotency key와 version 검사"]
        end
    end

    subgraph LLMZONE["LLM AREA: 비결정론 생성 구간<br/>시작: LLM Adapter가 redacted prompt와 target schema를 전송<br/>종료: draft JSON 또는 refusal 반환"]
        direction TB
        L1["Question Drafting<br/>domain별 research question 후보"]
        L2["Hypothesis Drafting<br/>claim·rationale·test idea 초안"]
        L3["Strategy Drafting<br/>engine contract에 맞는 spec 초안"]
        L4["Narrative Drafting<br/>사람 검토용 설명·리스크 요약"]
    end

    subgraph EVENTS["Event and Observation Flow"]
        direction TB
        V1["af.question.created.v1"]
        V2["af.hypothesis.promoted.v1"]
        V3["af.experiment.completed.v1"]
        V4["af.validation.failed.v1"]
        V5["af.strategy.registered.v1"]
        V6["Metrics·Logs·Traces"]
    end

    U1 --> E1
    U2 --> E1
    U3 --> E1
    U4 --> E2
    U4 --> E3

    E1 --> E4
    E2 --> E4
    E3 --> E4
    E4 --> E5
    E5 --> A1

    A1 --> A2
    A2 -->|"초안 생성 요청"| P1
    A3 -->|"초안 생성 요청"| P1
    A4 -->|"초안 생성 요청"| P1
    A7 -->|"검토 설명 요청"| P1

    P1 --> P2
    P2 -.->|"redacted prompt + schema"| L1
    P2 -.->|"redacted prompt + schema"| L2
    P2 -.->|"redacted prompt + schema"| L3
    P2 -.->|"result facts only + schema"| L4
    L1 -.->|"draft JSON"| P3
    L2 -.->|"draft JSON"| P3
    L3 -.->|"draft JSON"| P3
    L4 -.->|"draft JSON"| P3
    P3 -->|"valid"| P4
    P3 -->|"invalid/refusal"| P5
    P4 --> A2
    P4 --> A3
    P4 --> A4
    P4 --> A7
    P5 --> A2
    P5 --> A3
    P5 --> A4

    A2 --> O1
    O1 --> A3
    A3 --> O2
    O2 --> A4
    A4 --> O3
    O3 --> A5

    A5 --> J1
    J1 --> J2
    J2 --> J3
    J3 -->|"cache miss"| POLICIES
    POLICIES --> ENGINES
    G1 --> A6
    G2 --> A6
    G3 --> A6
    G4 --> A6
    G5 --> A6
    G6 --> A6
    G7 --> A6
    G8 --> A6
    G9 --> A6
    J3 -->|"cache hit"| A6
    A6 --> O5
    O5 --> A8
    A8 -->|"approved"| A7
    A8 -->|"rejected"| O6
    A7 --> O6

    A1 --> D1
    A2 --> D1
    A3 --> D1
    A4 --> D1
    A5 --> D1
    A6 --> D1
    A7 --> D1
    A8 --> D1
    J1 --> D1
    J2 --> D2
    J4 --> D3
    A4 --> D4
    P3 --> D5
    E5 --> D5
    D2 --> J2
    D4 --> J2
    D5 --> P3

    J2 --> O4
    O4 --> J4
    O4 --> A6
    D1 --> J5
    J5 --> V1
    J5 --> V2
    J5 --> V3
    J5 --> V4
    J5 --> V5
    J4 --> V6

    X1 -.->|"enforced by adapter and DB permissions"| P3
    X2 -.->|"enforced by validation service"| A6
    X3 -.->|"enforced by review service"| A8
    X4 -.->|"enforced by API and DB constraints"| E5

    class U1,U2,U3,U4 human
    class E1,E2,E3,E4,E5,A1,A2,A3,A4,A5,A6,A7,A8,P1,P2,P3,P4,P5,J1,J2,J3,J4,J5,C1,C2,C3,C4,C5,O1,O2,O3,O4,O5,O6 code
    class L1,L2,L3,L4 llm
    class G1,G2,G3,G4,G5,G6,G7,G8,G9 engine
    class D1,D2,D3,D4,D5 store
    class V1,V2,V3,V4,V5,V6 event
    class X1,X2,X3,X4 guard
```

## 2. 확정 문서 구조

```text
docs/
├── README.md
├── 01_Requirements.md
├── 02_Architecture.md
├── 03_DevelopmentPlan.md
├── 04_API.md
├── 05_Database.md
├── 06_CodingGuidelines.md
├── 07_TestPlan.md
└── ADR/
    ├── ADR-0001-modular-monolith.md
    ├── ADR-0002-domain-discriminated-contracts.md
    ├── ADR-0003-domain-specific-backtest-engines.md
    ├── ADR-0004-llm-deterministic-boundary.md
    ├── ADR-0005-storage-topology.md
    ├── ADR-0006-experiment-lineage-and-sealed-holdout.md
    ├── ADR-0007-durable-asynchronous-jobs.md
    └── ADR-0008-cross-domain-composition.md
```

## 3. 문서별 소유권

| 문서                       | 단독 소유하는 내용                                    | 포함하지 않는 내용                      |
| ------------------------ | --------------------------------------------- | ------------------------------- |
| `01_Requirements.md`     | 목적, 범위, 사용자, 유스케이스, FR/NFR, 제약, 가정, 인수 조건     | 프레임워크, 테이블·컬럼, 엔드포인트 상세, 클래스 구현 |
| `02_Architecture.md`     | 컴포넌트, 경계, 의존 방향, 데이터·이벤트 흐름, 상태 전이, 배포·런타임 구조 | FR 본문 반복, 컬럼 사전, HTTP 필드 사전     |
| `03_DevelopmentPlan.md`  | 단계, 작업 패키지, 선후 관계, 병렬화, 마일스톤, 완료 정의           | 제품 요구의 재해석, API·DB 계약 재정의       |
| `04_API.md`              | 외부·내부 인터페이스, 요청·응답 스키마, 오류, 이벤트, 버전 규칙        | 물리 테이블, 인덱스, 배포 구조              |
| `05_Database.md`         | 영속 모델, ERD, 컬럼, 키, 제약, 인덱스, 파티션, 보존, 마이그레이션   | HTTP 경로, 요청·응답 예시               |
| `06_CodingGuidelines.md` | 언어·코딩·의존성·오류·로그·비동기·DI·리뷰 규칙                  | 제품 범위, 스프린트 일정                  |
| `07_TestPlan.md`         | 테스트 계층, 데이터, 성능·복구·통계·인수 검증, 품질 게이트           | 구현 설계의 대체 설명                    |
| `ADR/*`                  | 중요한 선택의 맥락, 대안, 결정 이유, 결과, 재검토 조건             | 현재 계약의 중복 사본                    |

## 4. 확정 목차

### `01_Requirements.md`

문서 제어 → 목적과 목표 → 범위 → 시스템 개요 → 사용자·권한 → 유스케이스 → 기능 요구사항 → 도메인별 기능 요구사항 → 비기능 요구사항 → 제약 → 가정 → 인수 조건 → 추적성.

### `02_Architecture.md`

설계 원칙 → 시스템 컨텍스트 → 런타임 컴포넌트 → 모듈 책임과 의존성 → 핵심 계약 → 데이터 흐름 → 이벤트 흐름 → 상태 머신 → 동시성·멱등성 → 저장·캐시 → 디렉터리 → 배포 → 시퀀스 → 보안·관측성 → 장애 처리 → ADR 연결.

### `03_DevelopmentPlan.md`

계획 기준 → 릴리스 수준 → 의존성 그래프 → MVP 4일 → Beta 작업 패키지 → Production 작업 패키지 → 모듈별 작업 → 병렬화 → 마일스톤 → 검증·인계 → 리스크 → Definition of Done.

### `04_API.md`

계약 원칙 → 공통 표현 → 인증·권한 → 공통 스키마 → 도메인 판별형 스키마 → 리소스 스키마 → 엔드포인트 → 장시간 작업 → 이벤트 → 오류 → 멱등성·동시성 → 페이지네이션 → 버전 → OpenAPI 일치 규칙.

### `05_Database.md`

저장 원칙 → ERD → 타입·시간 규칙 → 테이블 사전 → 무결성 → 인덱스 → 파티션 → 시계열·객체 저장 연결 → 보존 → 백업·복구 → 마이그레이션 → 접근 통제.

### `06_CodingGuidelines.md`

도구 체인 → 이름·형식 → 모듈 경계 → 타입·스키마 → 오류 → 로그·관측성 → 비동기·동시성 → DI → 결정론 → LLM 안전 → 데이터·시간·단위 → 저장소 → 보안 → 테스트 → 리뷰·브랜치 → 문서 동기화 → 금지 패턴.

### `07_TestPlan.md`

목표 → 환경·책임 → 테스트 피라미드 → 단위 → 계약 → 통합 → E2E → 통계·누수 → 속성 기반 → 성능 → 복구·장애 → 보안 → 인수 → 테스트 데이터 → 커버리지·CI → 결함 처리 → FR/NFR 추적성.

## 5. 식별자와 추적성 규칙

| 대상       | 형식                | 예시                           |
| -------- | ----------------- | ---------------------------- |
| 기능 요구사항  | `FR-NNN`          | `FR-031`                     |
| 비기능 요구사항 | `NFR-NNN`         | `NFR-008`                    |
| 유스케이스    | `UC-NNN`          | `UC-004`                     |
| 인수 조건    | `AC-NNN`          | `AC-012`                     |
| 작업 패키지   | `WP-{단계}-{NN}`    | `WP-B2-03`                   |
| 테스트      | `T-{계층}-{NNN}`    | `T-E2E-006`                  |
| 오류 코드    | `AF-{영역}-{NNN}`   | `AF-HOLDOUT-001`             |
| 이벤트 타입   | `af.{영역}.{사건}.v1` | `af.experiment.completed.v1` |
| ADR      | `ADR-NNNN`        | `ADR-0004`                   |

요구사항 변경은 관련 테스트 ID와 인수 조건을 함께 변경한다. API 스키마 변경은 계약 테스트와 OpenAPI snapshot을, DB 변경은 마이그레이션 테스트를, 설계 결정 변경은 ADR 상태와 현재 문서를 함께 변경한다.

## 6. 충돌 방지 규칙

1. 제품이 무엇을 해야 하는지는 `01_Requirements.md`만 결정한다.
2. 외부에서 관찰되는 형식은 `04_API.md`가 소유하고, 영속 형식은 `05_Database.md`가 소유한다.
3. ADR은 결정의 역사와 이유를 보존한다. Accepted ADR의 결정이 현재 문서에 반영되지 않은 변경은 병합할 수 없다.
4. 실행 가능한 JSON Schema와 OpenAPI는 `04_API.md`의 계약을 기계 검증한 산출물이다. 불일치는 우선순위로 해결하지 않고 빌드를 실패시킨다.
5. SQLAlchemy 모델과 Alembic migration은 `05_Database.md`의 제약을 구현한다. DB drift 검사 실패 시 배포하지 않는다.
6. 문서에 없는 임의 기본값을 AI가 발명하지 않는다. 입력 누락이 계약상 허용되지 않으면 명시적 오류로 중단한다.
7. 단위는 필드명 또는 타입으로 고정한다. `bps`, `ms`, `seconds`, ISO 8601 UTC를 혼용하지 않는다.

## 7. 변경 영향 매트릭스

| 변경             | 반드시 함께 검토할 문서·산출물                                                             |
| -------------- | ----------------------------------------------------------------------------- |
| FR/NFR 추가·변경   | Requirements, TestPlan, 관련 API 또는 Architecture, 추적성 검사                        |
| 도메인 payload 변경 | API, Architecture의 계약 참조, schema fixture, contract test, schema registry      |
| 상태 전이 변경       | Architecture, API event, Database constraint, E2E test, ADR                   |
| 엔진 계약 변경       | Architecture, API의 Strategy/Result, CodingGuidelines, contract test, ADR-0003 |
| 테이블·인덱스 변경     | Database, Alembic migration, migration/recovery test                          |
| 비용·지연·체결 정책 변경 | API config schema, experiment fingerprint test, 관련 엔진 contract test           |
| holdout 정책 변경  | Requirements, Architecture, Database, TestPlan, ADR-0006                      |
| 배포 토폴로지 변경     | Architecture, DevelopmentPlan, operations test, 관련 ADR                        |

## 8. 누락 항목 검토 결과

원 기획서의 제품 개념은 유지하되 구현 가능한 기준선에 필요했던 다음 항목을 문서 체계에 추가했다.

| 보완 항목                   | 확정 위치                                    | 처리 원칙                                                        |
| ----------------------- | ---------------------------------------- | ------------------------------------------------------------ |
| 인증·역할·사람 승인             | Requirements, API, Architecture          | OIDC 주체와 RBAC를 사용하고 AI 주체는 승인 역할을 가질 수 없다.                   |
| 장시간 작업 수명주기             | Architecture, API, Database              | 영속 job, lease, heartbeat, 재시도, 취소를 명시한다.                     |
| 멱등성·낙관적 동시성             | API, Database                            | 변경 요청은 idempotency key와 resource version으로 보호한다.             |
| 이벤트 전달 신뢰성              | Architecture, Database                   | transactional outbox로 상태 변경과 이벤트 기록을 원자화한다.                  |
| 스키마 진화                  | API, Database                            | 판별자와 schema version을 고정하고 breaking change는 새 major API로 낸다.  |
| 시간·단위·시점 의미             | API, Database, CodingGuidelines          | UTC, point-in-time 필드, 명시 단위 타입을 강제한다.                       |
| 데이터·산출물 보존              | Database                                 | 보존 등급, 삭제 조건, legal hold, lineage 보존을 명시한다.                  |
| 보안·비밀·PII               | Architecture, CodingGuidelines, TestPlan | 비밀은 외부 secret store에 두고 로그·LLM 입력에서 제거한다.                    |
| 관측성·복구                  | Architecture, TestPlan                   | 구조화 로그, metric, trace, RPO/RTO, lease 회수를 검증한다.              |
| 통계 검증의 적용 조건            | Requirements, TestPlan                   | 정상성이 가정인 시계열·잔차에만 ADF와 KPSS를 함께 적용한다.                        |
| 4일 범위의 현실성              | DevelopmentPlan                          | 두 연구소만 완전 수직 구현하고 나머지는 계약·synthetic 실행까지 완료한다.               |
| SQLite/PostgreSQL 의미 차이 | ADR-0005                                 | MVP부터 PostgreSQL을 사용해 JSON, constraint, job claim 의미를 단일화한다. |

## 9. AI 구현 순서

AI 에이전트는 작업 시작 시 다음 순서를 따른다.

1. 대상 `FR-*`, `NFR-*`, `AC-*`를 선택한다.
2. 관련 ADR과 Architecture 경계를 읽는다.
3. API 또는 DB 계약 중 변경 대상의 소유 문서를 읽는다.
4. `03_DevelopmentPlan.md`의 작업 패키지와 선행 조건을 확인한다.
5. 실패 테스트를 먼저 추가하고 최소 구현을 수행한다.
6. 계약·통합·누수 테스트를 실행한다.
7. 문서·OpenAPI·migration·fixture drift 검사를 실행한다.
8. 변경 요약에 요구사항 ID, 테스트 ID, ADR 영향을 기록한다.

한 에이전트가 동시에 하나의 영속 스키마 파일을 소유한다. 병렬 작업은 `03_DevelopmentPlan.md`의 파일 소유권 경계를 따른다.

## 10. 원 기획서 내용의 권위 위치

| 원 기획 주제                                  | 권위 문서                                             |
| ---------------------------------------- | ------------------------------------------------- |
| 제품 정의, 입력 모드, 비목표                        | `01_Requirements.md` 1~4장                         |
| 8개 연구소와 도메인 경계                           | Requirements 7.6장, Architecture 6장, ADR-0002/0003 |
| mandate/question/hypothesis/strategy 구분  | Requirements 4장, API 5·7장                         |
| 상태 머신과 승인 불가 전이                          | Architecture 9장                                   |
| 공통·도메인 질문 schema                         | API 5~6장                                          |
| 연구소 plugin과 engine 계약                    | Architecture 5장, API 7~8장                         |
| 도메인별 engine 기능과 검증                       | Requirements 7.6~7.7장, TestPlan 5·9장              |
| Meta Portfolio와 도메인 결합                   | Requirements FR-031~033/053, ADR-0008             |
| 질문 생성 비율·심사·품질 관문                        | Requirements FR-018~025, API 9.4장                 |
| 원문·claim·wiki·실패 기억                      | Architecture 4·7장, Database 5장                    |
| 비용·지연·체결·슬리피지 정책                         | Requirements FR-034~036, API 8장                   |
| fingerprint·cache·재현성                    | Architecture 7·11장, ADR-0005/0006                 |
| G0~G10, 통계, 다중 탐색, feedback              | Requirements 7.7장, TestPlan 9~10장                 |
| repository·deployment·service dependency | Architecture 12~14장                               |
| metadata table·보존·migration              | Database 전체                                       |
| API·event·error·version                  | API 9~16장                                         |
| LLM/결정론 실행 규칙                            | CodingGuidelines 12장, ADR-0004                    |
| 4일 MVP와 생산급 확장                           | DevelopmentPlan 전체                                |
| 납품·인수·공격 테스트                             | DevelopmentPlan 20~21장, TestPlan 10~18장           |
