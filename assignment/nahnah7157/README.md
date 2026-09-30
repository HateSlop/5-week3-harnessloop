# 논문 탐색 및 서지 정제 Agent (Paper Research Agent)

## 1. Agent 목적
* **과제**: 논문 작성 시 주제 관련 선행 연구를 탐색하고, 각주/참고문헌(APA 서식)을 수동으로 정제할 때 발생하는 번거로움을 자동화하여 해결합니다.
* **주요 기능:** arXiv 학술 DB 실시간 검색 ➔ 선택한 논문 초록 확인 ➔ APA 스타일 참고문헌 생성 ➔ 요약 결과 파일 자동 저장

## 2. Tools
1. `search_paper(query, max_results)`: arXiv API를 통해 키워드 기반 논문 목록 검색
2. `read_paper(arxiv_id)`: 특정 논문의 전체 초록 및 상세 서지 정보 조회
3. `save_summary(content, filename)`: 최종 요약본 및 참고문헌을 로컬 파일(`output/paper_summary.md`)로 저장

## 3. Agent Flow & Harness
* **Agent Flow:** 사용자 요청 ➔ `search_paper` ➔ `read_paper` (인용 대상 검증) ➔ `save_summary` ➔ 최종 답변
* **Harness Engineering:**
  * `recursion_limit`을 통한 한 번 실행 당 최대 Step 제한 (Execution Budget)
  * API 타임아웃/에러 시 `RunTrace` 기반 재시도(Retry) 및 이력 관리
* **Loop Engineering:**
  * `verify_run`: Tool 실제 호출 여부, Markdown 헤더 유무, **arXiv ID 할루시네이션 대조 검증**
  * 검증 실패 시 Feedback을 추가하여 최대 3회까지 재실행 (`run_with_verification`)

## 4. 실행 방법
```bash
# 가상환경 활성화 후 실행
python assignment/nahnah/agent.py