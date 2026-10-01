import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI


# ============================================================
# 1. Environment
# ============================================================

ASSIGNMENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ASSIGNMENT_DIR.parents[1]
OUTPUT_DIR = ASSIGNMENT_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / ".env", override=True)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_TIMEOUT = 30.0

# Harness limits
MAX_STEPS = 30
MAX_RETRIES = 1

# Outer Loop limit
MAX_RUNS = 3

api_key = os.getenv("OPENAI_API_KEY", "")
if not api_key or api_key == "your-openai-api-key":
    raise RuntimeError(
        "루트 .env에 OPENAI_API_KEY를 설정한 뒤 다시 실행하세요."
    )


# ============================================================
# 2. Local Tools
# ============================================================

def _safe_repo_path(path: str) -> Path:
    """
    Resolve a path while preventing access outside this repository.
    """
    candidate = (PROJECT_ROOT / path).resolve()

    try:
        candidate.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("저장소 바깥의 파일에는 접근할 수 없습니다.") from exc

    return candidate


@tool("read_file")
def read_file(path: str) -> str:
    """
    Read a UTF-8 text or Markdown file inside the repository.
    Use this when the user asks you to analyze a local paper note,
    study material, or other text file.
    """
    target = _safe_repo_path(path)

    if not target.is_file():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    text = target.read_text(encoding="utf-8")

    # Prevent a single file from consuming the entire context.
    max_chars = 30000
    if len(text) > max_chars:
        return (
            text[:max_chars]
            + f"\n\n[TRUNCATED: original characters={len(text)}]"
        )

    return text


@tool("wikipedia_search")
def wikipedia_search(query: str) -> str:
    """
    Search Korean Wikipedia for background concepts.
    Use this only when additional background information is useful.
    """
    params = urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": 3,
            "namespace": 0,
            "format": "json",
        }
    )

    url = f"https://ko.wikipedia.org/w/api.php?{params}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "week3-semiconductor-study-agent/1.0"},
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))

    titles = data[1]
    descriptions = data[2]
    urls = data[3]

    results = []
    for title, description, article_url in zip(
        titles, descriptions, urls
    ):
        results.append(
            {
                "title": title,
                "description": description,
                "url": article_url,
            }
        )

    return json.dumps(
        {
            "query": query,
            "results": results,
        },
        ensure_ascii=False,
    )


@tool("save_markdown")
def save_markdown(filename: str, content: str) -> str:
    """
    Save completed study notes as a Markdown file.
    Files are saved only under assignment/eungyu/outputs/.
    """
    safe_name = Path(filename).name

    if not safe_name.endswith(".md"):
        safe_name += ".md"

    target = OUTPUT_DIR / safe_name
    target.write_text(content, encoding="utf-8")

    relative = target.relative_to(PROJECT_ROOT)
    return f"저장 완료: {relative}"


TOOLS = [
    read_file,
    wikipedia_search,
    save_markdown,
]


# ============================================================
# 3. Agent
# ============================================================

INSTRUCTIONS = """
당신은 Semiconductor Paper Study Agent입니다.

목표:
- 반도체 및 전자공학 관련 논문, 연구 노트, 기술 자료를 학습하는 사용자를 돕습니다.
- 등록된 Tool 중 필요한 것을 모델이 스스로 선택하여 사용합니다.

행동 원칙:
1. 사용자가 로컬 파일 분석을 요청하면 read_file을 사용해 실제 내용을 확인하세요.
2. 자료만으로 설명하기 어려운 배경 개념은 필요할 때 wikipedia_search로 확인하세요.
3. 사용자가 결과 저장을 요청하면 save_markdown을 사용하세요.
4. 읽지 않은 로컬 파일의 내용을 읽은 것처럼 말하지 마세요.
5. 로컬 자료에서 나온 내용과 외부 검색으로 보충한 내용을 구분하세요.
6. Tool을 미리 정해진 순서대로 무조건 호출하지 말고, 현재 상황에 필요한 Tool만 선택하세요.
7. save_markdown이 성공하지 않았다면 파일을 저장했다고 주장하지 마세요.

최종 학습 결과에는 반드시 다음 네 개의 Markdown heading을 정확히 포함하세요.

## 핵심 요약
## 핵심 개념
## 발표 포인트
## 예상 질문

'핵심 요약'에서는 자료 전체의 논리 흐름을 정리하세요.
'핵심 개념'에서는 중요한 전공 개념과 용어를 설명하세요.
'발표 포인트'에서는 발표에서 강조할 내용과 그림/결과를 설명할 관점을 정리하세요.
'예상 질문'에서는 자료를 읽은 뒤 제기할 수 있는 기술적 질문을 작성하세요.

근거가 없는 내용은 추측해서 확정적으로 쓰지 마세요.
"""


