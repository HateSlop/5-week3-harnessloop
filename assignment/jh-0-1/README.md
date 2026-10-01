# 국내 시가총액 Top 5 장마감 시세 Agent

## Agent 목적

**KOSPI와 KOSDAQ을 합친 시가총액 상위 5개 종목**의 장마감 고가·저가·종가·거래량을 조회해 Markdown 리포트로 저장하는 Agent입니다. 정규장(15:30 KST)이 끝나고 데이터가 확정된 뒤면 오늘 기준으로, 그 전이거나 휴장일이면 **직전 거래일** 기준으로 작성합니다.

숫자를 다루는 작업이라 LLM이 값을 잘못 옮기거나 지어내면 리포트 전체가 무의미해집니다. 그래서 이 Agent의 핵심은 **리포트에 적힌 모든 숫자를 Tool이 실제로 조회한 값과 한 칸씩 대조하는 검증 Loop**입니다.

주요 기능:

- 실행 시각에 따른 기준 거래일 자동 선택 (오늘 데이터 확정 전·휴장일 → 직전 거래일)
- `--wait`를 주면 오늘 데이터 확정 시각까지 기다렸다가 오늘 기준으로 실행
- KOSPI·KOSDAQ 시가총액 비교로 국내 전체 상위 5개 선정
- 리포트 숫자·순위·기준일 검증 후 틀리면 피드백과 함께 재실행

## Tools

| Tool | 역할 |
| --- | --- |
| `check_market_status()` | 현재 KST 시각과 데이터 확정 여부를 확인해 기준 거래일(오늘 또는 직전 거래일) 결정 |
| `get_market_cap_ranking(market, top_n)` | KOSPI 또는 KOSDAQ의 시가총액 상위 종목과 시가총액(억원) 조회 |
| `get_daily_ohlcv(code)` | 종목의 기준 거래일 일봉(시가·고가·저가·종가·거래량) 조회 |
| `save_report(content)` | 리포트를 `outputs/top5_YYYYMMDD.md`로 저장 (경로·파일명 고정) |

데이터는 모두 네이버 금융에서 표준 라이브러리(`urllib`)로 가져오며, API 키나 추가 패키지가 필요 없습니다. 시가총액 순위는 JSON 엔드포인트를 먼저 쓰고, 실패하면 시가총액 순위 웹페이지를 파싱하는 대체 경로로 자동 전환합니다.

> FinanceDataReader·pykrx는 KRX가 데이터 조회에 로그인을 요구하도록 바뀐 뒤 종목 목록·시가총액 조회가 불안정해져서 사용하지 않았습니다.

## Agent Flow

```
User: "오늘 장마감 기준 시총 Top 5 시세 정리해줘"
  ↓
Model ── check_market_status ──▶ Observation(기준 거래일)
  ↓
Model ── get_market_cap_ranking("KOSPI") ──▶ Observation
Model ── get_market_cap_ranking("KOSDAQ") ──▶ Observation
  ↓  (모델이 두 시장의 시가총액을 비교해 상위 5개 선정)
Model ── get_daily_ohlcv(code) × 5 ──▶ Observation
  ↓
Model ── save_report ──▶ Observation(저장 경로)
  ↓
Final Answer (표)
```

Python 코드는 호출 순서를 정하지 않습니다. 어떤 시장을 조회할지, 어떤 종목의 시세를 가져올지, 오류가 나면 무엇을 다시 시도할지는 모델이 Observation을 보고 결정합니다.

## Harness

**기준일 결정 (데이터 확정 조건)** — 오늘 데이터는 평일 15:40 KST(마감 15:30 + 확정 대기 10분) 이후에만 확정된 것으로 봅니다. 그 전에는 장중 일봉이 있어도 고가·저가·종가가 계속 바뀌므로 쓰지 않습니다.

