# Alpha Foundry 프로젝트 문서

이 디렉터리는 Alpha Foundry 구현의 Single Source of Truth다. 자연어 기획보다 이 문서 집합의 명시적 계약이 우선하며, 문서끼리 충돌하면 아래의 문서별 권위 규칙을 따른다.

처음 보는 사람은 [`PROGRAM_GUIDE.md`](./PROGRAM_GUIDE.md)만 먼저 읽으면 사용자 workflow, article 구조, CLI, LLM 경계, DB, 검색·검증·출판 구성까지 전체를 파악할 수 있다.

## 프로그램 전체 구조

아래 이미지를 누르면 SVG 원본을 새 탭에서 크게 볼 수 있다.

<a href="./docs/diagrams/alpha_foundry_overview.svg">
  <img src="./docs/diagrams/alpha_foundry_overview.svg" alt="Alpha Foundry 전체 구조" width="100%">
</a>

- Mermaid 원본: [alpha_foundry_overview.mmd](./docs/diagrams/alpha_foundry_overview.mmd)
- SVG 이미지: [alpha_foundry_overview.svg](./docs/diagrams/alpha_foundry_overview.svg)

다이어그램 해석 규칙:

- 파란 영역은 결정론적 코드다. 상태 변경, 예산 차감, 실행, 검증, 등록은 이 영역만 수행한다.
- 노란 영역은 LLM이다. LLM은 질문·가설·전략 후보를 생성하지만 권위 상태를 직접 변경하지 않는다.
- 실선은 코드 내부 데이터 흐름이고 점선은 LLM 호출 경계다.
- Knowledge Plane과 Capability Plane의 정보는 Orchestrator가 선택해 LLM 요청에 포함한다.

## 문서 구조

```text
docs/
├── 01_Requirements.md
├── 02_Architecture.md
├── 03_DevelopmentPlan.md
├── 04_API.md
├── 05_Database.md
├── 06_CodingGuidelines.md
├── 07_TestPlan.md
├── reference.md
├── diagrams/
│   ├── alpha_foundry_overview.mmd
│   └── alpha_foundry_overview.svg
└── ADR/
    ├── ADR-0001-modular-monolith.md
    ├── ADR-0002-domain-discriminated-contracts.md
    ├── ADR-0003-domain-specific-backtest-engines.md
    ├── ADR-0004-llm-deterministic-boundary.md
    ├── ADR-0005-storage-topology.md
    ├── ADR-0006-experiment-lineage-and-sealed-holdout.md
    ├── ADR-0007-durable-asynchronous-jobs.md
    ├── ADR-0008-cross-domain-composition.md
    ├── ADR-0009-finite-deterministic-search-and-trial-ledger.md
    └── ADR-0010-validation-holdout-and-publication.md
```

## 문서별 권위

| 문서 | 이 문서만 결정하는 내용 |
| --- | --- |
| `01_Requirements.md` | 목적, 범위, 사용자 결과, FR/NFR, 인수 조건 |
| `02_Architecture.md` | 컴포넌트 책임, 의존 방향, 데이터 흐름, 런타임 구조 |
| `03_DevelopmentPlan.md` | 릴리스 범위, 작업 순서, 의존성, 마일스톤 |
| `04_API.md` | 외부 요청·응답·오류·이벤트 계약 |
| `05_Database.md` | 영속 모델, 키, 제약, 인덱스, 보존, 마이그레이션 |
| `06_CodingGuidelines.md` | 코드 작성·리뷰·오류·로그·비동기 규칙 |
| `07_TestPlan.md` | 테스트 수준, 데이터, 합격 기준, 릴리스 게이트 |
| `ADR/*` | 변경 비용이 큰 설계 결정과 재검토 조건 |

같은 계약을 여러 문서에 복사하지 않는다. 다른 문서의 정보가 필요하면 링크와 ID로 참조한다.

## 구현 기준선

### MVP

- 질문 지정 모드와 영역 지시 모드, 불변 KnowledgePack/claim과 CapabilitySnapshot
- Factor, StatArb, Market Making, Structural Flow, Cross Venue, Derivatives, Event Fundamental, Time Series의 독립 typed JSON DSL·compiler 계약과 연구급 수치 엔진
- 각 도메인의 point-in-time 데이터 계약, 명시적 비용·지연·체결·slippage·impact·borrow·funding·회계 정책, synthetic golden fixture
- versioned finite universe를 대상으로 하는 유한·결정론적 evolutionary search와 append-only trial/lineage ledger
- purged/embargoed walk-forward, 전체 ledger 기반 PBO, 접근 전 원자적 holdout slot 소비와 계보 폐쇄
- 검증을 모두 통과해 `PUBLISHED`된 전략만의 사용자 조회, CLI·REST 구조화 결과와 재현 가능한 HTML/JSON ResearchReport
- 단일 애플리케이션, 지속 단일 Worker, SQLite, 로컬 artifact 저장소

