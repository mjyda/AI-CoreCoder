from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import urlparse

from ..tools.sandbox import sandbox_path


class BrowserTaskMixin:
    @staticmethod
    def _repair_text_mojibake(text: str) -> str:
        raw = text or ""
        if not raw:
            return raw
        repaired = raw
        replacements = {
            "QQ閭�绠�": "QQ邮箱",
            "閭�绠�": "邮箱",
        }
        for bad, good in replacements.items():
            repaired = repaired.replace(bad, good)
        # Common mojibake fix: UTF-8 bytes decoded as latin-1/cp1252.
        def _fix_chunk(chunk: str) -> str:
            try:
                fixed = chunk.encode("latin-1").decode("utf-8")
            except Exception:
                return chunk
            # Only accept if decode clearly improves readability (contains CJK).
            if re.search(r"[\u4e00-\u9fff]", fixed):
                return fixed
            return chunk

        repaired = re.sub(r"[\x80-\xff]{3,}", lambda m: _fix_chunk(m.group(0)), repaired)
        return repaired

    def _run_browser_task(self, user_input: str) -> str:
        expert = self._experts["browser"]
        browser_tool = next((t for t in expert.tools if t.name == "browser_history"), None)
        if browser_tool is None:
            return "Error: browser_history tool is not available for browser expert."
        intent = self._parse_browser_action_intent(user_input)
        if bool(intent.get("needs_clarification", False)) and float(intent.get("confidence", 0.0) or 0.0) < 0.72:
            return self._format_evidence_result(
                summary="浏览器任务意图存在歧义，先确认执行目标。",
                route_key="browser",
                evidence_lines=[f"intent={intent}"],
                detail=(
                    "请确认：\nA) 仅查询并展示浏览记录\nB) 查询后写入文件（JSON）\n"
                    "C) 查询后写入文件（普通文本）\n你也可以直接说：'查最近3条并写入 xxx.json，内容用普通文本'。"
                ),
            )
        query = str(intent.get("query", "")).strip() or self._extract_browser_query(user_input)
        if intent.get("mode") == "recent" or self._should_use_recent_history(user_input):
            query = ""
        strict_domain = str(intent.get("strict_domain", "")).strip().lower() or self._extract_strict_domain(user_input, query)
        limit = int(intent.get("limit", 0) or 0) or self._extract_result_limit(user_input)
        limit = max(1, min(limit, 100))
        wants_search = False if (intent.get("mode") == "recent" or self._should_use_recent_history(user_input)) else (
            intent.get("mode") == "search" or any(k in user_input.lower() for k in ("搜索", "查找", "search", "find"))
        )
        if wants_search and not query and not strict_domain:
            return self._format_evidence_result(
                summary="检测到你要求关键词搜索，但未能解析出关键词。",
                route_key="browser",
                evidence_lines=["tool=browser_history args=NOT_CALLED", f"user_input={user_input!r}"],
                detail="请改写为：'搜索 GitHub'、'在浏览记录中查找 GitHub' 或 'search github'。",
            )
        if any(k in user_input for k in ("统计", "最多", "top", "TOP")) and limit < 30:
            limit = max(limit, 30)
        raw = browser_tool.execute(query=query, limit=limit, strict_domain=strict_domain)
        raw = self._repair_text_mojibake(raw)
        if raw.startswith("Error:") or raw.startswith("No "):
            return raw
        write_to_file = bool(intent.get("write_to_file", False)) or any(k in user_input for k in ("写入文件", "保存到文件", "写到文件", "存成文件"))
        if write_to_file:
            write_tool = next((t for t in expert.tools if t.name == "write_file"), None)
            if write_tool is not None:
                file_path = (
                    str(intent.get("file_path", "")).strip()
                    or self._extract_windows_path(user_input)
                    or self._extract_named_output_file(user_input)
                    or str(sandbox_path("browser_history_report.txt"))
                )
                file_path = self._normalize_sandbox_file_path(file_path)
                target_format = str(intent.get("target_format", "auto")).lower()
                want_json_body = target_format == "json" or (
                    target_format == "auto" and (file_path.lower().endswith(".json") or any(p in user_input for p in ("json格式", "合法json", "标准json", "以json格式", "内容也是json", "保存为json")))
                )
                body_to_write = self._browser_history_to_json_file_body(raw, query=query, limit=limit, strict_domain=strict_domain) if want_json_body else raw + "\n"
                write_result = write_tool.execute(file_path=file_path, content=body_to_write)
                return self._format_evidence_result(
                    summary="已整理浏览记录并以合法 JSON 写入文件。" if want_json_body else "已整理浏览记录并写入文件。",
                    route_key="browser",
                    evidence_lines=[
                        f"tool=browser_history args={{'query': {query!r}, 'limit': {limit}, 'strict_domain': {strict_domain!r}}}",
                        f"tool=write_file args={{'file_path': {file_path!r}, 'format': {'json' if want_json_body else 'text'}, 'intent_confidence': {intent.get('confidence', 0.0)}}}",
                    ],
                    detail=f"{write_result}\n\n---\n{raw}",
                )
        if any(k in user_input for k in ("统计", "最多", "top", "TOP", "站点")):
            ranked = self._top_domains_from_history(raw, top_n=3)
            if ranked:
                lines = ["最近浏览记录中出现最多的站点："] + [f"{i}. {d} ({c} 次)" for i, (d, c) in enumerate(ranked, start=1)]
                return self._format_evidence_result(
                    summary="\n".join(lines),
                    route_key="browser",
                    evidence_lines=[f"tool=browser_history args={{'query': {query!r}, 'limit': {limit}, 'strict_domain': {strict_domain!r}}}", "result=top-domain-statistics"],
                    detail=raw,
                )
        return self._format_evidence_result(
            summary="已按时间倒序返回浏览记录结果。",
            route_key="browser",
            evidence_lines=[f"tool=browser_history args={{'query': {query!r}, 'limit': {limit}, 'strict_domain': {strict_domain!r}}}"],
            detail=raw,
        )

    def _extract_browser_query(self, user_input: str) -> str:
        text = user_input.strip()
        m = re.search(r"(?:搜索|查找)\s*[:：]?\s*([^\s，。,.!?！？；;]+)", text, flags=re.IGNORECASE) or re.search(r"(?:search|find)\s*[:：]?\s*([^\s,.;!?]+)", text, flags=re.IGNORECASE)
        if m:
            return self._normalize_browser_query(m.group(1))
        site_map = self._site_mappings["query_aliases"]
        lowered = text.lower()
        for key, mapped in site_map.items():
            if key in text or key.lower() in lowered:
                return mapped
        return self._normalize_browser_query(text)

    def _normalize_browser_query(self, raw_query: str) -> str:
        q = (raw_query or "").strip()
        if not q:
            return ""
        for word in ("我的", "我", "浏览器", "历史", "记录", "最近", "查看", "查一下", "查询", "内容"):
            q = q.replace(word, "")
        q = q.strip("，。,.!?！？；;:： ")
        canonical = self._site_mappings["canonical_query"]
        lower = q.lower()
        for key, value in canonical.items():
            if key in q or key in lower:
                return value
        return q

    @staticmethod
    def _should_use_recent_history(user_input: str) -> bool:
        text = user_input.strip().lower()
        if any(k in text for k in ("搜索", "查找", "search", "find")):
            return False
        return any(k in text for k in ("前", "最近", "记录", "整理")) and "浏览器" in text

    def _extract_strict_domain(self, user_input: str, query: str) -> str:
        text = user_input.strip()
        lower = text.lower()
        explicit = re.search(r"(?:域名|domain)\s*[:：=]?\s*([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text)
        if explicit:
            return explicit.group(1).lower()
        domain_map = self._site_mappings["strict_domains"]
        for key, domain in domain_map.items():
            if key in text or key in lower:
                return domain
        query_lower = (query or "").lower()
        for key, domain in domain_map.items():
            if key == query_lower:
                return domain
        return ""

    @staticmethod
    def _browser_plaintext_slice(raw: str) -> str:
        idx = raw.find("Browser history results")
        if idx < 0:
            return raw.strip()
        tail = raw[idx:].strip()
        sep = tail.find("\n---\n")
        if sep >= 0:
            tail = tail[:sep].strip()
        return tail

    def _parse_browser_history_plaintext(self, raw: str) -> list[dict[str, object]]:
        text = self._repair_text_mojibake(self._browser_plaintext_slice(raw))
        records: list[dict[str, object]] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            m = re.match(r"^(\d+)\.\s*\[([^\]]+)\]\s*(.*)$", lines[i].strip())
            if not m:
                i += 1
                continue
            rec: dict[str, object] = {"index": int(m.group(1)), "visited_at": m.group(2).strip(), "title": (m.group(3) or "").strip() or None}
            i += 1
            url = None
            visits = None
            while i < len(lines):
                l2 = lines[i].strip()
                if re.match(r"^\d+\.\s*\[", l2):
                    break
                um = re.match(r"URL:\s*(\S+)", l2)
                if um:
                    url = um.group(1)
                vm = re.match(r"Visits:\s*(\d+)", l2)
                if vm:
                    visits = int(vm.group(1))
                i += 1
            rec["url"] = url
            rec["visit_count"] = visits
            records.append(rec)
        return records

    def _browser_history_to_json_file_body(self, raw: str, *, query: str, limit: int, strict_domain: str) -> str:
        block = self._browser_plaintext_slice(raw)
        records = self._parse_browser_history_plaintext(raw)
        payload: dict[str, object] = {
            "source": "chrome_browser_history",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "query": (query or None),
            "limit": limit,
            "strict_domain": (strict_domain or None),
            "record_count": len(records),
            "records": records,
        }
        if not records and "Browser history results" in block and "(0)" not in block:
            payload["parse_warning"] = "未能从 tool 输出中解析出记录；请检查文件是否为 browser_history 原始文本。"
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def _parse_browser_action_intent(self, user_input: str) -> dict:
        schema = {"mode": "recent", "query": "", "limit": 20, "strict_domain": "", "write_to_file": False, "file_path": "", "target_format": "auto", "confidence": 0.0, "needs_clarification": False, "reason": ""}
        prompt = (
            "你是意图解析器。把用户请求解析为JSON，仅返回JSON，不要解释。\n"
            "字段: mode(recent/search), query, limit(1-100), strict_domain, write_to_file(bool), file_path, "
            "target_format(auto/json/text), confidence(0~1), needs_clarification(bool), reason。\n"
            "规则：\n1) 提到“普通文本/纯文本/plain text”时 target_format=text。\n"
            "2) 提到“json格式/合法json/标准json”时 target_format=json。\n3) 只说保存但未指定格式时 target_format=auto。\n"
            f"默认值: {json.dumps(schema, ensure_ascii=False)}\n用户输入: {user_input}"
        )
        parsed = self._parse_intent_json(prompt, schema)
        fmt = str(parsed.get("target_format", "auto")).strip().lower()
        if fmt not in ("auto", "json", "text"):
            fmt = "auto"
        conf = max(0.0, min(float(parsed.get("confidence", 0.0) or 0.0), 1.0))
        parsed["target_format"] = fmt
        parsed["confidence"] = conf
        low = user_input.lower()
        has_text = any(k in user_input for k in ("普通文本", "纯文本", "文本格式", "转成文本", "转为文本")) or ("plain text" in low)
        has_json = any(k in low for k in ("json格式", "合法json", "标准json", "保存为json"))
        if has_text and not has_json:
            parsed["target_format"] = "text"
            parsed["confidence"] = max(float(parsed["confidence"]), 0.86)
        elif has_json and not has_text:
            parsed["target_format"] = "json"
            parsed["confidence"] = max(float(parsed["confidence"]), 0.86)
        elif has_text and has_json:
            parsed["needs_clarification"] = True
            parsed["confidence"] = min(float(parsed["confidence"]), 0.6)
        return parsed

    def _parse_browser_intent(self, user_input: str) -> dict:
        return self._parse_browser_action_intent(user_input)

    @staticmethod
    def _top_domains_from_history(raw: str, top_n: int = 3) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for line in raw.splitlines():
            if "URL:" not in line:
                continue
            url = line.split("URL:", 1)[1].strip()
            host = urlparse(url).netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                counts[host] = counts.get(host, 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