| 실행 시점 | 기준 거래일 |
| --- | --- |
| 평일 15:40 이후 | 오늘 |
| 평일 장중 (오늘 일봉은 있지만 미확정) | 직전 거래일 |
| 평일 장 시작 전 (오늘 일봉 없음) | 직전 거래일 |
| 주말·공휴일 | 직전 거래일 |

"직전 거래일"은 달력상 어제가 아니라 대표 종목(삼성전자)의 일봉 날짜 목록에서 고르므로, 월요일 오전에 실행하면 금요일, 연휴 다음 날이면 연휴 전 마지막 거래일이 됩니다. `--wait`를 주면 확정 시각까지 대기한 뒤 오늘 기준으로 실행합니다.

**Execution Budget** — 라운드마다 전체 Tool 호출 16회, 같은 Tool 8회, LLM 호출 20회, 실행 시간 180초로 제한합니다. 외부 요청 사이에는 최소 0.5초 간격을 둡니다. LLM 호출 수와 토큰은 `raise_error=True` 콜백으로 집계합니다.

**Soft / Hard Stop** — Tool 예산을 넘으면 먼저 `BUDGET_EXCEEDED` Observation으로 모델에게 마무리를 지시합니다(soft stop). 이 경고를 2번 넘게 무시하거나, 시간·LLM 호출 예산을 넘거나, `recursion_limit`( $2(N_{\text{tool}}+5)+1$ )에 걸리면 예외로 라운드를 강제 종료합니다(hard stop).

**Error Handling** — 네트워크 오류는 지수 백오프로 최대 2회 재시도합니다. 잘못된 종목코드나 파싱 실패 같은 오류는 `ERROR: ...` 문자열로 모델에게 돌려줘 스스로 수정하게 합니다. 시가총액 순위 조회는 데이터 소스 대체 경로(JSON → HTML)도 가지고 있습니다.

**Trace** — 모든 이벤트(`round_start`, `model_call`, `tool_ok`, `tool_retry`, `tool_error`, `source_fallback`, `budget_block`, `harness_stop`, `verify`, `feedback`, `loop_end`)를 `outputs/trace_<시각>.jsonl`에 기록하고 콘솔에도 요약 출력합니다.

## Loop

```
Task → Agent + Harness → Report
            ↓
      Verification (Tool 관찰값과 대조)
            ↓
   통과? ── Yes → End
     │
     No → 실패 항목 Feedback + State Update → Re-run (최대 3회)
```

Harness는 Tool이 실제로 반환한 값(기준일, 시장별 순위, 종목별 일봉)을 State에 저장해 두고, 리포트의 표를 파싱해 이 값과 비교합니다.

| 검사 | 기준 |
| --- | --- |
| `report_saved` | 리포트 파일이 실제로 저장되었는가 |
| `trading_date` | 리포트에 기준 거래일이 표기되었는가 |
| `row_count` | 표에 정확히 5개 종목이 있는가 |
| `both_markets` | KOSPI와 KOSDAQ 순위를 모두 조회했는가 |
| `top5_order` | 종목과 순서가 조회된 시가총액 순위와 일치하는가 |
| `numbers_grounded` | 고가·저가·종가·거래량이 `get_daily_ohlcv` 조회값과 정확히 일치하는가 |
| `fallback_noted` | 기준일이 오늘이 아닐 때 리포트에 "직전 거래일" 기준임을 명시했는가 |
| `sanity` | 저가 ≤ 종가 ≤ 고가인가 |

정답이 명확한 수치 작업이라 LLM Evaluator 대신 **규칙 기반 검증만** 사용했습니다. 이 경우 LLM 평가보다 정확하고 비용도 들지 않습니다.

**Feedback / State Update** — 다음 라운드 입력에 실패한 검사와 구체적인 차이(예: `000660 close: 리포트 204,500 ≠ 조회값 204,000`), 기준 거래일, 이미 조회한 시장·종목, 이전 리포트를 넣어 재실행합니다.

