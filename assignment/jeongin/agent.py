
import itertools
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import APIError, APITimeoutError

# ==============================================================================
# 1. 환경 설정 및 베이스라인 경로 탐색
# ==============================================================================

# 프로젝트 루트 탐색 (requirements.txt와 tools 디렉토리가 있는 위치)
PROJECT_ROOT = next(
    (
        path
        for path in (Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parents[2])
        if (path / "requirements.txt").is_file() and (path / "tools").is_dir()
    ),
    None,
)
if PROJECT_ROOT is None:
    raise RuntimeError("저장소 루트를 찾을 수 없습니다.")

# .env 로드 (실습 2, 3 베이스라인 패턴)
load_dotenv(PROJECT_ROOT / ".env")

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_TIMEOUT_SECONDS = 30.0
MAX_STEPS_DEFAULT = 8
MAX_ITERATIONS_DEFAULT = 3

# 과제 작업 디렉토리
ASSIGNMENT_DIR = Path(__file__).resolve().parent

# ==============================================================================
# 2. 논문 코퍼스 데이터 출처 및 도구 (Tools) 구성
# ==============================================================================
# [데이터 출처]: 학회 세션 주제(Agent, Tool Use, Verbal RL)와 직결되는
# 대표적인 탑티어 학술 대회(ICLR, NeurIPS) 정식 게재 논문들의 원문 메타데이터 및
# 초록/방법론/기여점/한계점을 체계적으로 수록한 연구용 코퍼스입니다.

RESEARCH_PAPERS_DB = {
    "paper_001": {
        "title": "ReAct: Synergizing Reasoning and Acting in Language Models",
        "authors": "Shunyu Yao, Jeffrey Zhao, Dian Yu, Nan Du, Izhak Shafran, Karthik Narasimhan, Yuan Cao",
        "year": 2022,
        "venue": "ICLR 2023",
        "arxiv_id": "arXiv:2210.03629",
        "abstract": "언어 모델의 추론(Reasoning)과 행동(Acting)을 결합하여 지식 집약적 문제를 해결하는 상호작용 프레임워크.",
        "content": """
        ReAct는 대규모 언어 모델에서 추론(Chain-of-Thought)과 행동(Action 실행 및 환경 피드백 관찰)을 번갈아 수행하도록 설계된 선구적인 패러다임이다.
        기존의 순수 추론 모델은 내부 파라미터 지식에만 의존하여 정보 갱신이 불가능하고 환각(Hallucination)이 발생하는 취약점이 있었으며,
        순수 행동 모델은 장기적 계획 수립이나 복잡한 추론 과정 추적이 불가능했다.
        ReAct는 모델이 [Thought -> Action -> Observation]의 순환 루프를 거치며 외부 도구와 상호작용하도록 유도한다.
        실험 결과, HotpotQA와 Fever 벤치마크에서 환각 현상을 현저히 억제하고 해석 가능성을 극대화함을 증명했다.
        """,
        "limitations": "외부 도구의 실행 지연 시간과 토큰 소모량이 증가하며, 복잡한 다단계 환경에서 에러가 누적될 위험이 존재함.",
    },
    "paper_002": {
        "title": "Toolformer: Language Models Can Teach Themselves to Use Tools",
        "authors": "Timo Schick, Jane Dwivedi-Yu, Roberto Dessì, Roberta Raileanu, Maria Lomeli, Luke Zettlemoyer, Nicola Cancedda, Thomas Scialom",
        "year": 2023,
        "venue": "NeurIPS 2023",
        "arxiv_id": "arXiv:2302.04761",
        "abstract": "인간의 대규모 도구 라벨링 없이도 언어 모델이 스스로 어떤 API 도구를 언제 호출할지 학습하는 자기주도적 학습 기법.",
        "content": """
        Toolformer는 언어 모델이 계산기, Q&A 시스템, 위키백과 검색 엔진, 번역기 등의 외부 API를 스스로 사용하는 방법을 학습하도록 한 연구이다.
        대규모 텍스트 코퍼스에서 잠재적 API 호출 후보를 생성하고, 실제로 해당 API를 실행한 결과가 미래 토큰 예측의 Perplexity(복잡도)를
        실질적으로 낮추는 경우에만 유의미한 데이터로 필터링하여 사전 훈련 모델을 미세조정(Fine-tuning)한다.
        이를 통해 모델은 사람의 명시적인 지시나 도구 사용 어노테이션 없이도 적재적소에 도구를 호출할 수 있는 능력을 획득한다.
        """,
        "limitations": "여러 도구를 연쇄적으로 결합하여 복합적인 목표를 달성하는 상호작용 계획 능력은 다소 제한적임.",
    },
    "paper_003": {
        "title": "Reflexion: Language Agents with Verbal Reinforcement Learning",
        "authors": "Noah Shinn, Federico Cassano, Ashwin Gopinath, Karthik R. Narasimhan, Shunyu Yao",
        "year": 2023,
        "venue": "NeurIPS 2023",
        "arxiv_id": "arXiv:2303.11366",
        "abstract": "외부 평가 결과의 실패 원인을 언어적 자기반성 피드백으로 변환하여 모델의 단기 메모리에 반영하는 언어적 강화학습 프레임워크.",
        "content": """
        Reflexion은 에이전트가 과제를 수행하고 외부 평가자(Evaluator)로부터 받은 평가 결과를 자연어 피드백(Self-reflection)으로 변환하는 Outer Loop를 제안한다.
        기존 강화학습과 달리 모델 파라미터의 가중치를 업데이트하지 않고, 실패 원인과 개선 방향을 작업 메모리(Context)에 주입하여
        새로운 시도(Re-run)에서 실수를 교정하도록 유도한다.
        HumanEval 코딩 과제 및 ALFWorld 의사결정 환경에서 기존 ReAct 대비 월등한 성능 향상을 달성하며, Harness 및 Loop Engineering의
        실질적인 학술적 근거를 제공한다.
        """,
        "limitations": "평가자(Evaluator)의 피드백 품질에 에이전트의 교정 성능이 크게 의존하며, 잘못된 피드백이 주어질 경우 루프가 수렴하지 않을 수 있음.",
    },
}