model = ChatOpenAI(
    model=MODEL,
    timeout=MODEL_TIMEOUT,
    max_retries=0,
)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=INSTRUCTIONS,
)


# ============================================================
# 4. Harness
# ============================================================

@dataclass
class RunResult:
    status: str
    answer: str = ""
    tool_calls: int = 0
    retries: int = 0
    trace: list[str] = field(default_factory=list)
    error: str | None = None


def _short(value: Any, limit: int = 180) -> str:
    text = str(value).replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))
            else:
                parts.append(str(item))

        return "\n".join(parts)

    return str(content)


def inspect_messages(messages: list[Any]) -> tuple[str, int, list[str]]:
    """
    Convert LangChain messages into a readable execution trace.
    """
    trace = []
    tool_calls = 0
    final_answer = ""

    for message in messages:
        if isinstance(message, AIMessage):
            calls = getattr(message, "tool_calls", []) or []

            if calls:
                for call in calls:
                    tool_calls += 1
                    trace.append(
                        "TOOL_CALL | "
                        f"{call.get('name')} | "
                        f"{_short(call.get('args', {}))}"
                    )
            else:
                text = _content_to_text(message.content).strip()

                if text:
                    final_answer = text
                    trace.append(
                        f"MODEL_ANSWER | {_short(text)}"
                    )

        elif isinstance(message, ToolMessage):
            trace.append(
                "TOOL_RESULT | "
                f"{getattr(message, 'name', 'unknown')} | "
                f"{_short(message.content)}"
            )

    return final_answer, tool_calls, trace


def run_agent(task: str) -> RunResult:
    """
    Run one Agent execution inside a Harness.

    Harness features:
    - Execution Budget: recursion_limit
    - Timeout: ChatOpenAI timeout
    - Error Handling: try/except
    - Retry: limited retry
    - Trace: Tool calls/results/final answer
    - Stop Condition: Agent reaches final answer or error/budget stops run
    """
    trace = [f"RUN_START | {_short(task)}"]

    for attempt in range(MAX_RETRIES + 1):
        try:
            trace.append(f"MODEL_RUN | attempt={attempt + 1}")

            result = agent.invoke(
                {
                    "messages": [
                        HumanMessage(content=task)
                    ]
                },
                config={
                    "recursion_limit": MAX_STEPS,
                },
            )

            answer, tool_calls, message_trace = inspect_messages(
                result["messages"]
            )

            trace.extend(message_trace)

            if not answer:
                raise RuntimeError(
                    "Agent가 최종 답변을 생성하지 않았습니다."
                )

            trace.append("RUN_END | COMPLETED")

            return RunResult(
                status="COMPLETED",
                answer=answer,
                tool_calls=tool_calls,
                retries=attempt,
                trace=trace,
            )

        except Exception as error:
            trace.append(
                f"ERROR | {type(error).__name__}: {error}"
            )

            if attempt < MAX_RETRIES:
                trace.append(
                    f"RETRY | next_attempt={attempt + 2}"
                )
                continue

            trace.append("RUN_END | ERROR")

            return RunResult(
                status="ERROR",
                retries=attempt,
                trace=trace,
                error=f"{type(error).__name__}: {error}",
            )

    return RunResult(
        status="ERROR",
        trace=trace,
        error="Unexpected harness termination",
    )