**Conditional Stop / Loop Limit** — 모두 통과하면 즉시 종료하고, 같은 실패가 그대로 반복되면 조기 종료합니다. 최대 3라운드까지만 실행합니다. 종료 코드는 통과 0, 검증 실패 1입니다.

## 실행 방법

### 환경 설정

저장소 루트에서 세션 사전 준비 환경을 그대로 사용합니다. 추가 패키지는 없습니다.

```bash
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 입력
```

모델 기본값은 `gpt-4.1-mini`이며 `.env`의 `OPENAI_MODEL`로 바꿀 수 있습니다.

### 에이전트 실행

```bash
# 바로 실행 (15:40 이전이면 직전 거래일 기준)
python assignment/bjay/agent.py

# 15:40까지 기다렸다가 오늘 기준으로 실행
python assignment/bjay/agent.py --wait

# Harness 동작 확인: 예산을 일부러 작게
python assignment/bjay/agent.py --max-tool-calls 4
```

결과는 `assignment/bjay/outputs/`에 리포트(`top5_YYYYMMDD.md`)와 Trace(`trace_*.jsonl`)로 저장됩니다.

## 실행 예시

```
  PASS report_saved      outputs/top5_20260930.md
  PASS trading_date      기준일 2026-09-30
  PASS row_count         표의 종목 행 5개 (정확히 5개 필요)
  PASS both_markets      KOSPI·KOSDAQ 모두 조회함
  PASS top5_order        순위 일치
  PASS numbers_grounded  모든 숫자가 조회값과 일치
  PASS fallback_noted    '직전 거래일' 기준임을 명시함
  PASS sanity            저가 ≤ 종가 ≤ 고가
  [trace r2] verify         passed=true, failed=[]
  [trace r2] loop_end       status=passed, rounds=2

✅ 검증 통과 — outputs/top5_20260930.md

# 국내 시가총액 Top 5 장마감 시세 (2026-09-30, 직전 거래일 기준)

| 순위 | 종목명 | 종목코드 | 시장 | 고가 | 저가 | 종가 | 거래량 |
|---|---|---|---|---|---|---|---|
| 1 | 삼성전자 | 005930 | KOSPI | 276,000 | 267,500 | 269,500 | 16,477,580 |
| 2 | SK하이닉스 | 000660 | KOSPI | 1,824,000 | 1,773,000 | 1,783,000 | 3,280,459 |
| 3 | 삼성전자우 | 005935 | KOSPI | 205,000 | 195,000 | 196,400 | 5,124,775 |
| 4 | SK스퀘어 | 402340 | KOSPI | 1,163,000 | 1,119,000 | 1,132,000 | 453,597 |
| 5 | 삼성전기 | 009150 | KOSPI | 1,579,000 | 1,508,000 | 1,514,000 | 505,098 |

## 메모
- 데이터 출처: 국내 증권시장 KOSPI 시가총액 및 각 종목별 기준 거래일 일봉 시세
- 기준일: 2026-09-30 (오늘(2026-10-01) 데이터 확정 전(10:29 < 15:40)으로 인하여 직전 거래일 기준)
- 시가총액 순위는 2026-10-01 조회 시점 기준입니다.
- 이번에 시가총액 상위 5개는 모두 KOSPI에 속함을 확인했습니다.
```

## 한계

- 네이버 금융의 비공식 엔드포인트를 사용하므로, 페이지 구조가 바뀌면 파싱이 깨질 수 있습니다.
- 시가총액 순위는 조회 시점 값입니다. 장중에 직전 거래일 기준으로 실행하면, 순위는 현재 주가 기준이고 시세는 직전 거래일 값이라 순위가 경계에서 드물게 달라질 수 있습니다.
- 시가총액은 보통주 종목 단위 기준이며, 우선주는 별도 종목으로 취급됩니다.
- 본 리포트는 학습용 데이터 정리이며 투자 판단의 근거가 아닙니다.