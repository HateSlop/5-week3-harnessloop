# AI·테크 이슈 브리핑 Agent

## 목적과 기능

최근 48시간에 발행된 국내외 AI·테크 RSS 항목을 한국어로 최대 3개 브리핑합니다. RSS의 제목과 요약만 사용하고, 각 항목의 원문 링크와 한국시간 발행 시각을 함께 보여줍니다.

## Tools

| Tool | 역할 |
| --- | --- |
| `list_headlines` | TechCrunch AI, Ars Technica Technology Lab, KBS WORLD Science 중 한 피드의 최근 기사 제목 조회 |
| `search_headlines` | 세 피드의 제목·요약에서 특정 키워드 검색 |
| `read_feed_item` | 선택한 항목의 RSS 요약, 발행 시각, 원문 링크 확인 |

모델이 사용자 요청에 따라 필요한 Tool을 선택합니다. 세 Tool을 매번 모두 호출할 필요는 없습니다.

## Agent Flow

`create_agent`가 모델 → Tool 호출 → 관찰 → 모델 판단을 반복합니다. 모델은 확인한 기사 ID와 한국어 제목·요약을 JSON으로 답합니다. 링크와 날짜는 모델이 생성한 문장이 아니라 RSS에서 읽은 값을 출력합니다.

## Harness

- Tool 호출은 한 실행당 최대 8회, Agent의 graph recursion limit는 18입니다.
- RSS 요청에는 10초 timeout과 1MB 응답 크기 제한을 둡니다.
- 피드 오류를 Tool 결과로 전달하고, `--trace`로 Tool 호출과 검증 결과를 관찰할 수 있습니다.
- 실행 중 오류나 데이터 부족 시 근거 없는 브리핑을 출력하지 않습니다.

## Verification / Loop

답변이 JSON인지, 1~3개의 중복 없는 기사인지, 각 기사를 Tool로 읽었는지, 한국어 요약인지 확인합니다. 실패하면 이유를 피드백으로 주고 최대 한 번 다시 실행합니다. 최근 48시간 필터는 RSS를 읽을 때 적용합니다.

## 실행

`5-week3-harnessloop` 프로젝트 루트에서 Python 의존성을 설치하고 실행합니다. OpenAI 키는 프로젝트 루트의 `.env`에서 읽으며, 값을 코드나 출력에 넣지 않습니다.

```powershell
python -m pip install -r assignment/suyoung/requirements.txt
python assignment/suyoung/agent.py --trace
python assignment/suyoung/agent.py "최근 AI 에이전트 관련 이슈를 알려줘" --trace
```

브리핑 예시 형식:

```text
AI·테크 브리핑 | YYYY-MM-DD HH:MM KST | 최근 48시간

1. 한국어 제목
   RSS 내용을 바탕으로 한 한 문장 요약
   매체명 · YYYY-MM-DD HH:MM KST
   원문 링크
```

2026-10-01 실행에서 TechCrunch AI와 Ars Technica의 기사 3건을 브리핑했고, Trace는 다음과 같았습니다. 실제 기사와 결과는 실행 시각에 따라 달라집니다.

```text
list_headlines(techcrunch_ai)
list_headlines(ars_tech)
list_headlines(kbs_science)
read_feed_item(기사 ID)
read_feed_item(기사 ID)
read_feed_item(기사 ID)
run 1: PASS
```

RSS 출처: [TechCrunch AI](https://techcrunch.com/category/artificial-intelligence/feed/), [Ars Technica Technology Lab](https://feeds.arstechnica.com/arstechnica/technology-lab), [KBS WORLD Science](https://world.kbs.co.kr/service/about_rss.htm?lang=e).