# ============================================================
# 5. Verification
# ============================================================

REQUIRED_SECTIONS = [
    "## 핵심 요약",
    "## 핵심 개념",
    "## 발표 포인트",
    "## 예상 질문",
]


@dataclass
class Verification:
    passed: bool
    issues: list[str]


def verify_answer(answer: str) -> Verification:
    issues = []

    for section in REQUIRED_SECTIONS:
        if section not in answer:
            issues.append(
                f"필수 섹션이 없습니다: {section}"
            )

    positions = [
        answer.find(section)
        for section in REQUIRED_SECTIONS
    ]

    if (
        all(position >= 0 for position in positions)
        and positions != sorted(positions)
    ):
        issues.append(
            "필수 섹션의 순서가 올바르지 않습니다."
        )

    if len(answer.strip()) < 400:
        issues.append(
            "학습 결과가 너무 짧습니다. 핵심 내용을 더 구체적으로 설명하세요."
        )

    return Verification(
        passed=len(issues) == 0,
        issues=issues,
    )


def make_feedback(issues: list[str]) -> str:
    return (
        "이전 결과가 검증을 통과하지 못했습니다.\n"
        "다음 문제를 수정하여 전체 답변을 다시 작성하세요.\n- "
        + "\n- ".join(issues)
    )


# ============================================================
# 6. Loop Engineering
# ============================================================

def run_with_feedback(task: str) -> tuple[str, list[RunResult]]:
    """
    Outer Loop:

    Task
      -> Agent + Harness
      -> Result
      -> Verification
      -> PASS: stop
      -> FAIL: feedback + re-run
    """
    runs: list[RunResult] = []

    previous_answer = ""
    feedback = ""

    for iteration in range(1, MAX_RUNS + 1):
        print(f"\n========== LOOP {iteration} ==========")

        current_task = task

        if feedback:
            current_task += (
                "\n\n이전 실행 결과:\n"
                + previous_answer
                + "\n\n검증 Feedback:\n"
                + feedback
            )

        result = run_agent(current_task)
        runs.append(result)

        for event in result.trace:
            print("[TRACE]", event)

        if result.status != "COMPLETED":
            print("[RUN STATUS]", result.status)

            if iteration >= MAX_RUNS:
                print("[LOOP END] Run limit reached.")
                return "LIMIT_REACHED", runs

            feedback = (
                "이전 실행이 정상 완료되지 않았습니다. "
                "요청을 다시 수행하세요."
            )
            previous_answer = result.answer
            continue

        verification = verify_answer(result.answer)

        if verification.passed:
            print("[VERIFICATION] PASS")
            print("[LOOP END]")
            return "PASSED", runs

        print(
            "[VERIFICATION] FAIL:",
            verification.issues,
        )

        previous_answer = result.answer
        feedback = make_feedback(
            verification.issues
        )

        print("[FEEDBACK]")
        print(feedback)

    print("[LOOP END] Maximum runs reached.")
    return "LIMIT_REACHED", runs


# ============================================================
# 7. CLI
# ============================================================

def main() -> None:
    print(
        f"Model: {MODEL} | "
        f"Max Steps: {MAX_STEPS} | "
        f"Max Runs: {MAX_RUNS}"
    )

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:]).strip()
    else:
        task = input("\nTask> ").strip()

    if not task:
        raise ValueError("Task를 입력하세요.")

    final_status, runs = run_with_feedback(task)

    print("\n======================================")
    print(
        f"FINAL STATUS: {final_status} | "
        f"runs={len(runs)}"
    )
    print("======================================")

    for index, run in enumerate(runs, start=1):
        print(
            f"RUN {index}: "
            f"status={run.status}, "
            f"tool_calls={run.tool_calls}, "
            f"retries={run.retries}"
        )

    if runs and runs[-1].answer:
        print("\nFINAL ANSWER\n")
        print(runs[-1].answer)


if __name__ == "__main__":
    main()