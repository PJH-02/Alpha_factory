# Alpha Foundry 프로그램 해설서

이 문서는 Alpha Foundry를 처음 보는 사람이 목적, 실행 방법, 내부 구조, 데이터 흐름, 안전 경계와 현재 한계를 한 번에 이해하도록 작성한 입문 문서다.

## 1. 프로그램의 목적

Alpha Foundry는 논문과 연구 자료를 근거로 단일 금융 도메인의 알파 아이디어를 만들고, 이를 결정론적 연구·검증·출판 파이프라인으로 연결하기 위한 Python 기반 로컬 플랫폼이다.

핵심 원칙은 다음과 같다.

- LLM은 아이디어와 구조화된 후보를 제안하지만 권위 상태를 직접 변경하지 않는다.
- 논문 원문 대신 검토 가능한 wiki page와 immutable claim을 생성 과정에 사용한다.
- 한 전략은 정확히 하나의 primary domain에 속한다.
- 임의 Python 코드나 생성 코드를 실행하지 않는다.
- 동일한 입력과 버전은 동일한 canonical identity와 hash를 가진다.
- 검색, 검증, holdout, publication 결과는 SQLite와 content-addressed artifact store에 보존한다.
- 검증을 통과해 `PUBLISHED`된 결과만 공개 조회 대상으로 인정한다.

현재 사용자용 CLI는 논문 추가와 알파 생성 요청 두 기능만 노출한다. 나머지 저장소·검색·검증·출판 기능은 내부 서비스 또는 REST API 계약으로 유지된다.

## 2. 가장 빠른 사용 순서

### 2.1 NVIDIA API key 준비

프로젝트 루트 `.env`에 다음 값을 둔다.

```dotenv
NVIDIA_API_KEY=<build.nvidia.com에서 발급한 key>
```

CLI는 현재 작업 디렉터리의 `.env`를 자동으로 읽는다. 이미 운영체제 환경 변수에 같은 key가 있으면 환경 변수 값이 우선한다.

선택 설정은 다음과 같다.

```dotenv
ALPHA_FOUNDRY_HOME=.alpha-foundry
ALPHA_FOUNDRY_LLM_PROVIDER=nvidia
ALPHA_FOUNDRY_LLM_BASE_URL=https://integrate.api.nvidia.com/v1
ALPHA_FOUNDRY_LLM_TIMEOUT_SECONDS=30
```

### 2.2 논문 배치

모든 논문은 markdown으로 변환한 뒤 `article/<DOMAIN>/` 아래에 직접 분류한다.

```text
article/
├── FACTOR/
├── STAT_ARB/
├── MARKET_MAKING/
├── STRUCTURAL_FLOW/
├── CROSS_VENUE/
├── DERIVATIVES/
├── EVENT_FUNDAMENTAL/
└── TIME_SERIES/
```

예:

```text
article/FACTOR/value_and_quality.md
article/DERIVATIVES/option_surface_signals.md
```

도메인 오판을 막기 위해 LLM이 폴더를 분류하지 않는다. 프로그램은 사용자가 선택한 폴더 이름을 권위 있는 domain으로 사용한다.

다음 파일 배치는 거절된다.

```text
article/unclassified.md
article/UNKNOWN/paper.md
```

### 2.3 신규 논문 처리

```bash
alpha-foundry article-add
```

다른 article root를 쓰려면 경로를 전달한다.

```bash
alpha-foundry article-add D:/research/articles
```

이 command는 markdown이 들어 있는 domain 폴더마다 다음 작업을 수행한다.

1. 원문 markdown을 UTF-8로 읽는다.
2. NVIDIA OpenAI-compatible provider에 구조화된 요약을 요청한다.
3. 출처 locator와 excerpt가 포함된 claim을 만든다.
4. 관련 wiki page를 생성한다.
5. page 간 관련도를 계산하고 index를 갱신한다.
6. immutable KnowledgePack JSON을 생성한다.
7. claim과 pack을 SQLite에 import한다.
8. 같은 domain의 기존 pack이 있으면 다음 patch version을 만든다.

기본 AI 모델은 실제 응답이 검증된 `deepseek-ai/deepseek-v4-pro`다. GLM-5.2를 먼저 시도하고 실패 시 fallback하려면 다음과 같이 실행한다.

