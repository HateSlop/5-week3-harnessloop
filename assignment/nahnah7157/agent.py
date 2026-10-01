"""
국어학 연구자를 위한 논문 탐색 및 서지 정제 Agent (Korean Linguistics Research Agent)

- LangChain의 create_agent + "papers/" 폴더의 실제 로컬 논문 파일을 탐색·조회하는 Tool로 Agent Loop를 구성한다.
  (KCI·DBpia는 공개 API가 없거나 별도 승인이 필요해 이번 버전은 로컬 파일 기반으로 구현했다.
   papers/ 에 같은 형식의 .txt 파일을 추가하면 탐색 대상이 늘어난다.)
- Harness Engineering: Execution Budget(recursion_limit), Timeout/API 오류 처리와 Retry, Trace 기록.
- Loop Engineering: 서론/본론/결론 구조·발췌 인용·서지정보 근거를 검증하고, 실패하면 Feedback과 함께 재실행한다.

실행: python assignment/nahnah/agent.py
필요: 저장소 루트(또는 이 폴더)의 .env에 OPENAI_API_KEY

papers/ 폴더의 파일 형식 (한 파일 = 논문 한 편):
    제목: ...
    저자: ...
    학술지: ...
    연도: ...
    권호: ...
    페이지: ...
    ---
    (초록 또는 본문 전체)

파일명이 곧 논문 ID가 되므로, "KCI_2023_001.txt"처럼 "<출처코드>_<연도>_<일련번호>.txt" 형식을
권장한다(참고문헌 인용 근거 검증이 이 형식을 전제로 한다).
"""

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import APIError, APITimeoutError

# ------------------------------------------------------------------
# 0. 환경 설정
# ------------------------------------------------------------------
ASSIGNMENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(
    (path for path in (ASSIGNMENT_DIR, *ASSIGNMENT_DIR.parents)
     if (path / "requirements.txt").is_file()),
    ASSIGNMENT_DIR,
)
load_dotenv(PROJECT_ROOT / ".env")

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_TIMEOUT_SECONDS = 30.0
PAPERS_DIR = ASSIGNMENT_DIR / "papers"
OUTPUT_DIR = ASSIGNMENT_DIR / "output"
MAX_BODY_CHARS = 4000  # read_paper가 반환하는 본문 길이 상한(모델 Context 과다 사용 방지)

api_key = os.getenv("OPENAI_API_KEY", "")
if not api_key or api_key == "your-openai-api-key":
    raise RuntimeError("실행 전에 .env에 OPENAI_API_KEY를 설정하세요.")

# max_retries=0: SDK가 자체 재시도를 하지 않게 하고, Harness에서 재시도 여부를 직접 통제한다.
model = ChatOpenAI(model=MODEL_NAME, timeout=MODEL_TIMEOUT_SECONDS, max_retries=0)


# ------------------------------------------------------------------
# 1. Tool 3개 — 로컬 국어학 논문 파일 탐색·조회, 요약 파일 저장
# ------------------------------------------------------------------
PAPER_FIELD_LABELS = {
    "제목": "title", "저자": "authors", "학술지": "journal",
    "연도": "year", "권호": "vol_no", "페이지": "pages",
}


def _parse_paper_file(path: Path) -> dict:
    """papers/의 .txt 파일 하나를 '머리말(메타데이터) + ---  + 본문' 형식으로 파싱한다."""
    raw = path.read_text(encoding="utf-8")
    header_text, separator, body = raw.partition("\n---\n")
    if not separator:  # 구분선이 없으면 전체를 본문으로 취급
        header_text, body = "", raw

    metadata = {
        "paper_id": path.stem, "title": path.stem, "authors": [],
        "journal": "", "year": "", "vol_no": "", "pages": "",
    }
    for line in header_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        field_name = PAPER_FIELD_LABELS.get(key.strip())
        if field_name is None:
            continue
        value = value.strip()
        metadata[field_name] = (
            [item.strip() for item in value.split(",")] if field_name == "authors" else value
        )

    normalized_body = " ".join(body.split())
    metadata["abstract"] = normalized_body[:300]       # search_paper 결과 노출용 짧은 요약
    metadata["body"] = normalized_body[:MAX_BODY_CHARS]  # read_paper에서 쓸 본문(상한 적용)
    return metadata


