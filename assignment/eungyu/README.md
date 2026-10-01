# Semiconductor Paper Study Agent

반도체 및 전자공학 관련 논문·연구 노트·기술 자료를 읽고, 필요한 배경 개념을 추가로 탐색하여 학습 및 발표 준비 자료로 정리하는 LangChain 기반 AI Agent입니다.

이번 과제에서는 LangChain의 `create_agent`를 이용한 Agent Loop와 함께, Agent 실행을 제한하고 관찰하는 Harness Engineering, 결과를 검증하고 필요하면 다시 실행하는 Loop Engineering을 구현했습니다.

---

## 1. Agent 목적

전자공학 및 반도체 관련 자료를 공부할 때 다음 작업을 지원합니다.

- 로컬에 저장된 논문 노트 및 기술 자료 읽기
- 자료 이해에 필요한 배경 개념 검색
- 핵심 내용 및 전공 개념 정리
- 발표에서 강조할 내용 정리
- 예상 질문 생성
- 완성된 학습 자료를 Markdown 파일로 저장

최종 학습 결과는 다음 네 개의 섹션으로 구성됩니다.

```text
## 핵심 요약
## 핵심 개념
## 발표 포인트
## 예상 질문
```

단순히 자료를 요약하는 것에 그치지 않고, 발표나 추가 학습에 바로 활용할 수 있도록 구조화된 결과를 만드는 것을 목표로 합니다.

---

## 2. Tools

Agent에는 총 3개의 Tool을 등록했습니다.

### `read_file`

저장소 내부의 텍스트 또는 Markdown 파일을 읽는 Tool입니다.

주요 역할:

- 논문 노트 읽기
- 연구 자료 읽기
- 기술 문서 내용 확인

저장소 바깥의 파일에는 접근하지 못하도록 경로를 제한했습니다.

---

### `wikipedia_search`

한국어 Wikipedia에서 추가적인 배경 개념을 검색하는 Tool입니다.

주요 역할:

- 자료에 없는 배경지식 탐색
- 전공 용어 검색
- 관련 개념 확인

모든 실행에서 강제로 호출하는 것이 아니라, 모델이 현재 Task를 수행하기 위해 필요하다고 판단할 때만 사용합니다.

---

### `save_markdown`

완성된 학습 결과를 Markdown 파일로 저장하는 Tool입니다.

결과 파일은 다음 위치에 저장됩니다.

```text
assignment/eungyu/outputs/
```

예:

```text
assignment/eungyu/outputs/nand_study.md
```

---

## 3. Agent Flow

LangChain의 `create_agent`를 이용하여 기본적인 Agent Loop를 구성했습니다.

```text
User Task
   ↓
Model
   ↓
다음 행동 판단
   ↓
Tool Call 필요?
   ├─ Yes
   │    ↓
   │   Tool
   │    ↓
   │ Observation
   │    ↓
   │   Model
   │
   └─ No
        ↓
   Final Answer
```

Python 코드에서 Tool 실행 순서를 미리 정하지 않았습니다.

`read_file`, `wikipedia_search`, `save_markdown`을 Agent에 Tool로 등록하고, 모델이 현재 Task와 이전 Tool 실행 결과를 바탕으로 다음 행동과 Tool 사용 여부를 직접 결정합니다.

따라서 항상 세 개의 Tool을 모두 사용하는 것이 아니라, 사용자의 요청과 현재 Context에 따라 필요한 Tool만 선택할 수 있습니다.

---

## 4. Harness Engineering

한 번의 Agent Run을 제한하고 관찰하기 위한 Harness를 구현했습니다.

### Execution Budget

한 Run 안에서 Agent가 무한히 반복되지 않도록 실행 단계 수를 제한합니다.

```python
MAX_STEPS = 30
```

LangChain 실행 시 `recursion_limit`을 통해 적용합니다.

---

### Timeout

모델 호출이 무한히 대기하지 않도록 Timeout을 설정했습니다.

```python
MODEL_TIMEOUT = 30.0
```

---

### Error Handling

Agent 실행 중 오류가 발생하면 `try / except`를 통해 처리합니다.