```bash
alpha-foundry article-add \
  --model z-ai/glm-5.2 \
  --model deepseek-ai/deepseek-v4-pro
```

`--model` 순서가 fallback 순서다.

### 2.4 알파 생성 요청

```bash
alpha-foundry alpha-generate "liquid quality factor with point-in-time fundamentals" --domain FACTOR
```

사용자는 KnowledgePack ID, version 또는 검색 query command를 직접 다루지 않는다. 프로그램이 다음을 자동 수행한다.

1. domain을 기반으로 표준 pack ID를 결정한다.
2. 해당 domain의 최신 article pack version을 찾는다.
3. 사용자 요청을 query로 사용해 wiki-backed claim을 검색한다.
4. 관련도가 높은 claim만 LLM context에 넣는다.
5. claim에 근거한 알파 아이디어를 생성한다.
6. 생성된 아이디어를 새 wiki markdown page로 저장한다.

예를 들어 `FACTOR` domain은 자동으로 `factor-articles` pack의 최신 version을 사용한다.

## 3. 사용자용 CLI

CLI 진입점은 `src/alpha_foundry/cli.py`이며, command는 정확히 두 개다.

### 3.1 `article-add`

```text
alpha-foundry article-add [ARTICLE_ROOT]
```

주요 옵션:

- `--wiki-root PATH`: 생성 wiki 경로 변경
- `--provider NAME`: 사용할 등록 provider 지정
- `--model MODEL`: 모델 지정, 여러 번 사용하면 fallback chain
- `--home PATH`: SQLite와 artifact runtime root 변경
- `--json`: compact JSON 출력

### 3.2 `alpha-generate`

```text
alpha-foundry alpha-generate "<request>" --domain DOMAIN
```

주요 옵션:

- `--domain`: 필수, 지원되는 8개 domain 중 하나
- `--wiki-root PATH`: 생성 wiki 경로 변경
- `--provider NAME`: 사용할 provider 지정
- `--model MODEL`: 모델 또는 fallback chain 지정
- `--limit N`: wiki claim 검색 상한, 기본 8
- `--idea-count N`: 생성할 아이디어 수, 기본 3
- `--home PATH`: runtime root 변경
- `--json`: compact JSON 출력

이전의 `health`, `knowledge`, `search`, `generation`, `registry`, `report`, `internal`, `job`, `mandate` CLI command는 사용자 surface에서 제거됐다. 관련 내부 서비스와 REST API는 유지된다.

## 4. 생성 파일과 runtime 데이터

### 4.1 Wiki 파일

기본 wiki root는 `knowledge/wiki`다.

```text
knowledge/wiki/
├── pages/<pack-id>/<version>/*.md
├── indexes/<pack-id>/<version>/index.json
├── indexes/<pack-id>/latest.json
├── packs/<pack-id>/<version>/knowledge-pack.json
└── ideas/<pack-id>/<version>/*.md
```

- `pages`: 논문을 정리한 wiki page
- `indexes`: page, source, related page, claim ID 색인
- `packs`: DB import와 재검토가 가능한 KnowledgePack JSON
- `ideas`: wiki claim에 근거해 생성된 알파 아이디어 page

원문은 `article/`에 남고 generation context에는 원문 전체가 아니라 wiki-derived claim이 들어간다.

### 4.2 SQLite

기본 DB 경로는 다음과 같다.

```text
.alpha-foundry/alpha_foundry.sqlite
```

중요 테이블:

- `knowledge_claims`: immutable claim revision
- `knowledge_packs`: immutable pack revision
- `knowledge_pack_claims`: pack과 claim의 순서 관계
- `immutable_resources`: mandate 등 versioned resource
- `generation_requests`: generation identity와 상태
- `generation_attempts`: provider/model attempt provenance
- `jobs`: durable job lifecycle
- `search_runs`: finite deterministic search state
- `lineages`: experiment/search lineage
- `publications`: publication state machine
- `artifacts`, `artifact_links`: CAS artifact metadata와 관계

스키마 정의는 `src/alpha_foundry/infrastructure/db.py`에 있다.

### 4.3 Artifact store

기본 artifact root는 다음과 같다.

```text
.alpha-foundry/artifacts/
```

