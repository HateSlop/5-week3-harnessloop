# 국어학 및 학술 연구자를 위한 논문 탐색·서지 정제 Agent (Korean Studies Research Agent)

## 1. Agent 목적
* **해결하려는 문제:** 국어학·국문학 선행연구 분석 시, 수많은 논문을 일일이 검색하여 읽고 필요한 문장을 원문 그대로 발췌하거나 서론-본론-결론 구조로 요약하고, 한국어 표준 참고문헌 서식을 정리할 때 발생하는 반복적이고 비효율적인 수작업을 자동화합니다.
* **주요 기능:**
  * 로컬 논문 데이터베이스(`papers/` 폴더) 실시간 키워드 탐색
  * 특정 논문의 메타데이터 및 전체 본문/초록 상세 조회
  * **서론-본론-결론 구조화 요약** 및 **원문 그대로 발췌한 연구 활용 포인트** 추출
  * 정제된 요약본 및 참고문헌 목록 자동 저장 (`assignment/nahnah/output/paper_summary.md`)

---

## 2. Tools (3개)
1. `search_paper(query, max_results)`: `papers/` 폴더 내의 로컬 `.txt` 논문들을 제목·학술지·본문 키워드로 검색하여 논문 ID, 제목, 저자, 학술지, 연도, 본문 일부를 반환합니다.
2. `read_paper(paper_id)`: 논문 ID(파일명)로 특정 논문의 전체 메타데이터와 본문(최대 4,000자)을 상세 조회합니다.
3. `save_summary(content, filename)`: 최종 정리된 구조화 요약 및 참고문헌 목록을 지정된 파일 경로(`assignment/nahnah/output/`)로 안전하게 저장합니다.

---

## 3. Agent Flow
1. **사용자 요청 수신:** 연구 주제 및 요약 조건 입력 (예: '국어 통사론' 또는 '연결어미' 연구 탐색)
2. **후보 논문 검색:** 시스템 지시문(`INSTRUCTIONS`)에 따라 `search_paper` 도구를 호출하여 관련 로컬 논문 목록 탐색
3. **본문 정독 및 검증:** 요약 대상 논문의 `read_paper` 도구를 호출해 상세 본문 및 메타데이터 확인
4. **구조화 및 발췌:** LLM이 수집된 논문 데이터를 바탕으로 [서론-본론-결론 구조 요약], [원문 그대로 발췌한 내 연구 활용 포인트], [한국어 표준 참고문헌] 작성
5. **결과 출력 및 파일 저장:** `save_summary` 도구를 실행해 `output/paper_summary.md` 파일 작성 후 최종 답변 반환

---

## 4. Harness Engineering
* **Execution Budget (실행 제한):** `config={"recursion_limit": max_steps}`를 적용해 한 실행(Run)당 최대 도구 호출 횟수를 제한하여 무한 루프 및 모델 API 비용 과다 청구를 차단합니다.
* **Error Handling & Retry:** SDK 자체 재시도를 비활성화(`max_retries=0`)하고, API 타임아웃(`APITimeoutError`)과 일반 API 오류를 명확히 구분하여 `run_once` 내부에서 전체 Run을 지정된 횟수(`max_retries`) 안에서 안전하게 재시도합니다.
* **Run Trace (실행 기록):** 실행 과정에서 발생한 모든 `TOOL_CALL`, `TOOL_RESULT`, `TIMEOUT`, `API_ERROR` 이벤트를 `RunTrace` 객체에 실시간으로 기록하고 터미널에 시각화합니다.

---

## 5. Loop Engineering (Verification)
* **Tool 실행 여부 검증:** 단순히 답변 텍스트의 표면 문구가 아닌, 실제 성공적으로 기록된 `RunTrace` 내의 Tool 호출 이력을 검증합니다.
* **필수 섹션 양식 검증:** 최종 답변 내 필수 마크다운 섹션(`## 1. 요약`, `## 2. 내 연구 활용 포인트`, `## 3. 참고문헌`)이 모두 정상적으로 포함되었는지 확인합니다.
* **인용 근거 대조 (Grounding):** 참고문헌에 작성된 논문 ID가 실제 `search_paper` / `read_paper` 결과 데이터 내에 존재하는지 대조하여 지어낸 논문(Hallucination)을 차단합니다.
* **원문 그대로 발췌 검증:** `## 2. 내 연구 활용 포인트` 섹션에 인용된 문장이 `read_paper`로 확인한 본문 텍스트에 실제로 **원문 그대로 포함**되어 있는지 대조합니다 (재해석/Paraphrase가 아닌 진짜 인용인지 검증).
* **Feedback & Loop Limit:** 검증 실패 시 구체적인 오류 지적사항을 `Feedback`으로 생성해 재실행(`run_with_verification`)하며, 무한 재시도를 방지하기 위한 반복 제한(`max_iterations=3`)을 적용합니다.

---

## 6. 실행 방법 및 사용 안내

### 환경 설정
1. 가상환경 활성화 및 필요 패키지 설치
2. 프로젝트 루트의 `.env` 파일에 `OPENAI_API_KEY` 설정