오류가 발생한 경우 Trace에 오류 종류와 메시지를 기록합니다.

예:

```text
ERROR | ErrorType: message
```

---

### Retry

일시적인 실행 오류가 발생한 경우 제한된 횟수만큼 동일 Run을 다시 시도할 수 있도록 구성했습니다.

```python
MAX_RETRIES = 1
```

무한 Retry가 발생하지 않도록 상한을 두었습니다.

---

### Stop Condition

Agent가 Tool 사용을 마치고 최종 답변을 생성하면 현재 Run을 종료합니다.

정상 종료 시:

```text
RUN_END | COMPLETED
```

오류가 Retry 한도를 초과하면:

```text
RUN_END | ERROR
```

상태로 종료합니다.

---

### Trace

Agent 내부 실행 과정을 확인할 수 있도록 주요 이벤트를 Trace로 기록합니다.

```text
RUN_START
MODEL_RUN
TOOL_CALL
TOOL_RESULT
MODEL_ANSWER
RUN_END
```

이를 통해 다음 내용을 확인할 수 있습니다.

- 어떤 Tool을 사용했는지
- Tool에 어떤 입력이 전달되었는지
- Tool이 어떤 결과를 반환했는지
- Agent가 Tool 결과를 바탕으로 어떤 최종 답변을 생성했는지

---

## 5. Loop Engineering

한 번의 Agent Run이 정상적으로 종료되었더라도 최종 결과가 사용자의 요구사항을 만족한다는 보장은 없습니다.

따라서 Agent 바깥에 별도의 Verification Loop를 구현했습니다.

```text
Task
 ↓
Agent + Harness
 ↓
Result
 ↓
Verification
 ↓
조건 만족?
 ├─ Yes
 │    ↓
 │   End
 │
 └─ No
      ↓
   Feedback
      ↓
    Re-run
```

---

### Verification

최종 답변에 다음 네 개의 필수 섹션이 모두 존재하는지 검사합니다.

```text
## 핵심 요약
## 핵심 개념
## 발표 포인트
## 예상 질문
```

추가로 다음 항목도 검사합니다.

- 네 섹션이 올바른 순서로 작성되었는지
- 최종 답변이 지나치게 짧지 않은지

---

### Conditional Stop

Verification을 통과하면 전체 Loop를 즉시 종료합니다.

```text
[VERIFICATION] PASS
[LOOP END]
```

---

### Feedback / Re-run

Verification에 실패하면 누락된 요구사항을 구체적인 Feedback으로 생성합니다.

예:

```text
이전 결과가 검증을 통과하지 못했습니다.
다음 문제를 수정하여 전체 답변을 다시 작성하세요.

- 필수 섹션이 없습니다: ## 핵심 요약
- 필수 섹션이 없습니다: ## 핵심 개념
- 필수 섹션이 없습니다: ## 발표 포인트
- 필수 섹션이 없습니다: ## 예상 질문
```

이 Feedback과 이전 실행 결과를 새로운 Agent Run의 Context에 포함하여 다시 실행합니다.

---

### Loop Limit

Verification 실패가 반복되어도 무한히 실행되지 않도록 전체 Agent Run 횟수를 제한했습니다.

```python
MAX_RUNS = 3
```

3회 안에 Verification을 통과하지 못하면 다음 상태로 종료합니다.

```text
LIMIT_REACHED
```

---

## 6. 실행 방법

### 1. Conda 환경 활성화

프로젝트 루트에서 Week 3 환경을 활성화합니다.

```bash
conda activate 5-week3
```

### 2. 환경 변수 설정

