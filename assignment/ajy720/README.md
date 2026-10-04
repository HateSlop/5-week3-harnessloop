# 최신 AI 소식 조사 Agent

## 목적과 기능

최근 AI 소식을 직접 조회하고, 기사 발행일과 원문 URL을 확인한 뒤 한국어로 간단히 요약합니다. 출처를 찾지 못했거나 기사 본문을 읽지 못한 경우에는 확인 범위를 표시합니다. 사용자가 주제, 조회 기간, 최대 기사 수를 지정할 수 있습니다.

## Tools

| Tool | 역할 |
| --- | --- |
| `recent_news(source)` | OpenAI·Google AI·TechCrunch AI RSS 피드에서 최근 기사 목록을 가져옵니다. |
| `search_news(query)` | 세 피드의 최근 기사 제목·설명에서 키워드를 찾습니다. |
| `read_article(url)` | 앞서 찾은 기사만 열어 본문을 추출합니다. 본문 접근이 실패하면 RSS 설명을 반환하고 `feed_excerpt`로 표시합니다. |

세 Tool 모두 이 폴더의 `agent.py`에 직접 구현했습니다. MCP는 선택사항이므로 사용하지 않았습니다. 모델은 요청에 따라 필요한 Tool을 선택하며 매번 세 Tool을 모두 호출할 필요는 없습니다.

## Agent Flow

```text
사용자 주제·기간 입력
  → LangChain create_agent
  → 모델이 Tool 선택·호출
  → Tool 결과가 모델에 Observation으로 전달
  → 모델이 추가 조사 또는 최종 JSON 답변 결정
  → 바깥쪽 검증 Loop
```

Tool 호출 순서는 파이썬 코드에 고정하지 않았습니다. `create_agent`가 모델의 판단에 따라 Tool 호출과 결과 전달을 반복합니다.

## Harness

- **실행 예산:** 전체 Tool 호출을 최대 16회, LangChain 그래프의 재귀 한도를 18단계로 제한합니다.
- **시간 제한:** 모델 응답 40초, HTTP 요청당 12초로 제한합니다.
- **오류 처리:** Tool 오류는 Agent가 읽을 수 있는 오류 결과로 반환합니다. 기사 본문이 막혀 있고 RSS 설명이 있으면 그 설명만 사용합니다.
- **출처 제한:** 세 RSS 출처의 HTTPS URL만 취급하며, `read_article`은 앞서 피드에서 발견한 URL만 허용합니다.
- **Trace:** Tool 이름, 호출 번호, 성공·오류·본문 대체 여부와 검증 결과를 출력합니다.

## Loop Engineering

Agent의 최종 구조화 출력을 검사합니다. 기사 URL이 실제로 조회한 피드에 있고, 제목·발행일·출처가 일치하며, `read_article`을 거쳤고, 본문 확인 범위(`article` 또는 `feed_excerpt`)가 맞는지 확인합니다. 본문은 ID가 붙은 조각으로 전달하며, 각 요약에는 실제 해당 기사에 있는 `evidence_id`도 요구합니다. 실패하면 문제를 피드백으로 주어 Agent를 **최대 2회** 실행합니다. 그래도 실패하면 `FAIL`을 출력하고 종료 코드 1을 반환합니다. 검증은 근거 조각의 존재를 확인하며, 요약 문장의 모든 해석까지 자동 보증하지는 않습니다.

## 실행 방법

저장소 루트에서 Python 3.12 환경과 `requirements.txt`의 패키지를 준비합니다. 저장소 루트 `.env`에 `OPENAI_API_KEY`를 설정하고, 필요하면 `OPENAI_MODEL`을 지정합니다. 비밀 키는 과제 코드나 Git에 넣지 않습니다. 인터넷 연결이 필요합니다.

```bash
python -m pip install -r requirements.txt
python assignment/ajy720/agent.py --topic "AI 주요 소식" --days 7 --count 3
```

`--days`는 1~30, `--count`는 1~5입니다. 날짜는 한국 시간(KST) 발행일로 표시합니다. 이 프로그램은 읽기 전용이며 기사 저장이나 게시 기능은 없습니다.

## 실행 예시

2026-10-04에 `--days 7 --count 3`으로 실제 실행했을 때의 축약 로그입니다. 아래 제목은 실행 당시 피드 결과이며, 이후에는 달라질 수 있습니다.

```text
[기준 시각] 2026-10-04T14:43+09:00
[검증] PASS
[실행 기록] recent_news × 3 → read_article × 3 → verification: pass

1. A model guide for the GPT-6 family (openai, 2026-10-03)
출처: https://openai.com/index/practical-guide-building-gpt-6 | 확인 범위: article

2. The latest AI news we announced in September 2026 (google, 2026-10-03)
출처: https://blog.google/innovation-and-ai/technology/ai/google-ai-updates-september-2026/ | 확인 범위: article

3. OpenAI safety employee resigns, claiming the company’s ‘culture is broken’ (techcrunch, 2026-10-04)
출처: https://techcrunch.com/2026/10/03/openai-safety-employee-resigns-claiming-the-companys-culture-is-broken/ | 확인 범위: article
```

### 확인 범위와 한계

- `article`: 원문 HTML에서 본문을 추출했습니다.
- `feed_excerpt`: 원문을 읽지 못해 RSS 제목과 설명만 확인했습니다. 이때 Agent는 설명에 없는 세부 내용을 추정하지 않도록 지시받습니다.
- 조회 범위는 세 출처의 공개 RSS 피드와 설정한 기간 안의 기사입니다. 세상 모든 AI 뉴스를 포괄하지 않습니다.
- 자동 검증은 출처·날짜·Tool 사용 기록과 근거 조각 ID의 존재를 확인합니다. 요약의 모든 해석을 독립적으로 검증하는 것은 아닙니다.
