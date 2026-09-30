
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI

ASSIGNMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ASSIGNMENT_ROOT.parents[1]
load_dotenv(PROJECT_ROOT / ".env")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_TIMEOUT = 30.0
MAX_RUNS = 3  # 요약 작성 재시도 상한 (Tool 호출 횟수가 아니라 시도 횟수)
MAX_SECONDS = 180.0  # 한 번의 run_agent() 실행 전체에 허용하는 시간 예산
RECURSION_LIMIT = 20  # create_agent 내부 Tool Loop의 최대 Step 수

CAFE_URL = "https://pubg.com/ko/news/11057?category=patch_notes"
LOCAL_FALLBACK_FILENAME = "sample_pubg_patch_note.txt"

api_key = os.getenv("OPENAI_API_KEY", "")
if not api_key or api_key == "your-openai-api-key":
    raise RuntimeError("실행 전에 루트 .env에 OPENAI_API_KEY를 설정하세요.")


# ---------------------------------------------------------------------------
# 0. 로컬 Tool 함수 구현
# ---------------------------------------------------------------------------
# 세 Tool은 각각 원격 수집(fetch_patch_note_url), 로컬 사본 읽기(read_local_patchnote),
# 결과 저장(save_summary)을 담당한다. 패치노트가 로그인 세션이 있어야 본문이 렌더링되는
# 페이지에 게시된 경우를 대비해, fetch가 막히면 로컬 사본으로 우회하는 경로를 Agent가
# 스스로 선택하도록 상태(status)를 함께 반환한다.

PATCHNOTES_DIR = ASSIGNMENT_ROOT / "patchnotes"
OUTPUT_DIR = ASSIGNMENT_ROOT / "output"

DEFAULT_USER_AGENT = (
    "PatchNoteSummaryAgent/1.0 (educational assignment; contact: wellsohn0527@gmail.com)"
)
MIN_READABLE_LENGTH = 300  # 이보다 짧게 추출되면 JS 렌더링/로그인 벽으로 간주한다.

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _strip_html(html: str) -> str:
    without_script = _SCRIPT_STYLE_RE.sub(" ", html)
    without_tags = _TAG_RE.sub("\n", without_script)
    collapsed = _WHITESPACE_RE.sub(" ", without_tags)
    return _BLANK_LINES_RE.sub("\n\n", collapsed).strip()


def _resolve_in(base_dir: Path, filename: str) -> Path:
    # 모델이 실수로 폴더 이름(예: "patchnotes/x.txt")을 filename에 포함해도 정상 동작하도록
    # base_dir과 같은 이름의 선행 세그먼트는 제거한다.
    clean_name = filename
    prefix = f"{base_dir.name}/"
    if clean_name.startswith(prefix):
        clean_name = clean_name[len(prefix):]
    resolved = (base_dir / clean_name).resolve()
    if not resolved.is_relative_to(base_dir):
        raise ValueError("지정한 폴더 밖의 파일에는 접근할 수 없습니다.")
    return resolved