@tool
def search_papers(query: str) -> str:
    """주어진 검색어(키워드)와 관련된 논문 목록(ID, 제목, 저자, 연도, 학술대회)을 검색합니다."""
    results = []
    query_lower = query.lower()
    keywords = [kw for kw in query_lower.split() if len(kw) > 1]

    for paper_id, paper in RESEARCH_PAPERS_DB.items():
        text_corpus = f"{paper['title']} {paper['abstract']} {paper['authors']} {paper['content']}".lower()
        if not keywords or any(kw in text_corpus for kw in keywords):
            results.append(
                f"[논문 ID: {paper_id}]\n"
                f"- 제목: {paper['title']}\n"
                f"- 저자: {paper['authors']}\n"
                f"- 발표: {paper['year']}년 ({paper['venue']}, {paper['arxiv_id']})\n"
                f"- 초록: {paper['abstract']}"
            )

    if not results:
        return f"검색어 '{query}'에 해당하는 논문을 찾지 못했습니다. 다른 키워드(예: agent, tool, reasoning, reflexion)로 다시 검색해보세요."

    return f"총 {len(results)}건의 논문이 검색되었습니다:\n\n" + "\n\n".join(results)


@tool
def read_paper_detail(paper_id: str) -> str:
    """논문 ID를 입력받아 해당 논문의 상세 연구 내용, 방법론, 한계점을 조회합니다."""
    paper = RESEARCH_PAPERS_DB.get(paper_id.strip())
    if paper is None:
        return f"논문 ID '{paper_id}'을(를) 찾을 수 없습니다. search_papers로 올바른 논문 ID를 먼저 확인하세요."

    return (
        f"=== 논문 상세 정보: {paper_id} ===\n"
        f"제목: {paper['title']}\n"
        f"저자: {paper['authors']}\n"
        f"연도/학회: {paper['year']}년 ({paper['venue']})\n"
        f"arXiv: {paper['arxiv_id']}\n\n"
        f"[핵심 연구 내용 및 방법론]:\n{paper['content'].strip()}\n\n"
        f"[연구의 한계점]:\n{paper['limitations'].strip()}"
    )


@tool
def save_research_report(paper_id: str, content: str) -> str:
    """작성된 기술 리포트 내용을 파일(report_{paper_id}.md)로 실제 저장합니다."""
    paper = RESEARCH_PAPERS_DB.get(paper_id.strip())
    if paper is None:
        return f"저장 실패: 유효하지 않은 paper_id '{paper_id}'입니다."

    filename = f"report_{paper_id}.md"
    file_path = ASSIGNMENT_DIR / filename

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        return f"리포트 파일 저장 성공: {file_path.name} (총 {len(content)}자 저장 완료)"
    except Exception as e:
        return f"파일 저장 중 오류 발생: {type(e).__name__} - {e}"