프로젝트 루트의 `.env` 파일에 OpenAI API Key가 필요합니다.

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
```

API Key는 코드나 GitHub Repository에 포함하지 않습니다.

### 3. Agent 실행

프로젝트 루트에서 다음 명령어를 실행합니다.

```bash
python assignment/eungyu/agent.py
```

실행하면 다음과 같이 Task 입력을 받습니다.

```text
Task>
```

여기에 분석하고 싶은 자료와 원하는 작업을 입력합니다.

---

## 7. 실행 예시

Agent의 반도체 학습 기능을 확인하기 위해 다음 3D NAND Flash 학습 노트를 사용했습니다.

```text
assignment/eungyu/sample_3d_nand_note.md
```

테스트 입력:

```text
assignment/eungyu/sample_3d_nand_note.md를 읽고
3D NAND Flash 학습 자료를 정리해줘.
이해에 필요한 배경 개념은 필요하면 검색하고,
완성된 결과를 nand_study.md로 저장해줘.
```

### Run 1

Agent는 먼저 로컬 3D NAND 학습 노트를 읽었습니다.

```text
TOOL_CALL | read_file
TOOL_RESULT | read_file
```

이후 추가 배경 개념을 확인하기 위해 검색을 시도했습니다.

```text
TOOL_CALL | wikipedia_search | {'query': '3D NAND Flash'}
```

그리고 정리된 결과를 Markdown 파일로 저장했습니다.

```text
TOOL_CALL | save_markdown
TOOL_RESULT | save_markdown
```

결과 파일:

```text
assignment/eungyu/outputs/nand_study.md
```

파일 저장 자체는 성공했지만, 첫 번째 Run의 최종 응답은 단순히 저장 완료 사실만 설명했습니다.

따라서 최종 답변에 다음 필수 섹션들이 포함되지 않았습니다.

```text
## 핵심 요약
## 핵심 개념
## 발표 포인트
## 예상 질문
```

Verification 결과:

```text
[VERIFICATION] FAIL
```

구체적인 Feedback이 생성되었습니다.

```text
이전 결과가 검증을 통과하지 못했습니다.
다음 문제를 수정하여 전체 답변을 다시 작성하세요.

- 필수 섹션이 없습니다: ## 핵심 요약
- 필수 섹션이 없습니다: ## 핵심 개념
- 필수 섹션이 없습니다: ## 발표 포인트
- 필수 섹션이 없습니다: ## 예상 질문
- 학습 결과가 너무 짧습니다.
```

이후 새로운 Agent Run이 시작되었습니다.

```text
========== LOOP 2 ==========
```

### Run 2

두 번째 Run에서는 이전 Verification Feedback을 반영하여 네 개의 필수 섹션을 모두 포함한 답변을 생성했습니다.

```text
## 핵심 요약
## 핵심 개념
## 발표 포인트
## 예상 질문
```

최종적으로 Verification을 통과했습니다.

```text
[VERIFICATION] PASS
[LOOP END]

FINAL STATUS: PASSED | runs=2
```

실제 결과에서는 다음과 같은 3D NAND Flash 핵심 내용을 정리했습니다.

- Vertical Channel
- Word Line
- Gate Dielectric
- Charge Trap Layer
- Bit Line
- Program / Erase / Read
- Threshold Voltage
- Retention
- Endurance
- 높은 Aspect Ratio에 따른 공정 문제
- Cell-to-cell Variation

이를 통해 다음 Loop가 실제로 동작함을 확인했습니다.

```text
Agent Run
→ Verification FAIL
→ Feedback
→ Re-run
→ Verification PASS
→ End
```

---

## 8. 구현 구조 요약

전체 Agent는 크게 세 계층으로 구성됩니다.

```text
1. Agent
   └─ LangChain create_agent
      └─ Model이 Tool 사용 여부와 다음 행동 결정

2. Harness
   ├─ Execution Budget
   ├─ Timeout
   ├─ Error Handling
   ├─ Retry
   ├─ Stop Condition
   └─ Trace

3. Outer Loop
   ├─ Verification
   ├─ Conditional Stop
   ├─ Feedback
   ├─ Re-run
   └─ Loop Limit
```

단순히 LLM에 질문하고 답변을 받는 것이 아니라,

1. 필요한 Tool을 선택하여 정보를 수집하고
2. Harness를 통해 실행을 제한하고 관찰하며
3. 최종 결과를 Verification하고
4. 문제가 있으면 Feedback을 생성하여 다시 실행하는

Agent 구조를 구현했습니다.