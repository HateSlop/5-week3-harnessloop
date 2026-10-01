"""
국내 시가총액 Top 5 장마감 시세 Agent — 3주차 과제 (Agent Loop + Harness + Loop Engineering)

KOSPI·KOSDAQ 시가총액 순위를 비교해 국내 상위 5개 종목을 고르고, 기준 거래일의
고가·저가·종가·거래량을 조회해 Markdown 리포트로 저장한다.
기준 거래일은 실행 시각에 따라 자동으로 정해진다.
  - 평일 15:40 KST 이후: 오늘(당일 데이터 확정)
  - 그 이전(장 시작 전·장중) 또는 휴장일: 직전 거래일
바깥 Loop는 리포트의 숫자가 Tool이 실제로 가져온 값과 일치하는지 검증하고, 틀리면 재실행한다.

실행:
    python assignment/bjay/agent.py             # 15:40 이전이면 직전 거래일 기준으로 실행
    python assignment/bjay/agent.py --wait      # 15:40까지 기다렸다가 오늘 기준으로 실행
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError

load_dotenv()

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
KST = timezone(timedelta(hours=9))          # 한국은 서머타임이 없어 고정 오프셋으로 충분
MARKET_CLOSE = (15, 30)                     # 정규장 종료
DEFAULT_SETTLE_MIN = 10                     # 종료 후 데이터 확정 대기(분)
TOP_N = 5
UA = {"User-Agent": "Mozilla/5.0 (hateslop-week3-agent)"}
REPORT_COLUMNS = ["순위", "종목명", "종목코드", "시장", "고가", "저가", "종가", "거래량"]
FALLBACK_NOTE = "직전 거래일"               # 대체 기준일을 쓸 때 리포트에 반드시 들어가야 하는 표기
SETTLE_MIN = DEFAULT_SETTLE_MIN             # main()에서 --settle-min으로 덮어씀


def data_ready_at(now: datetime) -> datetime:
    """오늘 데이터가 확정되는 시각 = 정규장 종료 + 확정 대기 시간."""
    close_at = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return close_at + timedelta(minutes=SETTLE_MIN)


# =============================================================================
# 1. Harness — 실행 제한(Budget/Stop), 오류 처리(Retry), 관찰(Trace)
# =============================================================================
class HarnessStop(Exception):
    """Harness가 Agent 실행을 강제로 중단할 때 발생시키는 예외."""


@dataclass
class Budget:
    max_tool_calls: int = 16        # 라운드당 전체 Tool 호출 상한
    max_per_tool: int = 8           # 라운드당 같은 Tool 호출 상한
    max_model_calls: int = 20       # 라운드당 LLM 호출 상한
    max_seconds: float = 180.0      # 라운드당 실행 시간 상한
    tool_retries: int = 2           # 네트워크 오류 재시도 횟수
    request_interval: float = 0.5   # 외부 요청 간 최소 간격(초)
    max_budget_warnings: int = 2    # 예산 경고를 이 횟수 넘게 무시하면 강제 중단


def _short(v, n: int = 120) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


@dataclass
class Harness:
    budget: Budget
    trace_path: Path
    round_no: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    budget_warnings: int = 0
    per_tool: dict[str, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    last_request: float = 0.0
    # ---- 라운드를 넘어 유지되는 State (Tool이 실제로 관찰한 값만 기록) ----
    trading_date: str | None = None                                   # 'YYYY-MM-DD' 기준 거래일
    fallback_reason: str | None = None                                 # 오늘이 아닌 날을 쓰는 이유
    rankings: dict[str, list[dict]] = field(default_factory=dict)      # market -> 순위 목록
    ohlcv: dict[tuple[str, str], dict] = field(default_factory=dict)   # (code, date) -> 시세
    report_path: Path | None = None

    def log(self, event: str, **data) -> None:
        rec = {"ts": datetime.now(KST).isoformat(timespec="seconds"),
               "round": self.round_no, "event": event, **data}
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        detail = ", ".join(f"{k}={_short(v)}" for k, v in data.items())
        print(f"  [trace r{self.round_no}] {event:<14} {detail}")

    def start_round(self, n: int) -> None:
        self.round_no = n
        self.tool_calls = self.model_calls = self.budget_warnings = 0
        self.per_tool = {}
        self.started_at = time.monotonic()
        self.log("round_start", budget=self.budget.__dict__)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def check_time(self) -> None:
        if self.elapsed() > self.budget.max_seconds:
            raise HarnessStop(f"timeout: {self.elapsed():.0f}s > {self.budget.max_seconds:.0f}s")

    def on_model_call(self) -> None:
        self.check_time()
        self.model_calls += 1
        if self.model_calls > self.budget.max_model_calls:
            raise HarnessStop(f"model call budget exceeded ({self.budget.max_model_calls})")

    def run_tool(self, name: str, args: dict, fn: Callable[[], str]) -> str:
        self.check_time()
        self.tool_calls += 1
        self.per_tool[name] = self.per_tool.get(name, 0) + 1

        if self.tool_calls > self.budget.max_tool_calls or self.per_tool[name] > self.budget.max_per_tool:
            self.budget_warnings += 1
            self.log("budget_block", tool=name, args=args, warnings=self.budget_warnings)
            if self.budget_warnings > self.budget.max_budget_warnings:
                raise HarnessStop("agent ignored budget warnings")
            return ("BUDGET_EXCEEDED: 이번 라운드의 Tool 예산을 모두 사용했습니다. "
                    "추가 조회 없이 지금까지 얻은 값으로 save_report를 호출하고 마무리하세요.")

        for attempt in range(self.budget.tool_retries + 1):
            t0 = time.monotonic()
            try:
                out = fn()
                self.log("tool_ok", tool=name, args=args, attempt=attempt,
                         ms=int((time.monotonic() - t0) * 1000), output=out)
                return out
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                self.log("tool_retry", tool=name, attempt=attempt, error=repr(e))
                time.sleep(2 ** attempt)
            except Exception as e:
                self.log("tool_error", tool=name, args=args, error=repr(e))
                return f"ERROR: {type(e).__name__}: {e}. 인자를 확인하거나 다른 방법을 시도하세요."
        return "ERROR: 네트워크 오류가 반복되었습니다. 잠시 후 다시 시도해야 합니다."

    def http_get(self, url: str, encoding: str = "utf-8") -> str:
        wait = self.budget.request_interval - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode(encoding, errors="replace")


class ModelCallCounter(BaseCallbackHandler):
    """LLM 호출 횟수·토큰 기록. raise_error=True라서 예산 초과 시 실행이 멈춘다."""
    raise_error = True

    def __init__(self, harness: Harness):
        self.harness = harness

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.harness.on_model_call()
        self.harness.log("model_call", n=self.harness.model_calls)

    def on_llm_end(self, response, **kwargs):
        try:
            usage = response.generations[0][0].message.usage_metadata or {}
            self.harness.log("model_usage", input=usage.get("input_tokens"),
                             output=usage.get("output_tokens"))
        except Exception:
            pass


HARNESS: Harness | None = None


# =============================================================================
# 2. 데이터 소스 (네이버 금융, API 키 불필요)
# =============================================================================
def _to_int(s) -> int:
    return int(re.sub(r"[^\d\-]", "", str(s)) or 0)


def _fetch_daily(code: str, count: int) -> list[dict]:
    """일봉: date|open|high|low|close|volume 형식의 XML."""
    url = ("https://fchart.stock.naver.com/sise.nhn?"
           + urllib.parse.urlencode({"symbol": code, "timeframe": "day",
                                     "count": count, "requestType": 0}))
    xml = HARNESS.http_get(url, encoding="euc-kr")
    rows = []
    for data in re.findall(r'<item data="([^"]+)"', xml):
        d, o, h, l, c, v = data.split("|")
        rows.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "open": _to_int(o), "high": _to_int(h),
                     "low": _to_int(l), "close": _to_int(c), "volume": _to_int(v)})
    if not rows:
        raise ValueError(f"{code} 시세 데이터가 비어 있습니다 (종목코드 확인 필요)")
    return rows


def _fetch_ranking_json(market: str, n: int) -> list[dict]:
    url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize={n}"
    data = json.loads(HARNESS.http_get(url))
    out = []
    for s in data["stocks"]:
        out.append({"code": s["itemCode"], "name": s["stockName"],
                    "marcap_eok": _to_int(s["marketValue"])})
    return out


def _fetch_ranking_html(market: str, n: int) -> list[dict]:
    """JSON이 실패할 때 쓰는 대체 경로: 시가총액 순 정렬 페이지 파싱."""
    sosok = {"KOSPI": 0, "KOSDAQ": 1}[market]
    html = HARNESS.http_get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}",
                            encoding="euc-kr")
    out = []
    for chunk in html.split("<tr")[1:]:
        m = re.search(r'code=(\d{6})"\s+class="tltle">([^<]+)</a>', chunk)
        if not m:
            continue
        nums = [re.sub(r"<[^>]+>|\s", "", x) for x in
                re.findall(r'<td class="number"[^>]*>(.*?)</td>', chunk, re.S)]
        # 기본 컬럼 순서: 현재가, 전일비, 등락률, 액면가, 시가총액(억) ...
        marcap = _to_int(nums[4]) if len(nums) > 4 else 0
        out.append({"code": m.group(1), "name": m.group(2).strip(), "marcap_eok": marcap})
        if len(out) >= n:
            break
    if not out:
        raise ValueError("시가총액 페이지 파싱 실패")
    return out


# =============================================================================
# 3. Tools
# =============================================================================
@tool
def check_market_status() -> str:
    """현재 한국 시각과 데이터 확정 여부를 확인하고, 리포트의 기준 거래일을 정한다.
    오늘 데이터가 아직 확정되지 않았으면(장 시작 전·장중) 직전 거래일을 기준일로 정한다.
    작업을 시작할 때 가장 먼저 호출해야 한다."""
    def _run() -> str:
        now = datetime.now(KST)
        today = now.strftime("%Y-%m-%d")
        ready_at = data_ready_at(now)
        # 대표 종목의 최근 일봉 날짜들 = 최근 거래일 목록 (오름차순)
        dates = [r["date"] for r in _fetch_daily("005930", 5)]

        if dates[-1] == today and now < ready_at:
            # 오늘 일봉이 있지만 장중이라 고가·저가·종가가 아직 확정되지 않음
            target = dates[-2]
            reason = f"오늘({today}) 데이터 확정 전({now:%H:%M} < {ready_at:%H:%M})"
        elif dates[-1] != today:
            # 장 시작 전이거나 휴장일이라 오늘 일봉 자체가 없음
            target = dates[-1]
            reason = (f"오늘({today})은 휴장일" if now.weekday() >= 5 or now >= ready_at
                      else f"오늘({today}) 장 시작 전이거나 데이터 미반영")
        else:
            target, reason = today, None

        HARNESS.trading_date, HARNESS.fallback_reason = target, reason
        if reason is None:
            guide = "오늘 데이터가 확정되었습니다."
        else:
            guide = (f"{reason} → {FALLBACK_NOTE}인 {target}을 기준일로 사용합니다. "
                     f"리포트 제목과 메모에 '{FALLBACK_NOTE}' 기준임과 그 이유를 반드시 적으세요.")
        return (f"현재 시각(KST): {now:%Y-%m-%d %H:%M}\n"
                f"오늘 데이터 확정 시각: {ready_at:%H:%M}\n"
                f"기준 거래일: {target}\n{guide}")

    return HARNESS.run_tool("check_market_status", {}, _run)


@tool
def get_market_cap_ranking(market: str, top_n: int = 10) -> str:
    """KOSPI 또는 KOSDAQ의 시가총액 상위 종목 목록을 가져온다 (시가총액 단위: 억원).
    국내 전체 상위 종목을 고르려면 두 시장을 모두 조회해 시가총액을 비교해야 한다.

    Args:
        market: "KOSPI" 또는 "KOSDAQ"
        top_n: 가져올 종목 수 (1~20)
    """
    def _run() -> str:
        mk = market.strip().upper()
        if mk not in ("KOSPI", "KOSDAQ"):
            raise ValueError('market은 "KOSPI" 또는 "KOSDAQ"만 가능합니다')
        n = max(1, min(int(top_n), 20))
        try:
            rows, src = _fetch_ranking_json(mk, n), "json"
        except Exception as e:
            HARNESS.log("source_fallback", market=mk, error=repr(e))
            rows, src = _fetch_ranking_html(mk, n), "html"
        for r in rows:
            r["market"] = mk
        HARNESS.rankings[mk] = rows
        lines = [f"{i}. {r['name']} ({r['code']}) — 시가총액 {r['marcap_eok']:,}억원"
                 for i, r in enumerate(rows, 1)]
        return f"[{mk} 시가총액 순위, source={src}]\n" + "\n".join(lines)

    return HARNESS.run_tool("get_market_cap_ranking", {"market": market, "top_n": top_n}, _run)


@tool
def get_daily_ohlcv(code: str) -> str:
    """종목코드(6자리)의 기준 거래일 일봉 시세(시가·고가·저가·종가·거래량)를 가져온다.
    check_market_status로 기준 거래일을 정한 뒤에 호출해야 한다.

    Args:
        code: 6자리 종목코드 (예: "005930")
    """
    def _run() -> str:
        if HARNESS.trading_date is None:
            raise ValueError("기준 거래일이 정해지지 않았습니다. check_market_status를 먼저 호출하세요")
        c = re.sub(r"\D", "", code).zfill(6)
        rows = _fetch_daily(c, 5)
        for r in rows:
            HARNESS.ohlcv[(c, r["date"])] = r
        target = next((r for r in rows if r["date"] == HARNESS.trading_date), None)
        if target is None:
            raise ValueError(f"{c}의 {HARNESS.trading_date} 시세가 없습니다 (거래정지 등 확인 필요)")
        r = target
        return (f"[{c} | 기준 거래일 {r['date']}]\n"
                f"시가 {r['open']:,} | 고가 {r['high']:,} | 저가 {r['low']:,} "
                f"| 종가 {r['close']:,} | 거래량 {r['volume']:,}")

    return HARNESS.run_tool("get_daily_ohlcv", {"code": code}, _run)


@tool
def save_report(content: str) -> str:
    """완성한 Markdown 리포트를 outputs/ 폴더에 저장한다. 파일명은 기준 거래일로 자동 지정된다.

    Args:
        content: 저장할 Markdown 전체 내용
    """
    def _run() -> str:
        day = (HARNESS.trading_date or datetime.now(KST).strftime("%Y-%m-%d")).replace("-", "")
        path = OUTPUT_DIR / f"top5_{day}.md"
        path.write_text(content, encoding="utf-8")
        HARNESS.report_path = path
        return f"saved: outputs/{path.name} ({len(content)} chars)"

    return HARNESS.run_tool("save_report", {"chars": len(content)}, _run)


TOOLS = [check_market_status, get_market_cap_ranking, get_daily_ohlcv, save_report]


# =============================================================================
# 4. Agent
# =============================================================================
SYSTEM_PROMPT = f"""당신은 국내 주식시장 장마감 시세를 정리하는 Agent입니다.