`LocalArtifactStore`는 SHA-256 content address를 사용한다. 같은 bytes는 같은 digest를 가지며 payload와 manifest 일치를 읽을 때 다시 검증한다.

## 5. 전체 아키텍처

### 5.1 Domain core

주요 파일:

- `src/alpha_foundry/domain/models.py`
- `src/alpha_foundry/domain/canonical.py`
- `src/alpha_foundry/domain/errors.py`

책임:

- strict/frozen Pydantic model
- domain enum과 typed strategy AST
- capability, dataset, policy, validation, report, job 계약
- canonical bytes와 SHA-256 identity
- 공통 error code

`FrozenModel`은 unknown field와 암묵적 coercion을 거절하며 생성 후 변경할 수 없다.

### 5.2 Knowledge plane

주요 파일:

- `src/alpha_foundry/knowledge.py`
- `src/alpha_foundry/wiki.py`

`knowledge.py`는 다음 immutable 계약을 정의한다.

- `Citation`
- `Counterevidence`
- `FailureMemory`
- `Claim`
- `ClaimPin`
- `KnowledgePack`
- `ClaimContext`

`wiki.py`는 다음 작업을 수행한다.

- domain article markdown 발견
- LLM structured summary 생성
- page와 claim 조립
- related page 계산
- index/pack/page 쓰기
- query에 맞는 claim ranking
- wiki-grounded idea page 생성

Claim은 `id`, `version`, `domain`, `statement`, `source`, citation을 가진다. 수정은 기존 row를 바꾸지 않고 새 version을 만든다.

### 5.3 LLM boundary

주요 파일:

- `src/alpha_foundry/infrastructure/llm.py`
- `src/alpha_foundry/generation.py`
- `src/alpha_foundry/application/generation.py`

`OpenAICompatibleProvider`는 다음 endpoint를 호출한다.

```text
POST https://integrate.api.nvidia.com/v1/chat/completions
```

요청 특징:

- Bearer API key
- 명시적 model
- system/user message
- JSON Schema response format
- sampling option
- DeepSeek용 `chat_template_kwargs`

응답은 먼저 text/bytes 일치를 확인하고, code-owned Pydantic schema로 다시 검증한다. LLM이 생성한 임의 코드나 state transition은 실행하지 않는다.

provider/model fallback은 순서대로 수행된다. wiki 작업의 기본 후보는 다음 순서다.

1. `deepseek-ai/deepseek-v4-pro`
2. `z-ai/glm-5.2`

현재 NVIDIA trial endpoint에서 DeepSeek V4 Pro의 실제 completion은 검증됐고 GLM-5.2는 환경에 따라 timeout이 발생할 수 있다.

### 5.4 Application facade

주요 파일:

- `src/alpha_foundry/bootstrap.py`

`ApplicationFacade`는 CLI와 REST가 공유하는 command/query boundary다.

사용자 workflow와 직접 관련된 method:

- `add_articles`
- `generate_alpha`
- `import_markdown_knowledge`
- `query_knowledge_pack`
- `ideate_knowledge_pack`
- `add_knowledge_pack`
- `get_knowledge_pack`

`bootstrap()`은 SQLite, CAS, provider, domain lab registry와 clock을 조립한다. module import 시 환경 변수나 외부 endpoint를 읽지 않고 executable entrypoint가 명시적으로 bootstrap할 때만 runtime을 만든다.

### 5.5 Domain labs와 compiler

주요 디렉터리:

- `src/alpha_foundry/labs/`
- `src/alpha_foundry/engines/`

지원 domain:

1. Factor
2. Statistical Arbitrage
3. Market Making
4. Structural Flow
5. Cross Venue
6. Derivatives
7. Event Fundamental
8. Time Series

각 domain은 자체 payload, execution policy, question schema, strategy compiler와 numerical engine을 가진다. Lab끼리 직접 import하거나 cross-domain strategy를 합성하지 않는다.

`src/alpha_foundry/compiler.py`는 registry를 통해 정확히 한 domain compiler로 dispatch한다. compiler는 allowlisted typed AST를 data-only execution plan으로 변환하고 임의 코드를 실행하지 않는다.

### 5.6 Deterministic search

주요 디렉터리:

- `src/alpha_foundry/search/`