### 에이전트 실행
```bash
python assignment/nahnah/agent.py

##7. 실행 예시

[LOOP 1]
[RUN_START] '국어 통사론' 또는 '연결어미'에 관한 국어학 논문을 papers/ 폴더에서 탐색해서 서론-본론-결론 구조로 핵심 내용을 요약하고, 내 연구에 활용할 포인트 문장을 원문 그대로 발췌한 뒤, 한국어 표준 참고문헌 목
[TOOL_CALL] {"name": "search_paper", "args": {"query": "국어 통사론연결어미", "max_results": 3}}
[TOOL_RESULT] {"name": "search_paper", "status": "SUCCESS", "content": "{\"query\": \"국어 통사론 연결어미\", \"results\": [{\"paper_id\": \"KCI_2019_001\", \"title\": \"KCI_2019_001\
[TOOL_CALL] {"name": "read_paper", "args": {"paper_id": "KCI_2023_001"}}
[TOOL_RESULT] {"name": "read_paper", "status": "SUCCESS", "content":"{\"paper_id\": \"KCI_2023_001\", \"title\": \"국어 통사론 및 연결어미 형성의 제약 연구\", \"authors\": [\"김서강\"], \"journ
[TOOL_CALL] {"name": "save_summary", "args": {"content": "## 1. 요약\n\n### 서론: 연구목적\n본 연구는 현대 국어 통사론 및 연결어미 형성 시 나타나는 통사·의미적 제약을 고찰하는 데 목적을 두었다. 기존 논의의한계를 지적하며 연결어미 결합 방식의 
[TOOL_RESULT] {"name": "save_summary", "status": "SUCCESS", "content": "/Users/hannah/5-week3/5-week3-harnessloop/assignment/nahnah7157/output/paper_summary.md에 저장했습니다."}
[RUN_END] chars=222
[VERIFICATION] FAIL: ["답변에 '## 1. 요약' 섹션을 포함하고 서론/본론/결론 구조로 작성하세요.", "답변에 '## 2. 내 연구 활용 포인트' 섹션을포함해 인용할 문장을 발췌하세요.", "답변에 '## 3. 참고문헌' 섹션을 포함하세요.", '참고문헌에 정확한 논문 ID(예: KCI_2023_001)를 명시하세요.']

[LOOP 2]
[RUN_START] '국어 통사론' 또는 '연결어미'에 관한 국어학 논문을 papers/ 폴더에서 탐색해서 서론-본론-결론 구조로 핵심 내용을 요약하고, 내 연구에 활용할 포인트 문장을 원문 그대로 발췌한 뒤, 한국어 표준 참고문헌 목
[TOOL_CALL] {"name": "search_paper", "args": {"query": "국어 통사론연결어미", "max_results": 3}}
[TOOL_RESULT] {"name": "search_paper", "status": "SUCCESS", "content": "{\"query\": \"국어 통사론 연결어미\", \"results\": [{\"paper_id\": \"KCI_2019_001\", \"title\": \"KCI_2019_001\
[TOOL_CALL] {"name": "read_paper", "args": {"paper_id": "KCI_2023_001"}}
[TOOL_RESULT] {"name": "read_paper", "status": "SUCCESS", "content":"{\"paper_id\": \"KCI_2023_001\", \"title\": \"국어 통사론 및 연결어미 형성의 제약 연구\", \"authors\": [\"김서강\"], \"journ
[TOOL_CALL] {"name": "save_summary", "args": {"content": "## 1. 요약\n\n### 서론\n본 연구는 현대 국어 통사론에서 연결어미 형성 시 나타나는 통사적 및 의미적 제약을 고찰하는 것을 목적으로 한다. 기존 논의들이연결어미 결합의 제약에 대해 충분히 설명
[TOOL_RESULT] {"name": "save_summary", "status": "SUCCESS", "content": "/Users/hannah/5-week3/5-week3-harnessloop/assignment/nahnah7157/output/paper_summary.md에 저장했습니다."}
[RUN_END] chars=717
[VERIFICATION] PASS

=== 최종 답변 ===
## 1. 요약

### 서론
본 연구는 현대 국어 통사론에서 연결어미 형성 시 나타나는 통사적 및 의미적 제약을 고찰하는 것을 목적으로 한다. 기존 논의들이 연결어미 결합의 제약에 대해 충분히 설명하지 못하는 한계를 지적하며, 이 문제를 실증 데이터 분석을 통해 규명하고자 한다.

### 본론
연결어미 결합 방식에 있어서 선택 제약을 중심으로 다양한 용례 데이터를 검증하였다. 이를 통해 연결어미가 그 결합 가에 따라 특정한 통사적 및의미적 범주 내에서만 결합할 수 있음이 밝혀졌다. 또한, 연결어미 결합에 대해 상위 범주화 규칙을 제안하여 국어 통사론 내 연결어미의 체계적 이해를 도모하였다.

### 결론
본 연구는 연결어미 결합가에 따른 통사적, 의미적 제약을 체계적으로 설명하고, 이를 바탕으로 연결어미 결합의 상위 범주화 규칙을 제안하였다.이로써 국어 통사론에서 연결어미 연구의 기초를 확립하며, 향후 국어 통사 및 의미론 연구에 시사점을 제공하였다.

## 2. 내 연구 활용 포인트
- "본 연구는 현대 국어 통사론 및 연결어미 형성 시 나타나는 통사·의미적 제약을 고찰한다."
- "결론에서는 결합가에 따른 상위 범주화 규칙을 제안한다."

## 3. 참고문헌
- 김서강(2023), 「국어 통사론 및 연결어미 형성의 제약 연구」, 『국어학』, 102권, 45-78, [KCI_2023_001]

이 내용은 paper_summary.md 파일로 저장되었습니다.

=== 검증 결과 === PASS