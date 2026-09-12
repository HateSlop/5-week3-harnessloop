"""공식 MediaWiki Action API를 통해 한국어 위키백과를 검색합니다."""

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://ko.wikipedia.org/w/api.php"
DEFAULT_USER_AGENT = (
    "MinimalAgentLoopWorkshop/1.0 "
    "(educational notebook; contact: set WIKIMEDIA_USER_AGENT in .env)"
)


def wikipedia_search(query: str, *, timeout_seconds: float | None = None) -> str:
    """위키백과 검색 결과 최대 3개의 짧은 소개와 URL을 반환합니다."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": 3,
        "prop": "extracts|info",
        "inprop": "url",
        "exintro": 1,
        "explaintext": 1,
        "exchars": 500,
        "format": "json",
        "formatversion": 2,
    }
    request = Request(
        f"{API_URL}?{urlencode(params)}",
        headers={"User-Agent": os.getenv("WIKIMEDIA_USER_AGENT", DEFAULT_USER_AGENT)},
    )
    request_options = {} if timeout_seconds is None else {"timeout": timeout_seconds}
    with urlopen(request, **request_options) as response:
        data = json.load(response)

    pages = sorted(data.get("query", {}).get("pages", []), key=lambda page: page["index"])
    results = [
        {
            "title": page["title"],
            "summary": page.get("extract", "").strip(),
            "url": page["fullurl"],
        }
        for page in pages
    ]
    return json.dumps({"query": query, "results": results}, ensure_ascii=False)
