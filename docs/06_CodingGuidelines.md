# Alpha Foundry Coding Guidelines

| 항목 | 값 |
|---|---|
| 문서 버전 | 1.0.0 |
| 언어 기준 | Python 3.12 |
| 형식·lint | Ruff |
| 타입 | mypy strict |
| 테스트 | pytest + Hypothesis |

## 1. 적용 범위와 우선순위

이 규칙은 application source, engine, migration, test, script, generated schema wrapper에 적용한다. 충돌 시 우선순위는 다음과 같다.

1. 보안·holdout·회계·데이터 인과성 불변식
2. Accepted ADR과 Architecture dependency rule
3. API·DB executable contract
4. 이 Coding Guideline
5. module-local convention

우선순위로 문서 불일치를 방치하지 않는다. 충돌을 발견한 변경은 권위 문서까지 함께 수정하거나 중단한다.

## 2. Toolchain과 필수 검사

| 도구 | 역할 | 필수 설정 |
|---|---|---|
| `uv` | dependency·lock·virtual environment | `uv.lock` commit, frozen CI install |
| Ruff formatter | 형식 | line length 100, Python 3.12 target |
| Ruff lint | import/style/bug/security subset | `E,F,I,UP,B,SIM,ASYNC,PERF,RUF` enable |
| mypy | static type | `strict=true`, untyped defs/calls 금지 |
| pytest | test runner | strict markers, strict config |
| Hypothesis | property test | seed artifact와 failing example 저장 |
| import-linter | architecture dependency | Architecture 5장의 contract |
| Alembic | DB migration | single head, naming convention |
| pip-audit | dependency CVE | high/critical 0건 |
| detect-secrets | secret scan | baseline은 false-positive rationale 포함 |

