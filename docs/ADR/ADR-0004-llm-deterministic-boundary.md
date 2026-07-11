# ADR-0004: LLM 생성과 결정론적 판정의 경계를 강제한다

- 상태: Accepted
- 결정일: 2026-07-11

## Context

LLM은 연구 후보 생성에는 유용하지만 같은 입력에서 다른 출력을 만들 수 있고 데이터·engine 가용성이나 수치 결과를 권위 있게 판정할 수 없다. LLM이 상태나 실행을 직접 제어하면 재현성과 감사 가능성을 잃는다.

## Decision

LLM은 질문·가설·전략 후보와 설명만 생성한다. Orchestrator가 Knowledge, Capability, budget, schema를 요청에 포함한다. 출력은 untrusted JSON으로 취급하며 schema와 hard gate 통과 전에는 저장·실행하지 않는다. 상태 전이, 예산, routing 확정, engine 실행, validation, Registry 기록은 코드만 수행한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 자율 agent가 tool과 DB 직접 사용 | 실행 권한과 감사 경계가 불명확 |
| LLM 없이 template만 사용 | 영역 지시 연구 후보 다양성이 부족 |
| LLM 판정과 코드 판정 병합 | 실패 재현과 책임 분리가 불가능 |

## Consequences

- 긍정: provider를 바꿔도 권위 상태와 engine은 안정적이다.
- 긍정: 악성·잘못된 출력이 실행 전에 차단된다.
- 부정: structured schema와 validation code를 유지해야 한다.
- 부정: prompt만으로 간단히 보이는 기능도 application flow가 필요하다.

## 강제 방법

LLM port는 plain JSON만 반환하고 repository를 주입받지 않는다. architecture test와 CT-LLM suite로 sealed 정보·코드 실행·상태 변경을 차단한다.

## 재검토 조건

LLM 신뢰도가 향상되어도 이 경계는 유지한다. 후보 생성 외 작업을 허용하려면 별도 ADR, sandbox, 권한, 결정론적 사후 검증이 모두 필요하다.
