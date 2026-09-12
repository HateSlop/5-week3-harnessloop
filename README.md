# Harness Engineering과 Loop Engineering

## 1. 세션 소개

이 Repository는 LLM Agent의 기본 실행 구조부터 실행 제어, 결과 검증과 개선, Framework와 외부 Tool 연결까지 단계적으로 학습하는 3주차 세션 자료입니다.

세션은 다음 질문을 따라 진행합니다.

```text
Agent는 어떻게 Tool을 선택하고 행동을 반복하는가?
        ↓
Agent의 실행을 어떻게 제한하고 관찰하는가?
        ↓
결과가 충분하지 않다면 어떻게 검증하고 다시 실행하는가?
        ↓
이 구조를 LangChain과 LangGraph에서는 어떻게 표현하는가?
        ↓
MCP를 통해 외부 Tool을 어떻게 연결하는가?
```

각 실습은 앞에서 확인한 구조를 다음 단계에서 확장합니다. 실습 Notebook은 서로 import하지 않으며, 각각 새 커널에서 독립적으로 실행할 수 있습니다.

## 2. 시작하기 및 개인 과제

실습을 시작하기 전에 [3주차 사전 준비사항](https://app.notion.com/p/3d905fbaaaa380f9aa29c43a71c34465)을 따라 Python 3.12 가상환경, 패키지, API Key와 Jupyter 커널을 설정합니다. 

세션이 끝난 뒤에는 [3주차 과제 — 자신만의 Agent 구현](https://app.notion.com/p/3d905fbaaaa380499a58d222819aa22e)을 진행합니다. 과제는 LangChain을 기반으로 구현하며, 직접 작성한 Tool과 MCP 서버에서 불러온 Tool을 합해 3개 이상 구성합니다. MCP 사용은 선택사항입니다.

## 3. 각 실습 소개

### [실습 1 | Minimal Agent Loop](workshop/01_agent_loop.ipynb)

프레임워크 없이 Python과 OpenAI SDK로 최소 Agent Loop를 실행합니다. 모델이 `read_file`과 `wikipedia_search` 중 필요한 Tool을 선택하고, Tool Result를 받은 뒤 다음 행동을 결정하는 과정을 단계별로 관찰합니다.

대표 과제는 로컬 야구 기사를 읽고, 기사에 등장한 선수를 한국어 위키백과에서 검색하여 설명하는 것입니다.

### [실습 2 | Harness & Loop Engineering](workshop/02_harness_loop.ipynb)

전시 기획서를 바탕으로 안내문을 작성하는 Agent에 Harness와 외부 반복 구조를 추가합니다. 참가자는 Notebook의 TODO를 완성하면서 다음 요소가 실제 실행을 어떻게 제어하는지 확인합니다.

- Harness Engineering: Runtime State, Execution Budget, Stop Condition, Error Handling, Retry, Timeout, Trace
- Loop Engineering: Verification, Conditional Stop, Feedback, Re-run, Loop Limit, LLM Evaluator

### [실습 3 | LangChain, LangGraph와 MCP](workshop/03_langchain_langgraph.ipynb)

앞에서 직접 구현한 Agent Loop와 검증 Loop를 LangChain과 LangGraph로 구성합니다. `create_agent`, State, Node, Edge, Conditional Edge, ToolNode, Checkpointer와 Streaming을 실행하고 Graph 이미지를 통해 가능한 경로를 확인합니다.

마지막에는 LangChain `MCPAdapter`로 외부 Wikidata의 `search_items` Tool을 연결하여 Tool 발견, 호출, 결과 전달과 실제 Graph 실행 경로를 관찰합니다.

## 4. 저장소 구조

```text
5-week3-harnessloop/
├── .codex/
│   └── config.toml                    # Codex의 Notion MCP 연결 설정
├── AGENTS.md                          # Coding Agent를 위한 Outer Harness 지침
├── README.md                          # 프로젝트와 실습 안내
├── .env.example                       # 환경변수 예시
├── .gitignore                         # Git에서 제외할 로컬 파일 규칙
├── requirements.txt                   # Python 패키지 목록
├── tools/                             # 실습에서 공통으로 사용하는 Tool 함수
│   ├── __init__.py
│   ├── read_file.py
│   └── wikipedia_search.py
├── workshop/
│   ├── 01_agent_loop.ipynb            # 실습 1: Minimal Agent Loop
│   ├── 02_harness_loop.ipynb           # 실습 2: Harness & Loop Engineering
│   └── 03_langchain_langgraph.ipynb    # 실습 3: LangChain, LangGraph와 MCP
├── workspace/                         # Agent가 읽는 실습용 로컬 문서
│   ├── sample.txt
│   └── space_note.md
├── templates/
│   └── exhibition_notice.md            # 전시 안내문 출력 양식
├── reference/
│   └── exhibition_notice_sources.md    # 전시 안내문과 기획서의 참고 출처
└── assignment/                        # 개인 과제 폴더(직접 생성)
    └── [이름 또는 닉네임]/
        ├── README.md
        └── [에이전트 코드].py
```

실습 실행에는 사용되지 않지만, 프로젝트 자료를 설계하고 관리하는 과정에서 다음 파일을 사용했습니다.

- `.codex/config.toml`: Codex에서 프로젝트의 Notion 자료를 확인하기 위한 MCP 연결 설정입니다.
- `AGENTS.md`: Coding Agent가 프로젝트의 범위, 자료 구성과 검증 기준을 따르도록 정의한 지침입니다.
- `reference/exhibition_notice_sources.md`: 전시 기획서와 안내문 양식을 작성할 때 참고한 자료와 적용 범위를 기록합니다.
