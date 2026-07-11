# Alpha Foundry Coding Guidelines

상태: Approved  
대상: 사람과 모든 AI 개발 에이전트

## 1. 우선순위

규칙 충돌 시 다음 순서를 적용한다.

1. 승인된 ADR
2. API·Database의 명시적 계약
3. Architecture 의존성 규칙
4. 이 문서
5. 사용 library의 기본 관례

계약 변경이 필요하면 코드를 우회하지 말고 권위 문서를 같은 PR에서 변경한다.

## 2. Toolchain

| 목적 | 도구 | 필수 명령 |
| --- | --- | --- |
| Runtime | Python 3.12 | `python --version` |
| Package | `uv` | `uv sync --frozen` |
| Format/Lint | Ruff | `uv run ruff format --check .`, `uv run ruff check .` |
| Type | mypy strict | `uv run mypy src` |
| Test | pytest | `uv run pytest` |
| Schema | Pydantic v2 | OpenAPI/JSON Schema generation |
| Migration | SQL files + runner | 빈 DB·upgrade test |

CI와 로컬 명령은 동일해야 한다. warning을 무시해 합격시키지 않는다.

## 3. Naming Convention

| 대상 | 규칙 | 예 |
| --- | --- | --- |
| module/function/variable | `snake_case` | `compile_experiment` |
| class/protocol | `PascalCase` | `ExperimentEngine` |
| constant/enum member | `UPPER_SNAKE_CASE` | `QUESTION_SPECIFIED` |
| private symbol | 앞에 `_` | `_normalize_policy` |
| ID type | entity 이름 + `Id` | `MandateId` |
| domain plugin key | API Domain enum | `STAT_ARB` |
| engine key | lowercase kebab | `panel-portfolio` |
| event/error/test ID | 문서 규칙 | `experiment.completed`, `AF-STATE-001`, `UT-DOM-001` |

`data`, `manager`, `helper`, `util`, `process`처럼 책임이 불명확한 public 이름을 사용하지 않는다.

## 4. Architecture Rules

- `domain`은 표준 library와 순수 value object만 의존한다.
- application use case는 repository나 provider 구현이 아니라 port를 받는다.
- adapter 조립은 `bootstrap.py`에서만 수행한다.
- API route는 request 변환 후 application service 한 개를 호출한다.
- repository는 business rule을 만들지 않는다.
- engine은 I/O·LLM·DB를 호출하지 않는다.
- Lab은 다른 Lab 내부를 import하지 않는다.
- cross-domain 데이터는 [ADR-0008](./ADR/ADR-0008-cross-domain-composition.md)의 interface를 통한다.

순환 import와 내부 module 직접 import는 architecture test로 차단한다.

## 5. Schema와 타입

- public function의 parameter와 return에는 타입을 명시한다.
- `Any`, untyped `dict`, `# type: ignore`는 사유와 좁은 범위가 없으면 금지한다.
- 외부 JSON은 Pydantic model에서 `extra="forbid"`로 검증한다.
- domain별 payload는 discriminator가 있는 union으로 선언한다.
- money, rate, fee는 `Decimal`을 사용하고 float로 변환하지 않는다.
- timestamp는 timezone-aware UTC만 허용한다.
- 기간은 `start <= t < end`다.
- 단위는 변수명 또는 value type에 포함한다: `latency_ms`, `AnnualBps`.

## 6. 함수와 객체

- 함수는 하나의 명확한 결과를 만들고 I/O와 계산을 분리한다.
- boolean 인자가 두 개 이상이면 command/value object로 바꾼다.
- immutable model은 frozen 설정을 사용한다.
- state 변경은 entity method 또는 application command 한 곳에서만 수행한다.
- 새 revision은 원본을 mutate하지 않고 `parent_id`와 새 content hash를 만든다.
- public interface의 기본값은 의미가 명확하고 version 간 안정적일 때만 사용한다.

## 7. Error Handling

### 7.1 계층

```text
AlphaFoundryError
├── SchemaError
├── DomainError
├── CapabilityError
├── StateTransitionError
├── ValidationRejected
├── StorageError
├── ProviderError
└── EngineError
```