def _load_papers() -> list:
    """papers/ 폴더의 모든 .txt 파일을 매번 새로 읽는다(실행 중 파일 추가도 바로 반영)."""
    if not PAPERS_DIR.exists():
        return []
    return [_parse_paper_file(path) for path in sorted(PAPERS_DIR.glob("*.txt"))]


@tool("search_paper")
def search_paper(query: str, max_results: int = 3) -> str:
    """papers/ 폴더의 로컬 국어학 논문 파일을 제목·학술지·본문 키워드로 검색해 후보 목록을 반환한다."""
    papers = _load_papers()
    if not papers:
        return json.dumps(
            {"query": query, "results": [], "notice": "papers/ 폴더에 논문 파일이 없습니다."},
            ensure_ascii=False,
        )

    keywords = [token for token in query.split() if token] or [query]
    matched = [
        paper for paper in papers
        if any(
            keyword in paper["title"] or keyword in paper["journal"] or keyword in paper["body"]
            for keyword in keywords
        )
    ]
    # 일치하는 논문이 없으면 가짜로 채우지 않고 빈 목록을 그대로 반환한다.
    results = [
        {key: paper[key] for key in ("paper_id", "title", "authors", "journal", "year", "vol_no", "pages", "abstract")}
        for paper in matched[:max_results]
    ]
    return json.dumps({"query": query, "results": results}, ensure_ascii=False)


@tool("read_paper")
def read_paper(paper_id: str) -> str:
    """paper_id(파일명, 예: KCI_2023_001)로 로컬 논문 파일의 전체 메타데이터와 본문을 조회한다."""
    for paper in _load_papers():
        if paper["paper_id"] == paper_id.strip():
            return json.dumps(paper, ensure_ascii=False)
    return json.dumps(
        {"error": f"'{paper_id}'에 해당하는 논문 파일을 papers/ 폴더에서 찾지 못했습니다."},
        ensure_ascii=False,
    )