표준 local gate:

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy alpha_foundry apps
uv run lint-imports
uv run pytest -m "not performance and not recovery"
```

CI는 migration, OpenAPI/schema drift, performance, recovery, secret, dependency 검사를 추가한다.

## 3. 파일과 Naming Convention

### 3.1 Python symbol

| 대상 | 규칙 | 예시 |
|---|---|---|
| package/module | `snake_case`, 명사 또는 책임 | `question_audit.py` |
| class | `PascalCase` | `ExperimentLedger` |
| protocol/port | 역할 + `Port` | `ArtifactWriterPort` |
| immutable value | 명사 | `ExperimentFingerprint` |
| application command | 동사 + 대상 + `Command` | `RunExperimentCommand` |
| query | `Get/List/Search` + 대상 + `Query` | `GetProgramQuery` |
| handler | command/query + `Handler` | `RunExperimentHandler` |
| exception | 원인 + `Error` | `IllegalTransitionError` |
| constant | `UPPER_SNAKE_CASE` | `MAXIMUM_JOB_ATTEMPTS` |
| private | 단일 `_` 접두사 | `_canonicalize_decimal` |
| test | `test_<조건>_<기대결과>` | `test_future_available_at_rejects_dataset` |

`util.py`, `helpers.py`, `common.py`, `misc.py` 같은 책임 불명 이름은 금지한다. 약어는 `id`, `api`, `pnl`, `var`, `lob`, `ic`처럼 project glossary에 있는 것만 사용한다.

### 3.2 Identifier

- `str`로 모든 ID를 전달하지 않는다. core에서 `NewType` 또는 frozen value object로 `MandateId`, `QuestionId`, `StrategyId`, `ExperimentId`, `LineageId`를 구분한다.
- 외부 boundary에서 UUID parse 후 typed ID로 변환한다.
- DB·API DTO 밖에서 raw dictionary를 ID/resource 대신 사용하지 않는다.

### 3.3 Module public surface

각 package의 public symbol은 `__init__.py`에 명시적으로 export한다. wildcard import와 implicit re-export는 금지한다. module당 public class/function은 한 책임 집합에 한정하고 500 source lines를 넘으면 책임을 분리한다. Generated schema file은 예외이며 수정하지 않는다.

## 4. Architecture Rule

### 4.1 Dependency

- `core`는 standard library, `typing`, domain-safe decimal/date utility만 import한다.
- Pydantic DTO는 `core` invariant object와 분리하되 변환 함수가 한 곳에 존재한다.
- `labs/<domain>`에서 다른 `labs/<domain>` import는 금지한다.
- `engines`에서 FastAPI, SQLAlchemy model, LLM SDK, lab 구현을 import하지 않는다.
- `validation`은 engine internal class가 아니라 public result/artifact port만 사용한다.
- `infrastructure`가 `ports`를 구현하고 `core/application`은 infrastructure를 import하지 않는다.
- bootstrap 이외의 code에서 global registry, service locator, singleton client를 조회하지 않는다.

### 4.2 Domain ownership

Built-in domain package는 최소 다음 파일을 소유한다.

```text
payloads.py       # question/hypothesis/strategy strict models
lab.py            # ResearchLab implementation
compiler.py       # question→hypothesis→strategy
audit_rules.py    # domain hard gates
memory.py         # domain memory schema
registration.py   # versioned registry descriptor
```

Engine package는 `input_adapter.py`, `state.py`, `accounting.py`, `engine.py`, `attribution.py`를 분리한다. MM/CrossVenue처럼 event replay가 있는 package는 `clock.py`와 `events.py`를 추가한다.

### 4.3 Cross-domain composition

다른 전략 결과는 `CompositionSpec`과 6개 interface port를 통해서만 받는다. `FactorScore * SpreadZScore` 같은 산술 결합을 generic expression node로 추가하면 안 된다. Source strategy의 full internal payload나 memory를 target lab에 전달하지 않고 interface별 최소 DTO만 전달한다.

## 5. Type와 Schema 규칙

1. 모든 public function과 method는 argument와 return type을 명시한다.
2. `Any`, unparameterized `dict/list`, `# type: ignore`는 금지한다. 불가피한 vendor boundary의 `Any`는 adapter 한 곳에서 Protocol/TypedDict로 좁히고 주석에 vendor issue를 기록한다.
3. Pydantic model은 `ConfigDict(extra="forbid", strict=True, frozen=True)`를 기본으로 한다. Mutable command state만 별도 mutable aggregate가 소유한다.
4. Union은 `Annotated[Union[...], Field(discriminator="...")]`를 사용한다.
5. `Optional[T]`는 실제로 null이 의미 있는 경우만 사용한다. 미제공과 비적용을 null 하나로 합치지 않는다.
6. enum value는 영속·API 문자열과 동일한 대문자 snake case다.
7. default는 domain 의미가 자명하고 문서에 있는 값만 둔다. 비용, 지연, 체결, risk, timezone, holdout에 silent default를 두지 않는다.
8. Schema version과 content hash는 model 생성 후 canonical serializer로 계산하며 caller 값을 신뢰하지 않는다.
9. domain payload를 `dict[str, object]`로 통과시키지 않는다. registry의 concrete class로 deserialize한다.
10. JSON Schema snapshot 변경은 `BREAKING|BACKWARD_COMPATIBLE|DOCUMENTATION_ONLY` 분류를 PR에 기록한다.

## 6. 함수와 객체 설계

- 함수는 한 추상화 수준을 유지하고 boolean flag로 여러 동작을 숨기지 않는다. 별도 command 또는 policy object를 사용한다.
- pure calculation과 I/O를 분리한다. 통계·회계 함수는 같은 입력에 같은 출력을 반환하고 clock, DB, log를 읽지 않는다.
- domain entity는 invariant를 constructor에서 검증하고 invalid 중간 상태를 만들지 않는다.
- list를 반환할 때 order를 계약으로 정의한다. 무순서 결과는 stable key로 정렬한다.
- generator/iterator는 dataset streaming처럼 수명과 resource close가 명확한 곳에서만 사용한다.
- magic numeric constant를 금지한다. 값은 versioned config, exchange contract, named mathematical constant 중 하나여야 한다.
- 한 함수의 cyclomatic complexity는 10 이하, nesting은 3단계 이하를 기준으로 한다. 상태 머신은 table-driven transition으로 구현한다.

## 7. Error Handling

### 7.1 계층

```text
AlphaFoundryError
├── ContractError
├── AuthorizationError
├── DomainInvariantError
├── CapabilityError
├── BudgetError
├── DataIntegrityError
├── ValidationRejected
├── ConcurrencyError
├── RetryableDependencyError
└── ResourceLimitError
```