목표: 국내(KOSPI+KOSDAQ) 시가총액 상위 {TOP_N}개 종목의 기준 거래일 고가·저가·종가·거래량을 리포트로 저장합니다.
기준 거래일은 check_market_status가 정합니다. 오늘 데이터가 확정되지 않았으면 {FALLBACK_NOTE}이 기준일입니다.

작업 원칙:
- 먼저 check_market_status로 기준 거래일을 확인하세요.
- 국내 전체 순위를 정하려면 KOSPI와 KOSDAQ 순위를 모두 조회한 뒤 시가총액(억원)을 비교해 상위 {TOP_N}개를 고르세요.
- 숫자는 반드시 get_daily_ohlcv 결과를 그대로 옮기세요. 계산하거나 반올림하거나 추정하지 마세요.
- 기준 거래일이 오늘이 아니면 제목에 "({FALLBACK_NOTE} 기준)"을 붙이고, 메모에 그 이유를 적으세요.
- 시가총액 순위는 조회 시점 기준이라는 점을 메모에 적으세요.
- Tool 결과가 ERROR 또는 BUDGET_EXCEEDED이면 그 지시를 따르세요.
- 마지막에 save_report로 저장하고, 최종 답변으로 표를 그대로 보여주세요.

리포트 형식 (Markdown):
# 국내 시가총액 Top {TOP_N} 장마감 시세 (YYYY-MM-DD)   ← 대체 기준일이면 "(YYYY-MM-DD, 직전 거래일 기준)"
| {' | '.join(REPORT_COLUMNS)} |
|{'---|' * len(REPORT_COLUMNS)}
| 1 | 종목명 | 6자리코드 | KOSPI | 고가 | 저가 | 종가 | 거래량 |
(가격은 원 단위, 거래량은 주 단위, 천 단위 쉼표 사용)