- domain/application은 HTTP status를 알지 못한다.
- adapter는 내부 예외를 [04_API.md](./04_API.md#9-error-code)의 error code로 매핑한다.
- `except Exception`은 job/application 경계에서 실패를 기록한 뒤 다시 raise하거나 표준 오류로 변환할 때만 허용한다.
- 입력 오류를 자동 보정하지 않는다. 거절 field와 reason을 반환한다.
- 재시도는 `retryable=true`인 provider/storage 오류에만 적용한다.
- assertion은 사용자 입력 검증에 사용하지 않는다.

## 8. Logging

구조화 JSON log를 사용한다. 필수 field는 Architecture 14절을 따른다.

```python
logger.info(
    "experiment_completed",
    extra={
        "correlation_id": correlation_id,
        "job_id": str(job_id),
        "experiment_id": str(experiment_id),
        "stage": "engine",
        "duration_ms": duration_ms,
    },
)
```

금지되는 log:

- secret, access token, credential
- 원문 prompt·response 전문
- dataset row와 사용자 제공 원문
- sealed holdout selector와 metric 상세
- 전체 stack trace의 client 반환

LLM 호출은 provider, model, prompt hash, schema version, token 수, duration, status만 기록한다.

## 9. Async와 Job 정책

- HTTP route는 engine이나 LLM의 완료를 기다리지 않고 job을 반환한다.
- MVP Worker는 process당 하나이며 SQLite writer도 하나다.
- blocking engine은 event loop에서 직접 실행하지 않는다.
- job은 stage 경계에서 cancellation flag를 확인한다.
- 완료 artifact와 DB 상태는 멱등하게 commit한다.
- process 시작 시 `RUNNING` job은 `QUEUED`로 복구하되 sealed access는 되돌리지 않는다.
- library 내부에 숨은 background task를 만들지 않는다.

Beta 다중 Worker 규칙은 ADR-0007의 재검토 조건이 충족된 뒤 적용한다.

## 10. Dependency Injection

constructor injection을 기본으로 한다.

```python
class RunMandate:
    def __init__(
        self,
        mandates: MandateRepository,
        generator: ResearchGeneratorPort,
        labs: LabRegistry,
        engines: EngineRegistry,
        clock: Clock,
    ) -> None: ...
```

- service locator와 module global mutable dependency를 금지한다.
- 현재 시간, UUID, random seed, filesystem path도 port 또는 command로 전달한다.
- test는 fake port를 명시적으로 주입한다.

## 11. 결정론과 수치 규칙

- 입력 collection을 순회하기 전 정렬 기준을 명시한다.
- 모든 random 호출은 전달받은 seed에서 파생한다.
- canonical JSON은 UTF-8, key sort, 명시적 null, compact separator를 사용한다.
- NaN과 Infinity는 영속·API model에서 금지한다.
- 수익률 합성, 비용, position, cash의 회계 invariant를 각 engine step에서 검증한다.
- 병렬 연산 순서가 결과를 바꾸면 결과를 안정 정렬하거나 결정론적 reduction을 사용한다.
- numeric tolerance는 test에 이름 있는 constant로 정의한다.

## 12. LLM 규칙

LLM이 수행할 수 있다.

- 구조화된 질문·가설·전략 후보 생성
- 근거 요약과 실패기억 비교
- 허용된 reason code에 대한 설명 작성

LLM이 수행할 수 없다.

- SQL, Python, shell 실행
- 상태 전이, 예산 차감, Registry 등록
- 데이터·engine 존재 여부 최종 판정
- validation 결과 변경
- sealed holdout 조회
- 누락 config 추정

모든 출력은 provider SDK object를 제거한 plain JSON으로 변환하고 schema 검증 후 사용한다.

## 13. Database와 Artifact 코드

- transaction은 application use case 단위로 짧게 유지한다.
- SQL 문자열을 domain/application module에 작성하지 않는다.
- repository 조회는 정렬 순서를 명시한다.
- optimistic update는 `WHERE id=? AND row_version=?`를 사용한다.
- migration 없이 runtime에서 schema를 생성하거나 변경하지 않는다.
- artifact는 임시 쓰기 → hash 검증 → atomic rename → DB commit 순서를 지킨다.
- path에 사용자 입력을 직접 연결하지 않는다.

## 14. Testing 규칙

- bug fix는 실패 재현 test를 먼저 추가한다.
- 순수 계산은 unit test, port 구현은 integration test, 공개 schema는 contract test로 검증한다.
- snapshot은 큰 JSON 전체보다 안정된 schema·핵심 field에 사용한다.
- test는 실제 외부 LLM과 인터넷에 의존하지 않는다.
- 시간, UUID, seed는 fixture에서 고정한다.
- 수치 test는 독립 oracle 또는 손계산 가능한 작은 dataset을 사용한다.
- Test ID와 범위는 [07_TestPlan.md](./07_TestPlan.md)를 따른다.

## 15. 코드 리뷰 규칙

PR 본문 필수 항목:

```text
Requirements: FR-xxx, AC-xxx
Contracts: API section / table / ADR
Tests: UT-xxx, CT-xxx, IT-xxx, E2E-xxx
Data or migration impact: none | description
LLM boundary impact: none | description
```

Reviewer는 다음을 확인한다.

- 권위 문서와 구현 일치
- 새 optional field로 도메인 차이를 숨기지 않음
- LLM 출력이 validation을 우회하지 않음
- 시간 누수와 비용·회계 invariant
- retry와 예외가 중복 side effect를 만들지 않음
- log와 artifact에 민감정보가 없음
- 변경된 공개 계약의 consumer test 존재

작성자와 Reviewer가 같은 AI일 수 없다. 사람이 최종 merge 책임을 가진다.

## 16. Git과 문서

- commit은 실행 가능한 한 가지 변경을 표현한다.
- generated file은 원본과 같은 commit에 포함한다.
- `.mmd` 수정 시 대응 `.svg`를 재생성한다.
- migration, API schema, test fixture의 version을 임의로 덮어쓰지 않는다.
- 제거된 기능은 코드·테스트·문서·example에서 함께 제거한다.

## 17. 금지 패턴

- 한 class가 orchestration, DB, LLM, engine 계산을 모두 담당
- domain payload를 `dict[str, Any]`로 전달
- 모든 도메인을 optional field가 많은 하나의 model로 통합
- 사용자 config를 “현실적” 값으로 자동 변경
- 성공 metric만 저장하고 실패 후보를 삭제
- sealed 결과를 prompt나 다음 후보 생성에 포함
- broad exception을 무시하고 성공 상태 반환
- test에서 network, wall clock, random default 사용
