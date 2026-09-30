# PUBG 패치노트 요약 Agent

Week 3 (Harness & Loop Engineering) 과제. `workshop/03_langchain_langgraph.ipynb`의 구조(LangChain
`create_agent` + Tool, 규칙 검증 + LLM Evaluator 검증/재작성 Loop)를 배틀그라운드(PUBG) 공식
Naver Cafe 패치노트 요약이라는 목적에 맞춰 재구성했다. LangGraph `StateGraph`는 사용하지 않고,
Harness와 Loop는 Python 함수로 직접 구현했다.

## 1. Agent 목적

- **해결하는 문제:** 배틀그라운드 패치노트는 [PUBG 공식 Naver Cafe](https://cafe.naver.com/f-e/cafes/28866679/articles/6270454?menuid=33)에
  올라오는데, 원문이 길고 카테고리(밸런스 조정/버그 수정/신규 콘텐츠 등)가 뒤섞여 있어 빠르게 파악하기
  어렵다. 이 Agent는 지정한 패치노트 URL의 본문을 확인하고, 정해진 형식(제목 - 원문 링크 - 카테고리별
  변경 사항)으로 한국어 요약을 작성한다.
- **주요 기능**
  - URL 기반 원문 수집을 시도하고, 실패 시 로컬 사본으로 자동 전환
  - 카테고리별로 구조화된 고정 형식 요약 생성
  - 형식 규칙 + Tool 사용 + LLM Evaluator 세 겹 검증과 실패 시 자동 재작성
  - 모든 실행을 Trace로 기록해 관찰 가능

### 제약: Naver Cafe와 로그인 벽

PUBG 공식 Cafe 게시글은 로그인 세션이 있어야 본문이 렌더링되는 Next.js SPA다. `curl`/`urllib`로
원문 HTML을 요청하면 로그인 폼과 빈 `<div id="app">` 셸만 내려오고 실제 텍스트는 내려오지 않는 것을
직접 확인했다 (모바일 페이지, 구버전 `ArticleRead.nhn`, 내부 API 엔드포인트 모두 동일). 그래서
`fetch_patch_note_url` Tool은 추출된 텍스트가 너무 짧으면 `status: "blocked"`를 반환하고, Agent가
`read_local_patchnote` Tool로 `patchnotes/`에 사람이 미리 저장해 둔 사본을 사용하도록 설계했다.
`patchnotes/sample_pubg_patch_note.txt`는 실제 환경에서 이 흐름을 시연하기 위한 **예시 데이터**이며,
실제 패치노트를 요약하려면 카페 글 본문을 복사해 이 폴더에 저장한 뒤 사용하면 된다.

## 2. Tools

Tool 구현은 모두 `agent.py`의 "0. 로컬 Tool 함수 구현" 섹션에 있으며(로컬 함수), 같은 파일에서
`@tool`로 감싸 `create_agent`에 전달한다.

| Tool | 역할 |
| --- | --- |
| `fetch_patch_note_url(url)` | URL의 HTML을 가져와 태그를 제거한 본문 텍스트를 반환. 추출된 텍스트가 너무 짧으면(로그인/JS 렌더링 페이지로 판단) `status: "blocked"`와 대체 경로 안내를 반환 |
| `read_local_patchnote(filename)` | `patchnotes/` 폴더에 저장된 패치노트 사본(UTF-8 텍스트)을 읽음. 폴더 밖 경로는 차단 |
| `save_summary(filename, content)` | 완성된 요약을 `output/` 폴더에 저장하고 저장 경로를 반환 |

세 Tool 모두 매 실행마다 호출되는 것은 아니며, Agent가 `fetch_patch_note_url` 결과의 `status`를 보고
`read_local_patchnote` 호출 여부를 스스로 판단한다.

## 3. Agent Flow

```
User (패치노트 URL) → Model → Tool Call(fetch_patch_note_url)
                                 → status 확인
                                 → (blocked면) Tool Call(read_local_patchnote)
                                 → Observation(원문 텍스트)
                              → Model (요약 작성)
                                 → Tool Call(save_summary)
                                 → Observation(저장 경로)
                              → Model → Final Answer(요약)
```

`create_agent`가 반환하는 `CompiledStateGraph`가 이 Model ↔ Tool 반복을 내부적으로 수행한다. 어떤
Tool을 몇 번 호출할지는 코드에서 미리 정하지 않고 모델이 매 단계 판단한다.

## 4. Harness (`agent.py`의 `run_agent`)

- **Stop Condition:** 검증 통과 시 즉시 종료, 또는 `MAX_RUNS` 도달 시 종료
- **Execution Budget:**
  - `MAX_RUNS = 3` — 요약 작성 재시도 횟수 상한 (Tool 호출 횟수와는 별개)
  - `MAX_SECONDS = 180` — `run_agent()` 전체 실행에 허용하는 시간 예산, 매 run 시작 전에 확인
  - `RECURSION_LIMIT = 20` — `create_agent` 내부 Tool Loop 한 번의 최대 Step 수
- **Error Handling:** `agent.invoke()` 호출을 `try/except`로 감싸 모델/Tool 예외를 잡고, 즉시 실행을
  중단하며 원인을 Trace에 남긴다 (재시도로 숨기지 않는다).
- **Trace:** `RUN_START`/`TOOL_CALL`/`TOOL_RESULT`/`VERIFY`/`FEEDBACK`/`STOP`/`ERROR` 이벤트를
  순서대로 리스트에 기록하고 동시에 콘솔에 출력. `RunResult.trace`로 실행 후에도 확인 가능.

## 5. Loop (검증 → 조건부 종료 → Feedback → 재실행)

1. **Verification** (`run_agent` 내부, 매 run마다 3가지 검증을 모두 수행)
   - 형식 규칙 (`verify_format`): 제목 줄(`# ...`), `- 원문 링크:` 항목, 최소 1개 이상의 `## 카테고리`
     섹션, 최소 1개 이상의 `- ` 불릿, 중괄호/코드 블록 미포함 여부
   - Tool 사용 (`verify_tool_usage`): 원문 확인 Tool(`fetch_patch_note_url` 또는
     `read_local_patchnote`)과 `save_summary`가 실제로 성공했는지 확인
   - LLM Evaluator (`evaluate_with_llm`): 별도 모델 호출로 원문과 비교해 지어낸 수치·항목이 없는지
     평가 (JSON Schema로 `passed`/`feedback` 반환, 요약을 직접 다시 쓰지는 않음)
2. **Conditional Stop:** 세 검증을 모두 통과하면 `RunResult(passed=True, ...)`로 즉시 종료
3. **Feedback / Re-run:** 실패한 항목을 모아 `build_feedback()`으로 문장을 만들고, 같은 대화의
   `messages`에 사용자 메시지로 추가해 다음 run에서 이전 맥락(이미 읽은 원문 포함)을 유지한 채
   재작성을 요청
4. **Loop Limit:** `MAX_RUNS`(3회) 도달 시 마지막 답변과 `stop_reason="loop_limit_reached"`로 종료

## 6. 실행 방법

### 환경 설정

1. 저장소 루트에서 의존성 설치 (루트 `requirements.txt` 재사용)
   ```bash
   pip install -r requirements.txt
   ```
2. 저장소 루트 `.env`에 `OPENAI_API_KEY`, `OPENAI_MODEL` 설정 (형식은 `.env.example` 참고)

### 실행

```bash
cd assignment/sohnmj
python agent.py
```

기본 `TASK`는 PUBG 공식 Cafe 패치노트 URL을 요약하도록 지시하며, 접근이 막히면
`patchnotes/sample_pubg_patch_note.txt`를 사용하라는 안내가 함께 포함되어 있다. 실제 최신 패치노트를
요약하려면 카페 글 본문을 복사해 `patchnotes/`에 새 파일로 저장한 뒤, `agent.py`의 `TASK`(또는
`LOCAL_FALLBACK_FILENAME`)를 그 파일명으로 바꿔 실행하면 된다.

## 7. 실행 예시

`python agent.py` 실행 시 다음과 같은 순서로 로그가 출력된다 (실제 모델 응답 내용은 실행마다 달라질
수 있음).

```
Python: 3.12.x | Model: gpt-4.1-mini | Max Runs: 3
[RUN_START] {'run': 1}
[TOOL_CALL] {'run': 1, 'name': 'fetch_patch_note_url', 'args': {'url': 'https://cafe.naver.com/...'}}
[TOOL_RESULT] {'run': 1, 'name': 'fetch_patch_note_url', 'preview': '{"status": "blocked", ...'}
[TOOL_CALL] {'run': 1, 'name': 'read_local_patchnote', 'args': {'filename': 'sample_pubg_patch_note.txt'}}
[TOOL_RESULT] {'run': 1, 'name': 'read_local_patchnote', 'preview': '[예시 데이터 안내] ...'}
[TOOL_CALL] {'run': 1, 'name': 'save_summary', 'args': {'filename': 'patch_note_summary.md', 'content': '...'}}
[TOOL_RESULT] {'run': 1, 'name': 'save_summary', 'preview': '저장 완료: output/patch_note_summary.md'}
[VERIFY] {'run': 1, 'passed': True, 'tool_issues': 0, 'rule_issues': 0, 'llm_passed': True}

[FINAL STATUS] PASS | runs: 1
[FINAL ANSWER]
 # PC 27.1 패치노트 (예시)
- 원문 링크: patchnotes/sample_pubg_patch_note.txt
## 신규 콘텐츠
- 신규 총기 스킨 박스 '섬멸자' 시리즈가 상점에 추가되었습니다.
- 에란겔 맵에 신규 랜드마크 '캠프 알파'가 추가되었습니다.
## 밸런스 조정
- M416의 반동 회복 속도를 5% 감소시켰습니다.
- 레벨 3 방탄복의 방어력을 소폭 상향했습니다.
- 차량 폭발 시 데미지 범위를 축소했습니다.
## 버그 수정
- 특정 상황에서 낙하산이 정상적으로 펼쳐지지 않던 문제를 수정했습니다.
- 리플레이 시스템에서 관전 카메라가 튕기는 문제를 수정했습니다.
- 일부 텍스트가 언어 설정과 무관하게 영어로 표시되던 문제를 수정했습니다.
```

검증에 실패하는 경우 `[VERIFY] {..., 'passed': False, ...}` 다음에 `[FEEDBACK]` 로그가 출력되고,
같은 대화 맥락에서 재작성 후 다시 `[VERIFY]`가 출력된다. `MAX_RUNS`에 도달해도 통과하지 못하면
`[FINAL STATUS] NOT PASSED (loop_limit_reached)`로 마지막 시도 결과를 출력한다.