@tool("save_summary")
def save_summary(content: str, filename: str = "paper_summary.md") -> str:
    """국어학 연구 요약본 및 참고문헌 정제 결과를 assignment/nahnah/output/ 아래 파일로 저장한다."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    safe_name = Path(filename).name  # 경로 조작(../ 등) 방지: 디렉터리 성분 제거
    file_path = OUTPUT_DIR / safe_name
    file_path.write_text(content, encoding="utf-8")
    return f"{file_path}에 저장했습니다."


TOOLS = [search_paper, read_paper, save_summary]

INSTRUCTIONS = (
    "당신은 국어학 및 국문학 연구자의 선행연구 분석을 돕는 학술 전문 Agent입니다. 다음 절차를 엄격히 따르세요.\n"
    "1. search_paper로 주제와 관련된 국어학 논문을 papers/ 폴더에서 검색하세요.\n"
    "2. 요약에 인용할 논문은 반드시 read_paper로 본문을 정독한 후 작성하세요. "
    "read_paper로 확인하지 않은 논문은 인용하지 마세요.\n"
    "3. 논문 요약 시 단순 요약에 그치지 말고, 반드시 아래의 마크다운 구조를 준수하세요:\n"
    "   - ## 1. 요약 (서론: 연구목적, 본론: 분석 내용/방법, 결론: 연구 성과로 나누어 정리)\n"
    "   - ## 2. 내 연구 활용 포인트 (read_paper로 확인한 본문에서 그대로 발췌한 핵심 문장 1~2개. "
    "paraphrase하지 말고 원문 그대로 인용하세요.)\n"
    "   - ## 3. 참고문헌 (저자(연도), 논문명, 학술지명, 권호, 페이지, [paper_id] 형식)\n"
    "4. search_paper나 read_paper 결과에 없는 논문 ID나 서지 정보를 절대로 지어내지 마세요.\n"
    "5. 정리가 완료되면 반드시 save_summary 도구로 'paper_summary.md'에 저장한 뒤 답변을 마치세요."
)

agent = create_agent(model=model, tools=TOOLS, system_prompt=INSTRUCTIONS)


# ------------------------------------------------------------------
# 2. Harness Engineering — Execution Budget, Error Handling, Retry, Trace
# ------------------------------------------------------------------
@dataclass
class RunTrace:
    status: str = "RUNNING"
    error: str = ""
    answer: str = ""
    events: list = field(default_factory=list)


def _record(trace: RunTrace, event: str, detail="") -> None:
    trace.events.append({"event": event, "detail": detail})
    print(f"[{event}]", detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)[:160])


def run_once(task: str, *, max_steps: int, max_retries: int) -> RunTrace:
    """Agent를 한 번 실행한다(Stop Condition=recursion_limit). Timeout/API 오류는 전체를 재시도한다."""
    trace = RunTrace()
    _record(trace, "RUN_START", task[:120])

    result = None
    for attempt in range(max_retries + 1):
        try:
            result = agent.invoke(
                {"messages": [HumanMessage(content=task)]},
                config={"recursion_limit": max_steps},
            )
            break
        except (APITimeoutError, TimeoutError) as error:
            _record(trace, "TIMEOUT", f"attempt={attempt}")
            if attempt >= max_retries:
                trace.status, trace.error = "TIMEOUT", type(error).__name__
                return trace
            _record(trace, "RETRY", f"{attempt + 1}/{max_retries}")
        except APIError as error:
            _record(trace, "API_ERROR", str(error)[:200])
            if attempt >= max_retries:
                trace.status, trace.error = "ERROR", type(error).__name__
                return trace
            _record(trace, "RETRY", f"{attempt + 1}/{max_retries}")

    for message in result["messages"]:
        if isinstance(message, AIMessage) and message.tool_calls:
            for call in message.tool_calls:
                _record(trace, "TOOL_CALL", {"name": call["name"], "args": call["args"]})
        elif isinstance(message, ToolMessage):
            status = "ERROR" if getattr(message, "status", "success") == "error" else "SUCCESS"
            _record(trace, "TOOL_RESULT", {
                "name": message.name, "status": status, "content": str(message.content),
            })

    trace.answer = str(result["messages"][-1].content)
    trace.status = "COMPLETED"
    _record(trace, "RUN_END", f"chars={len(trace.answer)}")
    return trace


# ------------------------------------------------------------------
# 3. Loop Engineering — Verification(형식·인용 근거·발췌 여부) + Feedback + Loop Limit
# ------------------------------------------------------------------
# <출처코드>_<연도>_<일련번호> 형식의 논문 ID만 인정한다 (예: KCI_2023_001, DBPIA_2022_010).
PAPER_ID_PATTERN = re.compile(r"\b([A-Z]+_\d{4}_\d{3,})\b")


@dataclass
class Verification:
    passed: bool
    issues: list


def _successful_tool_results(trace: RunTrace, tool_name: str) -> list:
    return [
        event["detail"] for event in trace.events
        if event["event"] == "TOOL_RESULT"
        and event["detail"]["name"] == tool_name
        and event["detail"]["status"] == "SUCCESS"
    ]


def _extract_section(answer: str, heading: str) -> str:
    """'## <heading>'부터 다음 '##' 직전까지의 본문을 추출한다. 없으면 빈 문자열."""
    match = re.search(rf"##\s*{re.escape(heading)}(.*?)(?=\n##|\Z)", answer, re.DOTALL)
    return match.group(1).strip() if match else ""


def _split_sentences(text: str) -> list:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?다요])\s+", text) if len(sentence.strip()) >= 15]


def verify_run(trace: RunTrace) -> Verification:
    issues = []

    searched = _successful_tool_results(trace, "search_paper")
    read = _successful_tool_results(trace, "read_paper")
    saved = _successful_tool_results(trace, "save_summary")

    # (1) Tool을 실제로 썼는지 — 답변 문구가 아니라 성공한 Tool Trace로 확인한다.
    if not searched:
        issues.append("search_paper Tool을 사용해 관련 논문을 검색하세요.")
    if not read:
        issues.append("read_paper Tool을 사용해 인용할 논문의 본문을 확인하세요.")
    if not saved:
        issues.append("save_summary Tool을 사용해 작성 결과를 파일로 저장하세요.")

    # (2) 국어학 연구용 구조화 형식 검증
    if "## 1. 요약" not in trace.answer:
        issues.append("답변에 '## 1. 요약' 섹션을 포함하고 서론/본론/결론 구조로 작성하세요.")
    if "## 2. 내 연구 활용 포인트" not in trace.answer:
        issues.append("답변에 '## 2. 내 연구 활용 포인트' 섹션을 포함해 인용할 문장을 발췌하세요.")
    if "## 3. 참고문헌" not in trace.answer:
        issues.append("답변에 '## 3. 참고문헌' 섹션을 포함하세요.")

    # (3) 논문 ID Hallucination(지어냄) 검증 — 실제 search/read 결과에 없는 ID를 썼는지 확인한다.
    cited_ids = set(PAPER_ID_PATTERN.findall(trace.answer))
    verified_ids = set()
    read_bodies = []
    for detail in [*searched, *read]:
        try:
            payload = json.loads(detail["content"])
        except (TypeError, ValueError):
            continue
        candidates = payload.get("results", [payload]) if isinstance(payload, dict) else []
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("paper_id"):
                verified_ids.add(candidate["paper_id"])
                if candidate.get("body"):
                    read_bodies.append(candidate["body"])

    fabricated = cited_ids - verified_ids
    if fabricated:
        issues.append("다음 논문 ID는 실제 검색/조회 결과에 존재하지 않습니다: " + ", ".join(sorted(fabricated)))
    elif not cited_ids:
        issues.append("참고문헌에 정확한 논문 ID(예: KCI_2023_001)를 명시하세요.")

    # (4) "그대로 발췌" 검증 — 2번 섹션에 읽은 논문 본문의 문장이 실제로 그대로 들어있는지 대조한다.
    quote_section = _extract_section(trace.answer, "2. 내 연구 활용 포인트")
    if read_bodies and quote_section:
        candidate_sentences = [sentence for body in read_bodies for sentence in _split_sentences(body)]
        if candidate_sentences and not any(sentence in quote_section for sentence in candidate_sentences):
            issues.append(
                "'## 2. 내 연구 활용 포인트'에 논문 원문에서 그대로 발췌한 문장이 보이지 않습니다. "
                "요약하지 말고 원문 문장을 그대로 인용하세요."
            )

    return Verification(passed=not issues, issues=issues)


def make_feedback(issues: list) -> str:
    return "다음 지적사항을 수정하여 다시 작성하세요:\n" + "\n".join(f"- {issue}" for issue in issues)


def run_with_verification(task: str, *, max_iterations: int = 3, max_steps: int = 12, max_retries: int = 1):
    feedback = ""
    previous_answer = ""
    trace = verification = None

    for iteration in range(1, max_iterations + 1):
        current_task = task if not feedback else (
            f"{task}\n\n이전 시도 결과:\n{previous_answer}\n\n수정 피드백:\n{feedback}"
        )
        print(f"\n[LOOP {iteration}]")
        trace = run_once(current_task, max_steps=max_steps, max_retries=max_retries)

        if trace.status != "COMPLETED":
            print("[LOOP STOPPED]", trace.status, trace.error)
            return trace, None

        verification = verify_run(trace)
        print("[VERIFICATION]", "PASS" if verification.passed else f"FAIL: {verification.issues}")

        if verification.passed:
            return trace, verification
        if iteration == max_iterations:
            print("[LOOP LIMIT] 최대 반복 한도에 도달했습니다.")
            return trace, verification

        feedback = make_feedback(verification.issues)
        previous_answer = trace.answer

    return trace, verification


# ------------------------------------------------------------------
# 4. 실행
# ------------------------------------------------------------------
def main():
    task = (
        "'국어 통사론' 또는 '연결어미'에 관한 국어학 논문을 papers/ 폴더에서 탐색해서 "
        "서론-본론-결론 구조로 핵심 내용을 요약하고, 내 연구에 활용할 포인트 문장을 원문 그대로 발췌한 뒤, "
        "한국어 표준 참고문헌 목록(논문 ID 포함)을 생성하여 paper_summary.md로 저장해줘."
    )
    trace, verification = run_with_verification(task)

    print("\n=== 최종 답변 ===")
    print(trace.answer)
    print("\n=== 검증 결과 ===", "PASS" if verification and verification.passed else "FAIL")
    if verification and not verification.passed:
        print("남은 문제점:", verification.issues)


if __name__ == "__main__":
    main()
    