# 베이스라인 등록 Tool 목록 (정확히 3개)
TOOLS = [
    search_papers,
    read_paper_detail,
    save_research_report,
]

# ==============================================================================
# 3. Model 및 LangChain Agent 초기화
# ==============================================================================

SYSTEM_PROMPT = """
당신은 학술 논문 탐색 및 표준 기술 리포트 작성 전문 AI Agent입니다.
주어진 도구들을 자율적으로 활용하여 사용자의 요청을 완수하세요.

[도구 사용 원칙]
1. `search_papers`: 사용자의 요청에 맞는 논문을 검색하여 후보와 paper_id를 파악합니다.
2. `read_paper_detail`: 선택한 논문의 핵심 방법론과 세부 내용을 정독합니다.
3. `save_research_report`: 작성한 표준 기술 리포트를 실제 파일로 저장합니다.

[표준 기술 리포트 서식 요구사항]
리포트 본문은 반드시 다음 구조와 서식을 엄격히 준수하여 작성해야 합니다:
- 첫 줄: `# [리포트] {논문 제목}`
- 메타데이터 항목:
  - `- 저자: {저자명}`
  - `- 발표연도: {연도}`
  - `- 출처: {학술대회 및 arXiv 식별자}`
- 필수 본문 섹션 3개 (헤더 문구를 정확히 포함):
  `## 1. 핵심 요약`
  `## 2. 주요 연구 내용 및 의의`
  `## 3. 한계 및 시사점`
- 양식의 중괄호(`{`, `}`)나 불완전한 플레이스홀더를 최종 답변에 남기지 마세요.

[실행 순서]
- 논문 검색 -> 내용 정독 -> 표준 기술 리포트 작성 -> `save_research_report` 도구로 저장 -> 최종 답변 출력
- 최종 답변에는 리포트 전문을 온전히 포함해야 합니다.
"""