## 메모
- 데이터 출처와 기준일, 특이사항
"""


def build_agent():
    model = ChatOpenAI(model=MODEL_NAME, timeout=60, max_retries=2)
    return create_agent(model=model, tools=TOOLS, system_prompt=SYSTEM_PROMPT)


def build_task_message(feedback: str | None, h: Harness) -> str:
    msg = (f"국내 시가총액 상위 {TOP_N}개 종목의 장마감 고가, 저가, 종가, 거래량을 정리해 주세요. "
           "오늘 데이터를 얻을 수 없으면 직전 거래일 기준으로 정리해 주세요.")
    if feedback is None:
        return msg
    # ---- State Update: 이전 라운드 결과와 검증 피드백 전달 ----
    prev = h.report_path.read_text(encoding="utf-8")[:4000] if h.report_path and h.report_path.exists() else "(없음)"
    known = (f"기준 거래일: {h.trading_date}"
             f"{f' ({FALLBACK_NOTE}, 이유: {h.fallback_reason})' if h.fallback_reason else ''}, 조회한 시장: {', '.join(h.rankings) or '없음'}, "
             f"시세를 조회한 종목: {', '.join(sorted({c for c, _ in h.ohlcv})) or '없음'}")
    return (f"{msg}\n\n[이전 시도의 검증 실패 사항]\n{feedback}\n\n[이전 시도에서 확보한 정보]\n{known}\n"
            f"(이전 라운드 Tool 결과는 이 대화에 없으므로, 필요한 값은 다시 조회하세요.)\n\n"
            f"[이전 리포트]\n{prev}\n\n위 실패 사항을 고쳐서 다시 저장하세요.")


# =============================================================================
# 5. Verification — 리포트를 Tool 관찰값과 대조
# =============================================================================
@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def parse_report_table(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < len(REPORT_COLUMNS) or not re.fullmatch(r"\d{6}", cells[2]):
            continue
        rows.append({"rank": _to_int(cells[0]), "name": cells[1], "code": cells[2], "market": cells[3],
                     "high": _to_int(cells[4]), "low": _to_int(cells[5]),
                     "close": _to_int(cells[6]), "volume": _to_int(cells[7])})
    return rows


def expected_top(h: Harness) -> list[dict]:
    merged = [r for rows in h.rankings.values() for r in rows]
    return sorted(merged, key=lambda r: r["marcap_eok"], reverse=True)[:TOP_N]


def verify(h: Harness) -> list[Check]:
    if h.report_path is None or not h.report_path.exists():
        return [Check("report_saved", False, "save_report로 리포트를 저장하지 않았습니다.")]
    text = h.report_path.read_text(encoding="utf-8")
    rows = parse_report_table(text)
    checks = [Check("report_saved", True, f"outputs/{h.report_path.name}")]

    checks.append(Check("trading_date", bool(h.trading_date) and h.trading_date in text,
                        f"리포트에 기준 거래일 {h.trading_date} 표기 필요"
                        if not (h.trading_date and h.trading_date in text) else f"기준일 {h.trading_date}"))

    checks.append(Check("row_count", len(rows) == TOP_N, f"표의 종목 행 {len(rows)}개 (정확히 {TOP_N}개 필요)"))

    both = {"KOSPI", "KOSDAQ"} <= set(h.rankings)
    checks.append(Check("both_markets", both,
                        "KOSPI·KOSDAQ 모두 조회함" if both else
                        f"조회한 시장: {', '.join(h.rankings) or '없음'} — 두 시장을 모두 비교해야 함"))

    exp = expected_top(h)
    exp_codes = [r["code"] for r in exp]
    got_codes = [r["code"] for r in sorted(rows, key=lambda r: r["rank"])]
    checks.append(Check("top5_order", got_codes == exp_codes,
                        "순위 일치" if got_codes == exp_codes else
                        f"시가총액 기준 기대 순서 {exp_codes}, 리포트 {got_codes}"))

    mismatches = []
    for r in rows:
        obs = h.ohlcv.get((r["code"], h.trading_date))
        if obs is None:
            mismatches.append(f"{r['code']}: {h.trading_date} 시세를 get_daily_ohlcv로 조회하지 않음")
            continue
        for k in ("high", "low", "close", "volume"):
            if r[k] != obs[k]:
                mismatches.append(f"{r['code']} {k}: 리포트 {r[k]:,} ≠ 조회값 {obs[k]:,}")
    checks.append(Check("numbers_grounded", not mismatches and bool(rows),
                        "모든 숫자가 조회값과 일치" if not mismatches else "; ".join(mismatches[:6])))

    if h.fallback_reason:
        noted = FALLBACK_NOTE in text
        checks.append(Check("fallback_noted", noted,
                            f"'{FALLBACK_NOTE}' 기준임을 명시함" if noted else
                            f"기준일이 오늘이 아님({h.fallback_reason}) — 리포트에 '{FALLBACK_NOTE}' 기준임을 명시해야 함"))

    bad = [r["code"] for r in rows if not (r["low"] <= r["close"] <= r["high"])]
    checks.append(Check("sanity", not bad, "저가 ≤ 종가 ≤ 고가" if not bad else f"범위 이상: {bad}"))
    return checks


# =============================================================================
# 6. Loop — 시간 조건 확인 → Agent+Harness → Verify → (Feedback → Re-run)
# =============================================================================
def maybe_wait(wait: bool) -> None:
    """--wait이면 오늘 데이터 확정 시각까지 기다린다. 아니면 바로 실행하고,
    기준일 선택(오늘 / 직전 거래일)은 check_market_status Tool이 맡는다."""
    now = datetime.now(KST)
    ready_at = data_ready_at(now)
    if not wait or now.weekday() >= 5 or now >= ready_at:
        if now.weekday() < 5 and now < ready_at:
            print(f"ℹ️ 오늘 데이터 확정 전입니다 ({now:%H:%M} < {ready_at:%H:%M} KST). "
                  f"직전 거래일 기준으로 실행합니다. 오늘 기준을 원하면 --wait를 쓰세요.")
        return
    secs = (ready_at - now).total_seconds()
    print(f"⏳ {ready_at:%H:%M} KST까지 {secs / 60:.0f}분 대기합니다...")
    time.sleep(secs)


def run(max_rounds: int, budget: Budget) -> bool:
    global HARNESS
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    HARNESS = Harness(budget=budget, trace_path=OUTPUT_DIR / f"trace_{stamp}.jsonl")
    agent = build_agent()
    recursion_limit = 2 * (budget.max_tool_calls + 5) + 1

    feedback: str | None = None
    prev_failures: list[str] | None = None

    for round_no in range(1, max_rounds + 1):
        print(f"\n===== Round {round_no}/{max_rounds} =====")
        HARNESS.start_round(round_no)
        final_answer = ""
        try:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": build_task_message(feedback, HARNESS)}]},
                config={"recursion_limit": recursion_limit, "callbacks": [ModelCallCounter(HARNESS)]},
            )
            final_answer = str(result["messages"][-1].content)
            HARNESS.log("agent_done", answer=final_answer)
        except HarnessStop as e:
            HARNESS.log("harness_stop", reason=str(e))
        except GraphRecursionError:
            HARNESS.log("harness_stop", reason=f"recursion_limit={recursion_limit}")

        HARNESS.log("round_usage", tool_calls=HARNESS.tool_calls, model_calls=HARNESS.model_calls,
                    per_tool=HARNESS.per_tool, seconds=round(HARNESS.elapsed(), 1))

        checks = verify(HARNESS)
        for c in checks:
            print(f"  {'PASS' if c.passed else 'FAIL'} {c.name:<17} {c.detail}")
        failed = [c for c in checks if not c.passed]
        HARNESS.log("verify", passed=not failed, failed=[c.name for c in failed])

        if not failed:
            HARNESS.log("loop_end", status="passed", rounds=round_no)
            print(f"\n✅ 검증 통과 — outputs/{HARNESS.report_path.name}\n\n{final_answer}")
            return True

        failures = [f"{c.name}: {c.detail}" for c in failed]
        if failures == prev_failures:
            HARNESS.log("loop_end", status="no_progress", rounds=round_no)
            print("\n⚠️ 같은 실패가 반복되어 조기 종료합니다.")
            return False
        prev_failures = failures
        feedback = "\n".join(f"- {f}" for f in failures)
        HARNESS.log("feedback", feedback=feedback)

    HARNESS.log("loop_end", status="max_rounds", rounds=max_rounds)
    print(f"\n⚠️ Loop Limit({max_rounds}) 도달.")
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="국내 시가총액 Top 5 장마감 시세 Agent")
    ap.add_argument("--wait", action="store_true",
                    help="오늘 데이터 확정 시각까지 기다렸다가 오늘 기준으로 실행 (기본: 직전 거래일로 대체)")
    ap.add_argument("--settle-min", type=int, default=DEFAULT_SETTLE_MIN, help="15:30 이후 대기 분")
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--max-tool-calls", type=int, default=16)
    args = ap.parse_args()

    global SETTLE_MIN
    SETTLE_MIN = args.settle_min
    maybe_wait(args.wait)
    ok = run(args.max_rounds, Budget(max_tool_calls=args.max_tool_calls))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()