def fetch_patch_note_url(url: str) -> str:
    """URL의 본문 텍스트를 최대한 추출한다. 로그인/JS 렌더링 페이지는 status='blocked'를 반환한다."""
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    try:
        with urlopen(request, timeout=15.0) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, ValueError) as exc:
        return json.dumps(
            {"status": "error", "url": url, "message": f"요청 실패: {exc}"},
            ensure_ascii=False,
        )

    text = _strip_html(html)
    if len(text) < MIN_READABLE_LENGTH:
        return json.dumps(
            {
                "status": "blocked",
                "url": url,
                "message": (
                    "이 페이지는 로그인 또는 JavaScript 렌더링이 필요해 본문을 "
                    "직접 가져올 수 없습니다. read_local_patchnote Tool로 patchnotes/ "
                    "폴더에 저장된 사본을 사용하세요."
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps({"status": "ok", "url": url, "text": text[:8000]}, ensure_ascii=False)


def read_local_patchnote(filename: str) -> str:
    """patchnotes/ 폴더에 저장된 패치노트 사본(UTF-8 텍스트)을 읽는다."""
    path = _resolve_in(PATCHNOTES_DIR, filename)
    if not path.is_file():
        raise FileNotFoundError(f"patchnotes/{filename} 파일을 찾을 수 없습니다.")
    return path.read_text(encoding="utf-8")


def save_summary(filename: str, content: str) -> str:
    """완성된 요약을 output/ 폴더에 UTF-8 텍스트로 저장하고 저장 경로를 반환한다."""
    path = _resolve_in(OUTPUT_DIR, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"저장 완료: {path.relative_to(ASSIGNMENT_ROOT).as_posix()}"


# ---------------------------------------------------------------------------
# 1. LangChain Tool 정의 (create_agent에 그대로 전달)
# ---------------------------------------------------------------------------

@tool("fetch_patch_note_url")
def fetch_patch_note_url_tool(url: str) -> str:
    """Fetch a patch-note page's readable text. Returns status='blocked' for login/JS-only pages."""
    return fetch_patch_note_url(url)


@tool("read_local_patchnote")
def read_local_patchnote_tool(filename: str) -> str:
    """Read a locally saved patch-note copy from assignment/sohnmj/patchnotes/."""
    return read_local_patchnote(filename)


@tool("save_summary")
def save_summary_tool(filename: str, content: str) -> str:
    """Save the finished summary text under assignment/sohnmj/output/."""
    return save_summary(filename, content)


TOOLS = [fetch_patch_note_url_tool, read_local_patchnote_tool, save_summary_tool]

INSTRUCTIONS = (
    "당신은 배틀그라운드(PUBG) 패치노트를 한국어로 요약하는 Agent입니다. "
    "먼저 fetch_patch_note_url로 원문 수집을 시도하고, status가 'blocked' 또는 "
    "'error'이면 read_local_patchnote로 로컬 사본을 사용하세요. "
    "원문에 없는 내용을 지어내지 말고, 카테고리에 해당 내용이 없으면 해당 섹션은 생략하세요. "
    "최종 요약을 작성한 뒤에는 save_summary Tool을 filename='patch_note_summary.md'로 호출해 "
    "저장하고(파일명 앞에 'output/'을 붙이지 마세요, Tool이 output/ 폴더에 자동으로 저장합니다), "
    "저장한 것과 같은 내용을 최종 답변으로도 출력하세요.\n\n"
    "최종 답변은 반드시 다음 형식을 따릅니다:\n"
    "# <패치 제목 또는 버전>\n"
    "- 원문 링크: <실제로 사용한 URL 또는 로컬 파일명>\n"
    "## <카테고리 1, 예: 밸런스 조정>\n"
    "- <항목>\n"
    "## <카테고리 2, 예: 버그 수정>\n"
    "- <항목>\n"
)

TASK = (
    f"다음 배틀그라운드 패치노트 페이지를 요약해줘: {CAFE_URL}\n"
    f"만약 접근이 막히면 read_local_patchnote(filename='{LOCAL_FALLBACK_FILENAME}')로 "
    f"로컬 사본을 사용해줘 (filename 앞에 'patchnotes/'를 붙이지 마세요)."
)

model = ChatOpenAI(model=MODEL, timeout=MODEL_TIMEOUT, max_retries=0)
agent = create_agent(model=model, tools=TOOLS, system_prompt=INSTRUCTIONS)
evaluator_client = OpenAI(timeout=MODEL_TIMEOUT, max_retries=0)


# ---------------------------------------------------------------------------
# 2. Loop Engineering: 규칙 검증 + Tool 사용 검증 + LLM Evaluator
# ---------------------------------------------------------------------------

REQUIRED_LABELS = ["- 원문 링크:"]


def verify_format(answer: str) -> list[str]:
    """제목·원문 링크·섹션·불릿 형식을 검사해 문제 목록을 반환한다."""
    issues: list[str] = []
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("# "):
        issues.append("첫 줄은 '# <패치 제목>' 형식의 제목이어야 합니다.")
    for label in REQUIRED_LABELS:
        if label not in answer:
            issues.append(f"필수 항목이 빠졌습니다: {label}")
    if "## " not in answer:
        issues.append("최소 한 개 이상의 '## <카테고리>' 섹션이 있어야 합니다.")
    bullet_count = sum(1 for line in lines if line.startswith("- ") and "원문 링크" not in line)
    if bullet_count == 0:
        issues.append("카테고리 아래에 '- ' 불릿 항목이 최소 1개 이상 있어야 합니다.")
    if "{" in answer or "}" in answer or (chr(96) * 3) in answer:
        issues.append("중괄호나 코드 블록 없이 마크다운 텍스트로만 작성하세요.")
    return issues


def successful_tool_calls(messages: list[AnyMessage]) -> list[dict]:
    completed_ids = {
        item.tool_call_id for item in messages
        if isinstance(item, ToolMessage) and getattr(item, "status", "success") != "error"
    }
    return [
        call
        for item in messages if isinstance(item, AIMessage)
        for call in item.tool_calls if call["id"] in completed_ids
    ]


def verify_tool_usage(messages: list[AnyMessage]) -> list[str]:
    """원문 수집(fetch 또는 read_local) Tool과 저장 Tool이 실제로 성공했는지 확인한다."""
    calls = successful_tool_calls(messages)
    called_names = {call["name"] for call in calls}
    issues: list[str] = []
    if "fetch_patch_note_url" not in called_names and "read_local_patchnote" not in called_names:
        issues.append("fetch_patch_note_url 또는 read_local_patchnote로 원문을 먼저 확인하세요.")
    if "save_summary" not in called_names:
        issues.append("save_summary로 최종 요약을 저장하세요.")
    return issues


def extract_source_text(messages: list[AnyMessage]) -> str:
    """검증된 원문 내용을 Evaluator에 넘기기 위해 Tool 결과에서 추출한다."""
    chunks: list[str] = []
    for item in messages:
        if not isinstance(item, ToolMessage):
            continue
        if item.name == "read_local_patchnote":
            chunks.append(str(item.content))
        elif item.name == "fetch_patch_note_url":
            try:
                payload = json.loads(str(item.content))
            except json.JSONDecodeError:
                continue
            if payload.get("status") == "ok":
                chunks.append(payload.get("text", ""))
    return "\n\n".join(chunks)


EVALUATOR_INSTRUCTIONS = (
    "당신은 패치노트 요약의 평가자입니다. 요약을 직접 다시 쓰지 마세요. "
    "제공된 원문과 비교하여 원문에 없는 수치나 항목을 지어내지 않았는지, "
    "카테고리 구분과 핵심 변경 사항이 원문과 일치하는지만 평가하세요. "
    "형식(제목, 링크, 섹션, 불릿)은 별도로 검증하므로 평가하지 마세요. "
    "명확한 문제가 있을 때만 passed=false와 수정 가능한 feedback을 반환하세요."
)
EVALUATOR_FORMAT = {
    "type": "json_schema",
    "name": "patch_note_evaluation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"passed": {"type": "boolean"}, "feedback": {"type": "string"}},
        "required": ["passed", "feedback"],
        "additionalProperties": False,
    },
}


def evaluate_with_llm(source_text: str, answer: str) -> tuple[bool, str]:
    if not source_text.strip():
        return True, ""
    response = evaluator_client.responses.create(
        model=MODEL,
        instructions=EVALUATOR_INSTRUCTIONS,
        input=f"원문:\n{source_text}\n\n평가할 요약:\n{answer}",
        text={"format": EVALUATOR_FORMAT},
    )
    result = json.loads(response.output_text)
    return result["passed"], result["feedback"].strip()


def build_feedback(issues: list[str]) -> str:
    return "다음 문제를 고쳐 요약을 다시 작성하고 다시 저장하세요:\n- " + "\n- ".join(issues)


# ---------------------------------------------------------------------------
# 3. Harness: budget, timeout, error handling, trace
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    passed: bool
    answer: str
    runs: int
    trace: list[dict] = field(default_factory=list)
    stop_reason: str = ""


def _log(trace: list[dict], event: str, **detail) -> None:
    entry = {"event": event, **detail}
    trace.append(entry)
    print(f"[{event}]", {k: v for k, v in detail.items() if k != "raw"})


def run_agent(task: str, *, max_runs: int = MAX_RUNS, max_seconds: float = MAX_SECONDS) -> RunResult:
    messages: list[AnyMessage] = [HumanMessage(content=task)]
    trace: list[dict] = []
    start_time = time.monotonic()

    for run_index in range(1, max_runs + 1):
        elapsed = time.monotonic() - start_time
        if elapsed > max_seconds:
            _log(trace, "STOP", reason="execution_budget_exceeded", elapsed=round(elapsed, 1))
            return RunResult(False, "", run_index - 1, trace, "execution_budget_exceeded")

        _log(trace, "RUN_START", run=run_index)
        try:
            result = agent.invoke(
                {"messages": messages},
                config={"recursion_limit": RECURSION_LIMIT},
            )
        except Exception as exc:  # 모델/Tool 호출 실패 시 즉시 중단하고 원인을 기록
            _log(trace, "ERROR", run=run_index, message=str(exc))
            return RunResult(False, "", run_index, trace, "model_or_tool_error")

        messages = result["messages"]
        for message in messages:
            if isinstance(message, AIMessage) and message.tool_calls:
                for call in message.tool_calls:
                    _log(trace, "TOOL_CALL", run=run_index, name=call["name"], args=call["args"])
            elif isinstance(message, ToolMessage):
                _log(trace, "TOOL_RESULT", run=run_index, name=message.name,
                     preview=str(message.content)[:160])

        answer = str(messages[-1].content)
        tool_issues = verify_tool_usage(messages)
        rule_issues = verify_format(answer)
        source_text = extract_source_text(messages)
        llm_passed, llm_feedback = evaluate_with_llm(source_text, answer)
        issues = [*tool_issues, *rule_issues]
        if not llm_passed:
            issues.append("LLM 평가: " + (llm_feedback or "원문과의 일치 여부를 다시 확인하세요."))

        _log(trace, "VERIFY", run=run_index, passed=not issues,
             tool_issues=len(tool_issues), rule_issues=len(rule_issues), llm_passed=llm_passed)

        if not issues:
            return RunResult(True, answer, run_index, trace, "verified")

        feedback = build_feedback(issues)
        _log(trace, "FEEDBACK", run=run_index, feedback=feedback[:400])
        messages.append(HumanMessage(content=feedback))

    _log(trace, "STOP", reason="loop_limit_reached", runs=max_runs)
    return RunResult(False, str(messages[-1].content), max_runs, trace, "loop_limit_reached")


def main() -> None:
    print("Python:", sys.version.split()[0], "| Model:", MODEL, "| Max Runs:", MAX_RUNS)
    result = run_agent(TASK)
    print("\n[FINAL STATUS]", "PASS" if result.passed else f"NOT PASSED ({result.stop_reason})",
          "| runs:", result.runs)
    print("[FINAL ANSWER]\n", result.answer)


if __name__ == "__main__":
    main()