MVP는 생산급 시장 재현이나 optimizer의 baseline 우월성을 주장하지 않는다. Optimizer의 합격 기준은 유효 후보 생성과 finite termination이며, 생성 후보는 별도로 walk-forward·PBO·sealed holdout을 모두 통과해야 한다.

### MVP AI provider chain

- provider/model fallback 순서는 CapabilitySnapshot에 고정된 ordered chain으로만 정의한다. interactive wiki/idea 명령은 실제 응답성을 기준으로 `deepseek-ai/deepseek-v4-pro`를 기본 모델로 사용하며, `--model z-ai/glm-5.2 --model deepseek-ai/deepseek-v4-pro`처럼 명시적 fallback chain도 받을 수 있다.
- 인증은 `NVIDIA_API_KEY` 등 provider별 환경 변수로만 주입한다. workspace `.env`가 있으면 CLI/runtime이 자동으로 읽되, 이미 설정된 실제 환경 변수가 우선한다.
- 각 호출 attempt와 선택된 typed JSON artifact를 provenance에 저장한다. 이미 수락된 generation은 artifact를 재생해 LLM을 다시 호출하지 않는다.
- 외부 endpoint는 개발·테스트용이며 Production SLA로 간주하지 않는다. CI와 release test는 실제 key 대신 fake adapter를 사용한다.

정확한 adapter와 snapshot 계약은 [02_Architecture.md](./docs/02_Architecture.md)를 따른다.

### Article-to-wiki workflow

- CLI는 `article-add`, `alpha-generate` 두 command만 제공한다.
- 원본 markdown은 `article/<DOMAIN>/` 아래에 사용자가 직접 분류한다. 지원 폴더는 `FACTOR`, `STAT_ARB`, `MARKET_MAKING`, `STRUCTURAL_FLOW`, `CROSS_VENUE`, `DERIVATIVES`, `EVENT_FUNDAMENTAL`, `TIME_SERIES`다.
- `alpha-foundry article-add`는 domain 폴더별 원문을 읽어 wiki page, index, KnowledgePack JSON을 생성하고 SQLite에 import한다. 루트 직하 또는 알 수 없는 폴더의 markdown은 거절한다.
- `alpha-foundry alpha-generate "<request>" --domain FACTOR`는 최신 `FACTOR` article pack을 자동 선택하고 wiki-backed claim을 검색한 뒤 아이디어 page를 저장한다. 사용자는 pack/version/query command를 직접 다루지 않는다.
- 생성물 기본 구조는 `knowledge/wiki/pages/<pack-id>/<version>/`, `knowledge/wiki/indexes/<pack-id>/<version>/`, `knowledge/wiki/packs/<pack-id>/<version>/`, `knowledge/wiki/ideas/<pack-id>/<version>/`다.

### 명시적 연기

- cross-domain feature·strategy composition과 Meta Portfolio
- 실거래 주문 전송, 자동 운용 승격, 운영급 simulator
- 웹·모바일 UI, 다중 Worker, PostgreSQL, object storage, 멀티테넌시와 외부 공개 API

도메인 간 결합은 MVP에서 허용하지 않는다. 하나의 전략은 정확히 하나의 primary domain에 속하며, Lab 내부 구현을 공유하거나 조합하는 package·API·schema를 만들지 않는다.

릴리스별 세부 작업은 [03_DevelopmentPlan.md](./docs/03_DevelopmentPlan.md)가 권위 문서다.

## AI 구현 순서

AI 개발 에이전트는 다음 순서로 작업한다.

1. 작업 대상 FR과 Acceptance Criteria를 `01_Requirements.md`에서 확인한다.
2. 허용 의존성과 LLM 경계를 `02_Architecture.md`에서 확인한다.
3. 현재 Phase와 선행 작업을 `03_DevelopmentPlan.md`에서 확인한다.
4. 외부 계약은 `04_API.md`, 영속 계약은 `05_Database.md`만 따른다.
5. `06_CodingGuidelines.md`에 맞춰 구현하고 `07_TestPlan.md`의 테스트를 함께 추가한다.
6. 기존 ADR을 뒤집는 변경은 새 ADR 승인 전에는 구현하지 않는다.

## 변경 규칙

- 요구 결과가 바뀌면 Requirements를 먼저 변경한다.
- 컴포넌트 책임이나 의존 방향이 바뀌면 Architecture와 ADR을 변경한다.
- 외부 필드가 바뀌면 API versioning 규칙을 적용한다.
- 저장 구조가 바뀌면 Database migration을 같은 변경에 포함한다.
- 모든 구현 PR은 관련 FR, API 또는 Table, Test ID를 본문에 기록한다.
- Mermaid를 수정한 PR은 `.mmd`와 `.svg`를 함께 갱신한다.

SVG 재생성 명령(PowerShell, lock된 로컬 Mermaid CLI 사용):
```powershell
npm exec --no -- mmdc `
  -i docs/diagrams/alpha_foundry_overview.mmd `
  -o docs/diagrams/alpha_foundry_overview.svg `
  -b transparent
```
