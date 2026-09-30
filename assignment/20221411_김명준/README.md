Adaptive Workout Planner Agent

1. 프로젝트 개요
이 프로젝트는 최근 3일간의 운동 기록과 사용자의 현재 상태를 분석하여 오늘 수행할 운동 계획을 생성하는 LLM 기반 운동 플래너 Agent입니다.
단순히 정해진 순서대로 함수를 실행하는 방식이 아니라, LangChain의 create_agent를 사용하여 LLM이 필요한 Tool을 직접 선택하고 실행 결과를 바탕으로 다음 판단을 수행하는 Agent Loop를 구성했습니다.
또한 Agent의 최종 응답을 별도의 Harness가 검증하고, 조건을 만족하지 못하면 실패 원인을 다시 Agent에게 전달하여 재실행하도록 구현했습니다.

2. 주요 기능
사용자 입력
프로그램 실행 시 다음 정보를 입력받습니다.
- 운동 목표
- 운동 경력
- 오늘 운동 가능한 시간
- 현재 주관적 피로도
- 지난밤 수면시간
- 현재 근육통 정도
- 오늘 운동에 대한 추가 요청
운동 종목, 중량, 반복 수, 세트 수, RPE는 사용자가 별도로 입력하지 않습니다.
과거 운동 데이터는 workout_log.json에서 자동으로 읽습니다.

3. workout_log.json 구조
운동 기록은 최근 3일을 기준으로 다음과 같이 저장합니다.
- day_ago = 1 : 1일 전
- day_ago = 2 : 2일 전
- day_ago = 3 : 3일 전
예시:
[
  {
    "day_ago": 1,
    "exercises": [
      {
        "exercise": "스미스 머신 벤치 프레스",
        "weight_kg": [100, 90, 80],
        "reps_per_set": [6, 8, 8],
        "total_sets": [2, 2, 2],
        "rpe": [9, 9, 9]
      }
    ]
  }
]
한 운동에서 여러 중량 구간을 수행한 경우 배열로 기록할 수 있습니다.
예:
- 100kg × 5회 × 2세트
- 90kg × 6회 × 3세트
{
  "exercise": "벤치 프레스",
  "weight_kg": [100, 90],
  "reps_per_set": [5, 6],
  "total_sets": [2, 3],
  "rpe": [9, 8.5]
}
단일 값과 배열 입력을 모두 지원합니다.

4. 운동 인식 및 중량 해석
운동 이름을 기준으로 장비와 중량의 의미를 구분하도록 구현했습니다.
예:
- 덤벨 운동 → weight_kg를 한 손당 중량으로 해석
- 원암 운동 → weight_kg를 한쪽 팔당 중량으로 해석
- 벤트오버 레터럴 레이즈 → 이름에 덤벨이 없어도 덤벨 운동으로 인식
- 머신/케이블 운동 → 장비에 표시된 중량으로 해석
- 풀업 → weight_kg가 체중, 추가중량, 보조중량 중 무엇인지 불명확하면 외부 훈련량 계산에서 제외
이를 통해 같은 숫자라도 운동 종류에 따라 의미가 달라지는 문제를 보완했습니다.

5. Tool 구성
Agent에는 다음 Tool을 제공합니다.
get_recent_workout_summary
최근 3일의 전체 운동 기록을 분석합니다.
주요 반환 정보:
- 총 세트 수
- 총 반복 수
- Volume Load
- 세트 수 가중 평균 RPE
- 날짜별 운동 상세 기록
get_day_workout_detail
특정 날짜의 운동 기록을 자세히 조회합니다.
입력:
1 = 1일 전
2 = 2일 전
3 = 3일 전
get_exercise_history
특정 운동의 최근 수행 기록을 검색합니다.
예:
- 실제 중량
- 반복 수
- 세트 수
- RPE
- 여러 중량 구간
Agent는 위 Tool을 고정된 순서로 호출하지 않고, 사용자 요청과 필요한 정보에 따라 LLM이 직접 선택합니다.

6. Agent Loop
전체 Agent 실행 흐름은 다음과 같습니다.
사용자 입력
    ↓
LLM Agent
    ↓
필요한 Tool 판단
    ↓
Tool 호출
    ↓
Tool 결과 반환
    ↓
LLM 재판단
    ↓
최종 운동 계획 생성
Tool 호출 순서는 코드에서 고정하지 않았습니다.
따라서 사용자 요청에 따라 한 개의 Tool만 사용할 수도 있고, 여러 Tool을 추가로 사용할 수도 있습니다.

7. Harness Loop
Agent가 생성한 결과는 바로 종료하지 않고 Harness가 다시 검증합니다.
Agent 실행
    ↓
결과 검증
    ↓
PASS ─────→ 종료
    ↓ FAIL