def build_agent():
    """LangChain create_agent를 사용하여 Agent 인스턴스를 빌드합니다."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "your-openai-api-key":
        raise RuntimeError(".env 파일에 유효한 OPENAI_API_KEY를 설정하세요.")

    llm = ChatOpenAI(
        model=MODEL_NAME,
        timeout=MODEL_TIMEOUT_SECONDS,
        max_retries=0,  # Harness가 타임아웃과 에러를 직접 관측하고 제어하도록 0 설정
    )

    return create_agent(
        model=llm,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )


# ==============================================================================
# 4. Harness Layer (Stop Condition, Budget/MAX_STEPS, Error Handling, Trace)
# ==============================================================================


@dataclass
class RunState:
    """단일 Agent Run의 상태, 실행 카운터, Trace를 캡슐화한 Runtime State."""

    steps: int = 0
    tool_calls: int = 0
    status: str = "RUNNING"  # "RUNNING", "COMPLETED", "STOPPED", "ERROR", "TIMEOUT"
    answer: str = ""
    error: str = ""
    trace: list[dict] = field(default_factory=list)


def record_event(state: RunState, event: str, detail: Any = "") -> None:
    """실행 이벤트를 Trace 목록에 추가합니다."""
    state.trace.append({"event": event, "detail": detail})


def stop_run(state: RunState, status: str, error: str) -> None:
    """Run을 중단하고 종료 사유를 기록합니다."""
    state.status = status
    state.error = error
    record_event(state, "RUN_STOPPED", error)


def budget_exhausted(state: RunState, max_steps: int) -> bool:
    """실행 예산(MAX_STEPS)에 도달했는지 확인합니다."""
    return state.steps >= max_steps


def is_timeout_error(error: BaseException) -> bool:
    """타임아웃 계열 예외인지 판정합니다."""
    return isinstance(error, (APITimeoutError, TimeoutError))


def show_trace(state: RunState) -> None:
    """기록된 Trace 이벤트를 순차적으로 출력합니다."""
    print(f"status={state.status}, steps={state.steps}, tool_calls={state.tool_calls}")
    for idx, item in enumerate(state.trace, 1):
        detail = item["detail"]
        if isinstance(detail, dict) and "output" in detail:
            out_str = str(detail["output"])
            if len(out_str) > 100:
                detail = {**detail, "output": out_str[:100] + "..."}
        print(f"  {idx:02d}. {item['event']}: {detail}")


def run_agent_with_harness(
    task: str,
    *,
    agent=None,
    max_steps: int = MAX_STEPS_DEFAULT,
) -> RunState:
    """LangChain Agent Loop를 외부에서 감싸서 실행을 통제하는 Outer Harness."""
    state = RunState()
    record_event(state, "AGENT_START", {"task_preview": task[:120]})
    print(f"\n[HARNESS START] Task: {task[:90]}...")

    if agent is None:
        try:
            agent = build_agent()
        except Exception as e:
            stop_run(state, "ERROR", f"Agent 초기화 실패: {type(e).__name__} - {e}")
            return state

    try:
        # LangChain create_agent 실행 (recursion_limit으로 Budget 한도 강제)
        recursion_limit = max_steps * 2 + 2
        invoke_result = agent.invoke(
            {"messages": [HumanMessage(content=task)]},
            config={"recursion_limit": recursion_limit},
        )

        messages = invoke_result.get("messages", [])

        # 메시지 히스토리를 파싱하여 모델 호출 및 도구 실행 Trace를 구성
        for msg in messages:
            if isinstance(msg, AIMessage):
                state.steps += 1
                record_event(state, "MODEL_STEP", f"step={state.steps}")

                if msg.tool_calls:
                    for call in msg.tool_calls:
                        state.tool_calls += 1
                        record_event(
                            state,
                            "TOOL_CALL",
                            {"name": call["name"], "args": call["args"]},
                        )
                        print(f"  [TOOL CALL] {call['name']}({call['args']})")
                else:
                    # 최종 답변 텍스트 수신
                    state.answer = str(msg.content)
                    record_event(state, "FINAL_ANSWER", f"length={len(state.answer)}")

            elif isinstance(msg, ToolMessage):
                status = "ERROR" if getattr(msg, "status", "") == "error" else "SUCCESS"
                record_event(
                    state,
                    "TOOL_RESULT",
                    {
                        "name": msg.name,
                        "output": str(msg.content),
                        "status": status,
                    },
                )
                print(f"  [TOOL RESULT] {msg.name}: {str(msg.content)[:120]}...")

        # Stop Condition 판정
        if budget_exhausted(state, max_steps):
            stop_run(state, "STOPPED", f"MAX_STEPS 도달 ({state.steps}/{max_steps})")
        elif state.answer:
            state.status = "COMPLETED"
            record_event(state, "RUN_END", "정상 종료")
        else:
            stop_run(state, "ERROR", "답변 미생성")

    except Exception as error:
        # Error Handling
        if is_timeout_error(error):
            stop_run(state, "TIMEOUT", type(error).__name__)
        elif "recursion_limit" in str(error).lower():
            stop_run(state, "STOPPED", "MAX_STEPS_EXCEEDED")
        elif isinstance(error, APIError):
            stop_run(state, "ERROR", f"APIError: {error.message}")
        else:
            stop_run(state, "ERROR", f"{type(error).__name__}: {str(error)}")

    print(f"[HARNESS END] Status: {state.status}, Steps: {state.steps}, ToolCalls: {state.tool_calls}")
    return state


# ==============================================================================
# 5. Verification Layer (품질 및 완전성 중심 검증)
# ==============================================================================


@dataclass
class Verification:
    """검증 통과 여부 및 구체적인 문제점 목록."""

    passed: bool
    issues: list[str] = field(default_factory=list)


EXPECTED_TITLE_PREFIX = "# [리포트]"
REQUIRED_METADATA_KEYS = ["저자", "발표연도"]
REQUIRED_SECTIONS = [
    "## 1. 핵심 요약",
    "## 2. 주요 연구 내용 및 의의",
    "## 3. 한계 및 시사점",
]


def verify_paper_info_completeness(answer: str) -> list[str]:
    """1. 논문 정보가 충분한가: 제목, 저자, 발표연도 등 메타데이터 및 내용 충실도 검사."""
    issues = []
    lines = [line.strip() for line in answer.splitlines() if line.strip()]

    # 제목 형식 검사
    if not any(line.startswith(EXPECTED_TITLE_PREFIX) for line in lines[:3]):
        issues.append(f"리포트 첫머리에 '{EXPECTED_TITLE_PREFIX} [논문 제목]' 형식의 제목이 누락되었습니다.")

    # 메타데이터 누락 여부 검사
    for key in REQUIRED_METADATA_KEYS:
        if key not in answer:
            issues.append(f"논문 필수 메타데이터 '{key}' 정보가 누락되었습니다.")

    # 충분한 분량의 분석이 포함되어 있는지 검사 (최소 200자 이상)
    if len(answer) < 200:
        issues.append("리포트 본문의 내용이 너무 짧습니다. 논문의 핵심 아이디어와 기여점을 충분히 서술하세요.")

    return issues


def verify_required_sections(answer: str) -> list[str]:
    """2. 기술 리포트의 필수 섹션이 존재하는가: 3개 핵심 섹션 및 템플릿 잔존 여부 검사."""
    issues = []

    # 필수 섹션 헤더 검사
    for section in REQUIRED_SECTIONS:
        if section not in answer:
            issues.append(f"기술 리포트 필수 섹션 헤더가 누락되었습니다: '{section}'")

    # 템플릿 잔존 기호 검사
    if "{" in answer or "}" in answer:
        issues.append("리포트 본문에 중괄호({}) 템플릿 플레이스홀더가 남아있습니다. 실제 내용으로 채우세요.")

    return issues


def verify_saved_file_integrity() -> list[str]:
    """3. 리포트 파일이 실제로 저장되었는가: 디스크 파일 존재 여부 및 파일 크기 검사."""
    issues = []
    saved_files = list(ASSIGNMENT_DIR.glob("report_*.md"))

    if not saved_files:
        issues.append("디스크에 실제 저장된 'report_{paper_id}.md' 파일이 존재하지 않습니다. save_research_report 도구를 호출하세요.")
    else:
        # 파일 내용 검증
        latest_file = max(saved_files, key=lambda f: f.stat().st_mtime)
        try:
            content = latest_file.read_text(encoding="utf-8")
            if len(content.strip()) < 100:
                issues.append(f"저장된 파일({latest_file.name})의 내용이 너무 짧거나 비어 있습니다.")
        except Exception as e:
            issues.append(f"저장된 파일({latest_file.name})을 읽는 중 오류가 발생했습니다: {e}")

    return issues


def verify_run(state: RunState) -> Verification:
    """최종 결과의 품질과 완전성을 종합 검증합니다."""
    if state.status != "COMPLETED":
        return Verification(passed=False, issues=[f"Agent 비정상 종료: status={state.status} (error={state.error})"])

    info_issues = verify_paper_info_completeness(state.answer)
    section_issues = verify_required_sections(state.answer)
    file_issues = verify_saved_file_integrity()

    all_issues = [*info_issues, *section_issues, *file_issues]
    return Verification(passed=len(all_issues) == 0, issues=all_issues)


# ==============================================================================
# 6. Outer Re-run Loop (Feedback, Loop Limit, State Update)
# ==============================================================================


@dataclass
class LoopResult:
    """Outer Loop 실행의 전체 히스토리, Trace, 최종 상태를 보관하는 데이터 클래스."""

    status: str = "RUNNING"  # "PASSED", "LIMIT_REACHED", "RUN_FAILED"
    runs: list[RunState] = field(default_factory=list)
    verifications: list[Verification] = field(default_factory=list)
    feedbacks: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)


def loop_limit_reached(iteration: int, max_iterations: int) -> bool:
    """Outer Loop의 최대 반복 횟수에 도달했는지 확인합니다."""
    return iteration >= max_iterations


def route_loop(verification: Verification, iteration: int, max_iterations: int) -> str:
    """검증 결과와 반복 횟수로 Outer Loop의 분기를 결정합니다."""
    if verification.passed:
        return "PASSED"
    if loop_limit_reached(iteration, max_iterations):
        return "LIMIT_REACHED"
    return "RETRY"


def make_feedback(issues: list[str]) -> str:
    """검증에서 발견된 문제점들을 다음 시도의 지시사항(Feedback)으로 생성합니다."""
    if not issues:
        return ""
    bullet_points = "\n".join(f"- {issue}" for issue in issues)
    return (
        "이전 실행 결과가 다음 검증 기준을 충족하지 못했습니다. 아래 문제점을 완벽히 수정하여 다시 수행하세요:\n"
        f"{bullet_points}"
    )


def run_with_feedback(
    task: str,
    *,
    agent=None,
    max_steps: int = MAX_STEPS_DEFAULT,
    max_iterations: int = MAX_ITERATIONS_DEFAULT,
) -> LoopResult:
    """검증 실패 시 피드백과 함께 새로운 Run을 실행하는 Outer Re-run Loop."""
    loop = LoopResult()
    feedback = ""
    previous_answer = ""

    print("\n" + "=" * 80)
    print(f"[OUTER LOOP START] 최대 반복 한도: {max_iterations}회")
    print("=" * 80)

    for iteration in itertools.count(1):
        print(f"\n>>> [LOOP ITERATION {iteration} / {max_iterations}]")

        # 피드백이 있는 경우 이전 실행의 실패 원인을 다음 실행 Context에 명시적으로 주입
        if feedback:
            current_task = (
                f"{task}\n\n"
                f"[이전 시도 작성본]:\n{previous_answer}\n\n"
                f"[검증 피드백 (수정 요구사항)]:\n{feedback}"
            )
        else:
            current_task = task

        loop.tasks.append(current_task)

        # 1. Harness를 통해 Agent Run 실행
        run_state = run_agent_with_harness(current_task, agent=agent, max_steps=max_steps)
        loop.runs.append(run_state)

        # 치명적 에러 발생 시 루프 중단
        if run_state.status not in ("COMPLETED", "STOPPED"):
            loop.status = "RUN_FAILED"
            record_event(run_state, "LOOP_END", f"RUN_FAILED: {run_state.status}")
            print(f"[LOOP ABORTED] Agent 비정상 종료: {run_state.status}")
            break

        # 2. 결과 검증 (Verification)
        verification = verify_run(run_state)
        loop.verifications.append(verification)
        record_event(run_state, "VERIFICATION", {"passed": verification.passed, "issues_count": len(verification.issues)})

        print("\n[VERIFICATION RESULT]")
        if verification.passed:
            print("  Status: PASS (모든 품질 및 완전성 기준 충족!)")
        else:
            print(f"  Status: FAIL (총 {len(verification.issues)}건의 문제 발견)")
            for issue in verification.issues:
                print(f"    - {issue}")

        # 3. 루프 분기 판단 (Conditional Stop / Loop Limit / Retry)
        decision = route_loop(verification, iteration, max_iterations)

        if decision == "PASSED":
            loop.status = "PASSED"
            record_event(run_state, "LOOP_END", "PASSED")
            print(f"\n[LOOP SUCCESS] {iteration}회차 실행에서 검증을 최종 통과했습니다.")
            break
        elif decision == "LIMIT_REACHED":
            loop.status = "LIMIT_REACHED"
            record_event(run_state, "LOOP_END", "LIMIT_REACHED")
            print(f"\n[LOOP STOPPED] 최대 재실행 횟수({max_iterations}회)에 도달하여 루프를 종료합니다.")
            break
        else:
            # RETRY: 피드백 생성 후 다음 Run 준비
            feedback = make_feedback(verification.issues)
            previous_answer = run_state.answer
            loop.feedbacks.append(feedback)
            record_event(run_state, "FEEDBACK", {"feedback_preview": feedback[:120]})
            record_event(run_state, "RERUN", f"next_iteration={iteration + 1}")
            print("\n[GENERATED FEEDBACK]")
            print(feedback)

    print("\n" + "=" * 80)
    print(f"[OUTER LOOP FINISHED] 최종 상태: {loop.status}, 총 Run 횟수: {len(loop.runs)}")
    print("=" * 80)
    return loop


# ==============================================================================
# 7. 메인 실행 엔트리포인트
# ==============================================================================

if __name__ == "__main__":
    task_input = (
        "AI Agent의 도구 사용 또는 행동-추론 결합에 관한 대표 논문을 하나 검색해서 본문을 읽고, "
        "정해진 서식에 맞춘 기술 리포트를 작성하여 파일로 저장해줘."
    )

    print("=== [과제] 학술 논문 탐색 및 검증 기반 기술 리포트 작성 Agent 실행 ===")
    result = run_with_feedback(task_input, max_iterations=3)

    # 최종 결과 출력
    if result.runs and result.runs[-1].answer:
        print("\n=================== [최종 산출물 리포트] ===================")
        print(result.runs[-1].answer)
        print("============================================================")

    # 최종 Run의 Trace 요약 출력
    if result.runs:
        print("\n=================== [마지막 Run Trace] ===================")
        show_trace(result.runs[-1])
        print("============================================================")