각 application error는 stable `error_code`, safe message, typed details, `retryable`을 가진다. HTTP status mapping은 adapter가 [API Error Code](04_API.md#11-error-code)에 따라 수행한다.

### 7.2 규칙

- `except Exception`은 process/job boundary에서 log·rollback·safe failure로 변환할 때만 허용하고 반드시 re-raise 또는 terminal failure를 기록한다.
- domain rejection과 software exception을 섞지 않는다. 검증 실패는 `ValidationRejected`와 정상 `REJECTED` 상태다.
- exception을 빈 값, 0, null metric으로 바꾸지 않는다.
- retry는 error class가 `retryable=True`일 때 orchestration layer만 수행한다. engine 내부 retry 금지.
- database unique/concurrency error는 repository가 stable domain error로 변환한다.
- assertion은 programmer invariant에만 사용하고 사용자 입력 검증에 사용하지 않는다.
- cleanup exception으로 원래 exception을 덮어쓰지 않는다.

## 8. Logging과 Observability

### 8.1 구조화 log

모든 log는 JSON object이며 문자열 interpolation 대신 named field를 쓴다.

```python
logger.info(
    "experiment_started",
    extra={
        "experiment_id": str(experiment_id),
        "fingerprint_prefix": fingerprint.prefix,
        "domain": domain.value,
        "engine_id": engine.engine_id,
    },
)
```

필수 context는 Architecture 17장의 공통 필드다. event 이름은 past tense 또는 상태 사실로 고정한다: `job_claimed`, `gate_failed`, `artifact_verified`.

### 8.2 금지 log

- access/refresh token, API key, password, connection string
- raw prompt/LLM response, source 원문, 사용자 자연어 전체
- full SQL parameter, market data row, order/position series
- exception object 안의 provider credential/URL query
- 사람 display name, raw IP, raw user agent

actor는 내부 ID의 keyed hash, fingerprint는 첫 12 hex만 log한다. 상세 artifact는 접근 통제 저장소에 둔다.

### 8.3 Metric과 trace

- duration metric은 monotonic clock을 사용한다.
- high-cardinality ID를 metric label로 사용하지 않는다.
- API→job enqueue의 trace context를 job payload metadata에 전달하고 worker가 child trace를 시작한다.
- engine loop의 row/event마다 span을 만들지 않고 batch·phase 단위 span을 사용한다.

## 9. Async와 동시성 정책

### 9.1 API

- FastAPI route는 async이고 network/DB adapter만 await한다.
- NumPy, pandas/Polars collect, compression, hashing, statistics를 event loop에서 실행하지 않는다.
- 100ms를 넘을 수 있는 계산은 job으로 enqueue한다.
- request 안 병렬 I/O는 `asyncio.TaskGroup`과 명시 semaphore를 사용한다. unbounded `gather` 금지.
- DB transaction 안에서 LLM, blob, market source network call을 하지 않는다.
- timeout은 dependency adapter에서 설정하고 cancel을 삼키지 않는다.

### 9.2 Worker

- worker coordinator가 job을 claim하고 CPU engine은 `spawn` 방식 격리 subprocess에서 실행한다.
- subprocess는 job별 memory, CPU, wall-clock, output byte 한도를 받는다.
- global process pool에 mutable engine state를 재사용하지 않는다.
- handler는 15초 heartbeat와 phase boundary cancellation checkpoint를 호출한다.
- sealed job은 전용 queue/role/process에서만 실행한다.
- 동일 resource를 잠글 때 순서는 `research_lineage → strategy → experiment → job`이다. 역순 lock 금지.

### 9.3 Retry와 backpressure

- API는 mutation transaction을 자동 재전송하지 않는다.
- worker transient retry는 5초, 30초, 120초 backoff와 deterministic jitter를 사용한다.
- queue depth와 oldest age가 threshold를 넘으면 lower-priority open discovery job 수락을 429로 제한한다.
- provider rate limit은 tenant와 model profile별 token bucket으로 제어한다.

## 10. Dependency Injection

1. Application handler는 constructor로 repository/clock/id generator/policy/port를 받는다.
2. production bootstrap과 test fixture가 같은 constructor를 사용한다.
3. default argument에 client, session, mutable object를 생성하지 않는다.
4. `ClockPort`, `RandomPort`, `ArtifactPort`, `DatasetPort`, `LLMPort`, repository Protocol을 명시한다.
5. factory는 plugin descriptor와 strict parameter를 받아 concrete instance를 만든다.
6. DI container는 `apps/*/bootstrap.py`에 한정한다. domain code에서 container 조회 금지.
7. test는 mock보다 in-memory fake 또는 real Testcontainers adapter를 우선한다. 통계 engine은 fake 계산기를 사용하지 않고 작은 exact fixture를 사용한다.

## 11. 결정론과 수치 규칙

### 11.1 Randomness

- 모든 random 함수는 `numpy.random.Generator(PCG64(seed))` instance를 argument로 받는다.
- `numpy.random.*`, Python module-global `random`, 현재 시각 기반 seed를 사용하지 않는다.
- parallel trial은 `SeedSequence(seed).spawn(n)`으로 index-stable child seed를 만든다.
- trial 실행 순서가 바뀌어도 trial key와 child seed mapping은 유지한다.

### 11.2 Numeric

- 돈, fee, contract quantity, cash ledger는 `Decimal` 또는 integer minor unit을 사용한다.
- 대규모 통계 배열은 `float64`를 사용하고 `float32`는 ML model 내부 성능 근거와 tolerance test가 있을 때만 허용한다.
- settlement 외 중간 계산을 tick/cent로 반올림하지 않는다.
- 합계는 stable sort와 pairwise/Kahan summation 중 module이 선언한 방식을 사용한다.
- matrix solver는 tolerance, condition number, fallback rule을 artifact에 기록한다.
- divide-by-zero, singular matrix, insufficient sample은 warning number가 아니라 typed invalid result다.

### 11.3 Canonical serialization

- object key lexicographic sort
- Unicode NFC normalization
- Decimal의 trailing zero 제거, `-0`은 `0`
- timestamp UTC `Z`, microsecond 6자리
- 무순서 set-like array는 schema가 지정한 stable key로 sort·dedup
- ordered legs, DAG ordinal, event stream은 순서를 보존
- JSON UTF-8, whitespace 없음

Fingerprint test는 각 구성요소 하나를 바꾸면 hash가 바뀌고 key order만 바꾸면 같음을 검증한다.

## 12. LLM 실행 규칙

### 12.1 허용

- mandate 구조화, domain 후보, evidence retrieval plan
- question/hypothesis/StrategySpec draft
- 경제 메커니즘·falsifier·negative control·alternative explanation
- 정성적 결과 해석과 failure taxonomy draft

### 12.2 경계

- system prompt에는 schema version, allowed enum, budget, domain boundary, forbidden feedback를 포함한다.
- response format은 provider structured-output 기능과 local strict validator를 함께 사용한다.
- 첫 schema 실패 후 오류 path만 제공하는 repair를 1회 허용한다. 두 번째 실패는 job 실패다.
- 자유 형식 markdown/code를 executable output으로 해석하지 않는다.
- LLM의 수치, p-value, P&L, gate decision을 신뢰하지 않고 deterministic code로 재계산한다.
- generated code 실행은 제품 범위 밖이다. StrategySpec은 declarative node와 registered plugin ref만 포함한다.
- prompt injection 가능 source text는 data delimiter 안에 넣고 instruction으로 승격하지 않는다.
- source claim과 model inference를 다른 field에 저장한다.
- training 상세, validation coarse, sealed hidden view를 type과 repository query로 분리한다.
- token, candidate, revision, wall-clock budget을 호출 전에 차감하고 실패 호출도 provider cost가 발생하면 token budget에 포함한다.

## 13. Data, 시간, 단위 규칙

### 13.1 Point-in-time

- 의사결정 시각 `t`의 모든 input row는 `available_at <= t`를 만족해야 한다.
- `event_time`은 경제 사건 시각이고 `available_at`은 시스템 사용 가능 시각이다. 둘을 대체하지 않는다.
- 수정 데이터는 revision lineage와 당시 available_at을 보존한다.
- as-of join은 명시 direction=`backward`, tolerance, exact-match policy를 요구한다.
- `.bfill()`, negative `shift`, centered rolling, 전체 기간 normalization/selection은 strategy path에서 금지한다.
- cross-validation transformer는 각 train fold 안에서 fit한다.

### 13.2 Timezone과 calendar

- internal datetime은 aware UTC다. `datetime.now()` 대신 injected clock의 `now_utc()`를 쓴다.
- 시장 session 변환은 versioned calendar adapter를 사용한다.
- DST ambiguous/nonexistent time은 source offset 없이는 거부한다.
- 기간 끝은 항상 exclusive다.

### 13.3 Units

- 변수명에 단위를 붙인다: `latency_ms`, `fee_bps`, `duration_seconds`, `notional_quote`.
- rate와 percent를 같은 타입으로 쓰지 않는다. 100 bps = rate 0.01 변환은 named function 하나가 담당한다.
- price/quantity/currency는 instrument contract와 함께 전달한다.
- annualization factor는 asset calendar·frequency config에서 얻고 252를 숨은 상수로 넣지 않는다.

## 14. Quant, 통계, 회계 구현 규칙

1. 통계 함수는 입력 sample count, missing 처리, method, parameters, seed, statistic, p-value/interval, validity status를 함께 반환한다.
2. 정상성이 가정인 시계열·잔차에만 ADF와 KPSS를 함께 사용한다. 서로 충돌하면 inconclusive로 처리하고 자동 평균회귀 판정을 하지 않는다.
3. dependence 진단은 ACF/PACF와 Ljung–Box를 함께 보고 lag 선택 규칙을 기록한다.
4. 정규성을 가정하지 않는다. QQ/Anderson–Darling과 tail diagnostic을 기록하고 비정규 표본의 interval은 block/bootstrap 또는 robust method를 사용한다.
5. sparse/regime sample의 Bayesian model은 prior family와 hyperparameter를 schema와 report에 기록한다.
6. time-series split은 chronological walk-forward, overlapping label은 purge+embargo다. random shuffle split 금지.
7. selection, normalization, hedge estimation, regime fit은 training/formation 안에서만 수행한다.
8. gross P&L에서 fee, slippage, impact, borrow, funding, other를 한 번씩 빼 net P&L을 만든다. 부호를 module마다 재정의하지 않는다.
9. order fill 합은 order quantity를 넘지 않고, cash·position·realized/unrealized P&L은 각 event 뒤 reconciliation 가능해야 한다.
10. risk metric은 return convention, horizon, confidence, estimator를 결과에 포함한다. VaR/ES confidence를 숨은 default로 두지 않는다.
11. kill-switch와 hard risk limit 위반은 signal보다 우선하는 state transition이다.

## 15. Database와 Artifact 코드 규칙

- repository method는 aggregate 단위이며 ORM model을 application layer로 반환하지 않는다.
- transaction은 application Unit of Work가 소유한다. repository가 자체 commit하지 않는다.
- lazy-loaded relationship을 금지하고 필요한 query projection을 명시한다.
- N+1 query는 integration query-count test로 차단한다.
- raw SQL은 job claim, recursive DAG validation, performance-critical report query, migration에만 허용하며 query plan test와 parameter binding을 요구한다.
- migration에서 application model을 import하지 않는다.
- large JSON, DataFrame, NumPy array를 DB column에 넣지 않는다.
- artifact는 temporary write → hash → content-addressed upload → DB link 순서를 따른다.
- artifact reader는 checksum을 검증하고 mismatch 시 cache 사용을 중단한다.
- blob path를 사용자 input으로 조합하지 않는다.

## 16. Security 규칙

- secret은 Key Vault/managed identity로 받고 config file, CLI argument, exception, fixture에 넣지 않는다.
- JWT 검증은 signature, issuer, audience, expiry, clock skew, key id를 모두 검사한다.
- authorization은 route decorator만이 아니라 application handler에서 재검사한다.
- tenant_id와 actor_id는 request body에서 받지 않고 verified identity context에서 주입한다.
- SQL, shell, template, path는 parameterized API를 사용한다. `shell=True`, `eval`, `exec`, unsafe pickle/yaml loader 금지.
- model artifact는 signed manifest와 allowlisted loader만 사용한다. Python pickle을 외부에서 읽지 않는다.
- LLM/tool input에서 secret scanner를 실행하고 발견 시 호출을 차단한다.
- sensitive artifact download는 short-lived, single-purpose authorization을 사용한다.
- dependency/image는 hash/signature와 SBOM을 갖는다.

## 17. Testing 규칙

- bug fix는 실패를 재현하는 test를 먼저 추가한다.
- test는 wall clock, network, 외부 LLM, 실제 cloud credential에 의존하지 않는다.
- unit test는 pure invariant와 calculation; contract test는 schema/plugin; integration은 PostgreSQL/Blob; E2E는 application flow를 검증한다.
- fixture는 factory function으로 만들고 숨은 default를 최소화한다.
- expected metric은 구현 코드를 호출해 계산하지 않고 손계산, 독립 library, frozen golden artifact 중 하나를 사용한다.
- floating 비교는 의미 기반 tolerance를 명시하고 전역 loose tolerance를 두지 않는다.
- test ordering 의존, retry로 flaky test 숨김, sleep 기반 동기화를 금지한다.
- concurrency test는 barrier와 deterministic scheduler/event를 사용한다.
- production incident fixture는 anonymize한 후 regression suite에 편입한다.

세부 test matrix와 threshold는 [TestPlan](07_TestPlan.md)이 소유한다.

## 18. Code Review 규칙

### 18.1 PR 크기와 내용

- 한 PR은 하나의 작업 패키지 또는 분리 가능한 contract 변경만 포함한다.
- generated file과 handwritten file을 별도 commit으로 나눈다.
- schema/migration/holdout/accounting 변경은 최소 2명의 review가 필요하며 한 명은 Human Quant Reviewer 또는 Tech Lead다.
- AI 작성 PR은 작성 model/prompt hash를 기록하되 raw prompt를 공개하지 않는다.

### 18.2 필수 checklist

- [ ] FR/NFR/AC와 상태 전이가 명시됐다.
- [ ] domain boundary와 import rule을 지킨다.
- [ ] 입력 시점과 split이 causal하다.
- [ ] 비용·수량·cash·P&L 부호와 단위가 일치한다.
- [ ] error는 stable code이며 retry 분류가 정확하다.
- [ ] log와 artifact에 secret/PII가 없다.
- [ ] random seed, version, fingerprint 영향이 반영됐다.
- [ ] concurrency/idempotency/holdout race가 검토됐다.
- [ ] migration과 index가 query/retention 패턴에 맞는다.
- [ ] 정상·실패·경계·공격 test가 있다.
- [ ] API/DB/docs/ADR drift가 없다.

## 19. Git, Release, Documentation

- branch는 `feat/WP-M3-F04-portfolio-constructor`, `fix/AF-DATA-001-asof-boundary` 형식이다.
- commit은 imperative summary와 관련 ID를 포함한다.
- main 직접 push 금지, required checks와 review 후 squash merge한다.
- release tag는 `vMAJOR.MINOR.PATCH`; image digest, Git commit, migration head, schema catalog hash를 release manifest에 기록한다.
- code behavior 변경과 문서 갱신을 다른 PR로 미루지 않는다.
- `docs/README.md`의 변경 영향 매트릭스를 따른다.
- Accepted ADR을 supersede할 때 새 ADR을 만들고 이전 ADR status를 `Superseded by ADR-NNNN`으로 바꾼다.
- 미완성 표기, 비어 있는 example, 구현되지 않은 `pass`, 무조건 성공하는 stub은 release branch에 허용하지 않는다. MVP synthetic engine도 deterministic calculation과 의도된 pass/fail을 반환해야 한다.

## 20. 금지 패턴

```text
LLM text → eval/exec → backtest
validation metric → question generator exact feedback
sealed result → same lineage revision
dict[str, Any] domain payload
global random / current-time seed
naive datetime / hidden timezone conversion
future bfill / centered rolling / full-period fit
engine-internal fee default
catch Exception and continue
DB transaction across remote I/O
cross-lab internal import
mutable global cache
artifact without hash/schema/provenance
AI actor approval or LIVE transition
```

이 패턴은 review 지침에 그치지 않고 lint, architecture, schema, property, leakage, authorization test로 차단한다.