실패 원인 Feedback
    ↓
Agent 재실행
Harness에는 다음 기능이 포함되어 있습니다.
Stop Condition
검증 조건을 모두 만족한 최종 답변이 생성되면 Loop를 종료합니다.
Execution Budget
무한 반복을 방지하기 위해 실행 예산을 설정했습니다.
- 시도당 최대 Tool 호출 수
- 전체 최대 Tool 호출 수
- 최대 Harness 재시도 횟수
- Agent recursion limit
Error Handling
다음 오류를 구분하여 처리합니다.
- workout_log.json 파일 없음
- JSON 문법 오류
- 잘못된 운동 데이터
- 환경변수/API 설정 오류
- Harness 실행 실패
- 예상하지 못한 예외
Trace
실행 과정에서 Agent가 어떤 Tool을 호출했는지 출력합니다.
예:
=== AGENT TRACE (시도 1) ===
[Tool 호출 1] get_recent_workout_summary {}
[Tool 결과 반환] get_recent_workout_summary
이를 통해 Agent가 실제로 Tool을 선택하고 사용했는지 확인할 수 있습니다.

8. 결과 검증
Harness는 최종 응답에서 다음 항목을 확인합니다.
- 운동 기록 Tool 사용 여부
- 한국어 출력 여부
- RPE 기반 분석 포함 여부
- 오늘 운동 계획 포함 여부
- 사용 가능한 운동 시간 반영 여부
- 분석의 한계 또는 불확실성 표현 여부
- 과도한 생리학적 단정 여부
검증에 실패하면 실패 원인을 Feedback으로 만들어 Agent에게 다시 전달합니다.

9. 운동 분석 원칙
RPE, 세트 수, 반복 수, Volume Load 등의 데이터는 훈련 스트레스를 이해하기 위한 참고 지표로 사용합니다.
하지만 다음 내용을 현재 데이터만으로 확정적으로 판단하지 않도록 Agent Prompt에 제한을 두었습니다.
- CNS fatigue
- 과훈련 증후군
- 부상 위험
- 완전한 회복 여부
- 의학적 이상
또한 운동 기록에 없는 중량이나 수행 데이터를 임의로 생성하지 않도록 설계했습니다.

10. 실행 방법
일반 실행
python agent.py
실행 후 터미널에서 사용자 상태를 입력하면 됩니다.
Self Test
API 호출 없이 운동 기록 파싱과 중량 해석 기능을 검사할 수 있습니다.
python agent.py --self-test
정상 동작 시 다음과 같이 출력됩니다.
SELF-TEST PASS
- 덤벨 중량: 한 손당으로 인식, 양손 총 외부부하 반영
- 원암 중량: 한쪽당으로 인식, 좌우 수행 총 외부부하 반영
- 벤트오버 레터럴 레이즈: 카탈로그로 덤벨 운동 인식
- 풀업 60kg: 체중/보조/추가중량 미상으로 훈련량에서 제외
- 배열/쉼표 입력, broadcast, 정수 검증 통과

11. 환경 설정
프로젝트 루트의 .env 파일에 다음 값을 설정합니다.
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4.1-mini
API Key는 코드에 직접 작성하지 않고 환경변수로 관리합니다.

12. 프로젝트 파일
20221411_김명준/
├── agent.py
├── workout_log.json
└── README.md

13. 과제 요구사항 반영
본 프로젝트는 다음 요소를 포함합니다.
- 3개 이상의 Tool
- LangChain create_agent
- 실제 Agent Tool Loop
- LLM의 능동적인 Tool 선택
- Stop Condition
- Execution Budget
- Error Handling
- Trace
- 결과 Validation
- Validation 실패 시 Feedback 및 Retry
- 사용자 입력 기반 Agent 실행
- 외부 파일(workout_log.json) 기반 데이터 활용

14. 실행 예시
Adaptive Workout Planner Agent

운동 목표: 근비대
운동 경력: 중급
오늘 운동 가능한 시간: 120분
현재 주관적 피로도: 6/10
지난밤 수면시간: 7시간
현재 근육통: 6/10

추가 요청:
근비대에 강한 초점을 맞춰서 이두, 삼두 동시에 가능한 세트로 짜줘
Agent는 최근 3일 운동 기록을 Tool로 확인한 뒤 사용자의 현재 상태와 함께 해석하여 오늘의 운동 계획을 생성합니다.

15. 한계
- RPE, 피로도, 근육통은 주관적인 입력이므로 실제 생리적 상태와 차이가 있을 수 있습니다.
- 운동 이름이 카탈로그에 없으면 보수적인 fallback 규칙으로 해석합니다.
- 서로 다른 머신이나 케이블 장비의 표시 중량은 직접적인 비교가 어려울 수 있습니다.