검색은 무한 optimizer가 아니라 versioned finite universe를 순회한다.

주요 개념:

- immutable candidate
- finite universe
- frozen parent pool
- deterministic traversal order
- append-only proposal/trial event
- plateau 또는 universe exhaustion 종료

같은 seed, universe, profile, operator set은 같은 순서를 만든다.

### 5.7 Validation과 holdout

주요 디렉터리:

- `src/alpha_foundry/validation/`

기능:

- purged/embargoed walk-forward
- PBO certificate
- sealed holdout access
- report disclosure policy
- validation registry

Holdout 결과는 candidate feedback으로 재주입하지 않는다. validation을 통과하지 못한 결과는 publication으로 넘어가지 않는다.

### 5.8 Publication

주요 파일:

- `src/alpha_foundry/publication.py`
- `src/alpha_foundry/application/publication.py`
- `src/alpha_foundry/reporting.py`

Publication은 state machine과 compare-and-swap ownership으로 관리한다.

개략 상태:

```text
AVAILABLE -> PREPARING -> PUBLISHED
                     \-> FAILED_RETRYABLE
```

Report JSON과 HTML을 먼저 CAS에 stage한 뒤 짧은 DB transaction에서 함께 link한다. 공개 registry에는 `PUBLISHED` 결과만 나타난다.

### 5.9 Durable jobs와 worker

주요 파일:

- `src/alpha_foundry/application/jobs.py`
- `src/alpha_foundry/worker.py`

특징:

- SQLite-backed job lifecycle
- 한 번에 하나의 local worker
- explicit cancellation boundary
- process restart 시 persisted RUNNING job을 interrupted failure로 종료
- generated code dispatch 금지

현재 사용자 CLI는 job 관리 command를 노출하지 않는다. 운영 기능은 내부 서비스와 REST에 남아 있다.

### 5.10 REST API

주요 파일:

- `src/alpha_foundry/interfaces/api.py`

REST는 내부 운영·검토·조회 surface를 유지한다. CLI가 단순해졌다고 해서 job, search, publication, report API가 삭제되는 것은 아니다.

API 응답은 공통 envelope를 사용하고 correlation ID와 typed error를 제공한다.

## 6. Article 처리 상세 흐름

`alpha-foundry article-add`의 상세 흐름은 다음과 같다.

```text
article/<DOMAIN>/*.md
    -> add_articles
    -> domain folder 검증
    -> import_markdown_knowledge
    -> discover_markdown_materials
    -> NVIDIA structured completion
    -> DraftPage / DraftClaim validation
    -> WikiPage + Claim + KnowledgePack 생성
    -> wiki files/index 저장
    -> knowledge_claims / knowledge_packs DB import
```

도메인 판단은 폴더가 담당한다. LLM은 claim 내용과 wiki 문서를 작성하지만 claim의 `domain` 값은 폴더에서 결정된 enum을 코드가 주입한다.

## 7. Alpha 생성 상세 흐름

`alpha-foundry alpha-generate`의 상세 흐름은 다음과 같다.

```text
user request + explicit DOMAIN
    -> generate_alpha
    -> <domain>-articles pack ID 결정
    -> latest numeric semantic version 선택
    -> order_claim_context(query=request, domain=DOMAIN)
    -> 관련 wiki-backed claim만 선택
    -> NVIDIA structured completion
    -> IdeaBundle validation
    -> knowledge/wiki/ideas/.../*.md 저장
    -> wiki index 갱신
```

이 command가 만드는 결과는 근거가 연결된 아이디어 page다. 자동 backtest, sealed holdout, publication까지 한 번에 실행하는 command는 아니다. 그 후속 단계는 내부 search/validation/publication 서비스가 담당한다.

## 8. 오류 처리

공통 error code는 `src/alpha_foundry/domain/errors.py`에 정의된다.

주요 code:

- `AF-SCHEMA-001`: 입력 구조 또는 article 폴더 오류
- `AF-RESOURCE-001`: 필요한 article pack/wiki 없음
- `AF-CAPABILITY-001`: provider 또는 실행 capability 없음
- `AF-LLM-001`: 모든 model attempt 실패
- `AF-STORAGE-001`: SQLite/CAS/wiki file 오류
- `AF-VALIDATION-001`: validation 실패
- `AF-JOB-INTERRUPTED`: restart로 RUNNING job 종료

