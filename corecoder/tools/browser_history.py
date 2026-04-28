"""Browser history tool (local Chrome History SQLite)."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .base import Tool


def _default_history_path() -> Path | None:
    home = Path.home()
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", ""))
        if not str(base):
            return None
        return base / "Google" / "Chrome" / "User Data" / "Default" / "History"
    # Linux default
    return home / ".config" / "google-chrome" / "Default" / "History"


def _chrome_time_to_iso(microseconds_since_1601: int) -> str:
    if not microseconds_since_1601:
        return "unknown"
    epoch_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)
    dt = epoch_1601 + timedelta(microseconds=microseconds_since_1601)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _tokenize_query(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", query.lower())
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique


class BrowserHistoryTool(Tool):
    name = "browser_history"
    description = (
        "Read local Chrome browsing history. "
        "Supports keyword filtering and result limits."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional keyword filter for URL/title",
            },
            "limit": {
                "type": "integer",
                "description": "Number of records to return (default 20, max 100).",
            },
            "profile_path": {
                "type": "string",
                "description": "Optional custom Chrome History file path.",
            },
            "strict_domain": {
                "type": "string",
                "description": "Optional domain constraint. When set, URL host must contain this domain (e.g. zhihu.com).",
            },
        },
    }

    def execute(self, query: str = "", limit: int = 20, profile_path: str = "", strict_domain: str = "") -> str:
        history_path = Path(profile_path).expanduser().resolve() if profile_path else _default_history_path()
        if history_path is None:
            return "Error: could not determine default Chrome History path."
        if not history_path.exists():
            return f"Error: History DB not found at {history_path}"

        limit = max(1, min(limit, 100))
        query = (query or "").strip()
        strict_domain = (strict_domain or "").strip().lower()

        # Chrome may keep the DB locked; copy to a temp file for readonly query.
        #
        # Important: when strict_domain is provided without a query, we should
        # fetch more candidates first, then filter by domain. Otherwise, if the
        # most recent N records are not from that domain, we would incorrectly
        # return empty even though older records match.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "History.copy.db"
            shutil.copy2(history_path, tmp_db)
            conn = sqlite3.connect(tmp_db)
            try:
                cur = conn.cursor()
                if query:
                    terms = _tokenize_query(query) or [query.lower()]
                    clauses: list[str] = []
                    params: list[object] = []
                    for term in terms:
                        clauses.append("(LOWER(urls.url) LIKE ? OR LOWER(urls.title) LIKE ?)")
                        like = f"%{term}%"
                        params.extend([like, like])

                    sql = """
                        SELECT urls.url, urls.title, urls.visit_count, visits.visit_time
                        FROM visits
                        JOIN urls ON visits.url = urls.id
                        WHERE
                    """
                    sql += " OR ".join(clauses)
                    sql += " ORDER BY visits.visit_time DESC LIMIT ?"
                    params.append(max(limit * 5, 60))
                    cur.execute(sql, tuple(params))
                else:
                    candidate_limit = limit
                    if strict_domain:
                        candidate_limit = min(max(limit * 50, 300), 5000)
                    sql = """
                        SELECT urls.url, urls.title, urls.visit_count, visits.visit_time
                        FROM visits
                        JOIN urls ON visits.url = urls.id
                        ORDER BY visits.visit_time DESC
                        LIMIT ?
                    """
                    cur.execute(sql, (candidate_limit,))

                rows = cur.fetchall()
            finally:
                conn.close()

        if not rows:
            if query:
                return f"No browser history matched query: {query}"
            return "No browser history records found."

        if query:
            terms = _tokenize_query(query) or [query.lower()]

            def _match_score(row: tuple[str, str, int, int]) -> tuple[int, int]:
                url, title, _visit_count, visit_time = row
                content = f"{(title or '').lower()} {(url or '').lower()}"
                score = sum(1 for term in terms if term in content)
                return score, int(visit_time or 0)

            rows = sorted(rows, key=_match_score, reverse=True)
            rows = [row for row in rows if _match_score(row)[0] > 0][:limit]
            if not rows:
                return f"No browser history matched query: {query}"

        if strict_domain:
            def _host_match(row: tuple[str, str, int, int]) -> bool:
                url = (row[0] or "").strip()
                host = urlparse(url).netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                return strict_domain in host

            rows = [row for row in rows if _host_match(row)]
            rows = rows[:limit]
            if not rows:
                if query:
                    return f"No browser history matched query: {query} with strict domain: {strict_domain}"
                return f"No browser history matched strict domain: {strict_domain}"

        lines: list[str] = []
        lines.append(f"Browser history results ({len(rows)}):")
        for idx, (url, title, visit_count, visit_time) in enumerate(rows, start=1):
            safe_title = (title or "").strip() or "(no title)"
            when = _chrome_time_to_iso(int(visit_time) if visit_time else 0)
            lines.append(
                f"{idx}. [{when}] {safe_title}\n"
                f"   URL: {url}\n"
                f"   Visits: {visit_count}"
            )
        return "\n".join(lines)