CLI exit code:

- `0`: 성공
- `2`: schema/usage
- `3`: validation rejection
- `4`: provider/capability/storage temporary failure
- `5`: state/resource/internal failure

API key, prompt 원문, sealed holdout detail은 public error나 repr에 포함하지 않는다.

## 9. 테스트 구조

```text
tests/
├── unit/
├── contract/
├── numeric/
├── integration/
├── e2e/
├── recovery/
├── security/
├── performance/
└── architecture/
```

- unit: canonicalization과 작은 helper
- contract: public model/schema invariants
- numeric: domain engine 계산
- integration: SQLite/CAS/service 관계
- e2e: CLI와 REST observable behavior
- recovery: crash/restart/fault handling
- security: holdout leakage와 generated code 거절
- performance: 큰 factor input과 API latency
- architecture: domain boundary와 composition 금지

일반 검증 명령:

```bash
uv run ruff check src tests
uv run mypy
uv run pytest
```

AI endpoint 실호출은 CI 기본 test에 넣지 않는다. CI는 `FakeProvider`를 사용하고 실제 key smoke test는 별도로 수행한다.

## 10. 개발자가 자주 찾는 위치

| 목적 | 파일/디렉터리 |
| --- | --- |
| 사용자 CLI | `src/alpha_foundry/cli.py` |
| runtime assembly/facade | `src/alpha_foundry/bootstrap.py` |
| article/wiki 처리 | `src/alpha_foundry/wiki.py` |
| knowledge contracts | `src/alpha_foundry/knowledge.py` |
| LLM adapter | `src/alpha_foundry/infrastructure/llm.py` |
| generation contracts | `src/alpha_foundry/generation.py` |
| SQLite schema | `src/alpha_foundry/infrastructure/db.py` |
| artifact store | `src/alpha_foundry/infrastructure/artifacts.py` |
| domain models | `src/alpha_foundry/domain/models.py` |
| compilers | `src/alpha_foundry/compiler.py`, `src/alpha_foundry/labs/` |
| numerical engines | `src/alpha_foundry/engines/` |
| deterministic search | `src/alpha_foundry/search/` |
| validation | `src/alpha_foundry/validation/` |
| publication | `src/alpha_foundry/application/publication.py` |
| REST API | `src/alpha_foundry/interfaces/api.py` |
| 전체 계약 문서 | `docs/` |

## 11. 현재 한계

- 사용자 CLI의 alpha 생성은 wiki-grounded idea page 생성까지 수행한다.
- 자동 backtest부터 validation, sealed holdout, publication까지 연결하는 단일 user command는 아직 없다.
- article import는 domain 폴더의 모든 markdown을 새 pack version으로 다시 요약한다. 변경 파일만 증분 처리하는 cache는 아직 없다.
- multi-persona 또는 인간 reviewer 합의 workflow는 아직 없다.
- NVIDIA cloud endpoint는 trial service이므로 latency와 quota가 일정하지 않다.
- GLM-5.2는 실제 환경에서 timeout이 발생할 수 있어 DeepSeek V4 Pro가 기본이다.
- production trading, 주문 전송, portfolio composition은 MVP 범위 밖이다.

## 12. 안전하게 확장하는 방법

새 기능을 추가할 때 다음 순서를 따른다.

1. 새 전략이 기존 8개 domain 중 어디에 속하는지 먼저 결정한다.
2. 기존 domain lab의 typed payload와 compiler pattern을 재사용한다.
3. 임의 code field나 callable을 schema에 넣지 않는다.
4. 새 resource는 immutable version과 canonical hash를 가진다.
5. LLM output은 code-owned schema로 검증한다.
6. claim에는 source, locator, excerpt를 남긴다.
7. holdout 결과를 generation/search feedback으로 되돌리지 않는다.
8. observable behavior와 failure branch를 test로 고정한다.
9. CLI user intent가 아닌 운영 기능은 REST/internal service에 둔다.

이 문서와 `README.md`, `docs/01_Requirements.md`, `docs/02_Architecture.md`, `docs/04_API.md`, `docs/05_Database.md`를 함께 보면 구현 의도와 계약 경계를 추적할 수 있다.
