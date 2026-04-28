from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from ..tools.sandbox import SANDBOX_ROOT, ensure_within_sandbox, sandbox_path


class FileTaskMixin:
    def _run_file_task(self, user_input: str) -> str:
        expert = self._experts["file"]
        text = user_input.strip()
        lower = text.lower()
        if any(k in text for k in ("内容", "格式", "覆盖", "替换", "重写", "转为", "转成", "改成")) and (
            ".json" in lower or "json" in lower or "文本" in text or "plain text" in lower or bool(self.context.last_file_path)
        ):
            rw = self._parse_rewrite_intent(user_input)
            target = str(rw.get("target_format", "unknown"))
            conf = float(rw.get("confidence", 0.0) or 0.0)
            path = str(rw.get("path", "")).strip()
            if rw.get("needs_clarification", False):
                path_hint = path or str(self.context.last_file_path or "")
                return self._format_evidence_result(
                    summary="我理解到你在做“文件格式改写”，但目标格式不够明确，先确认一次。",
                    route_key="file",
                    evidence_lines=[f"intent={rw}", f"context_last_file={self.context.last_file_path!r}"],
                    detail=f"请回复其中一个：\nA) 转为普通文本并覆盖原文件\nB) 规范为合法 JSON 并覆盖原文件\n目标文件：{path_hint or '(未识别到，请补充文件名或路径)'}",
                )
            if conf >= 0.75 and target == "plaintext" and (path or self.context.last_file_path):
                return self._file_task_json_to_plaintext(path or str(self.context.last_file_path or ""))
            if conf >= 0.75 and target == "json" and (path or self.context.last_file_path):
                return self._file_task_json_normalize(user_input, path or str(self.context.last_file_path or ""))

        followup_path = self._resolve_followup_file_path(text)
        if self._user_wants_plaintext_file_rewrite(text):
            target = followup_path or (str(self.context.last_file_path) if self.context.last_file_path else "")
            if target:
                return self._file_task_json_to_plaintext(target)

        json_fix_path = self._extract_json_followup_path(text)
        if not json_fix_path and self._user_wants_json_file_rewrite(text):
            resolved = self._resolve_followup_file_path(text)
            if resolved:
                json_fix_path = resolved
            else:
                lp = self.context.last_file_path
                if lp:
                    try:
                        p = Path(str(lp)).expanduser().resolve()
                        if not p.is_file() and p.suffix:
                            candidates = [c for c in SANDBOX_ROOT.glob(f"{p.stem}.*") if c.is_file()]
                            if candidates:
                                candidates.sort(key=lambda c: c.stat().st_mtime, reverse=True)
                                json_fix_path = str(candidates[0].resolve())
                        elif p.is_file() and str(lp).lower().endswith(".json"):
                            json_fix_path = str(p)
                    except OSError:
                        pass
        if json_fix_path and self._user_wants_json_file_rewrite(text):
            return self._file_task_json_normalize(user_input, json_fix_path)

        intent = self._parse_file_intent(user_input)

        def _tool(name: str):
            return next((t for t in expert.tools if t.name == name), None)

        action = str(intent.get("action", "")).strip().lower()
        if action:
            path = str(intent.get("path", "")).strip() or self._extract_windows_path(text) or str(SANDBOX_ROOT)
            if action == "list":
                tool = _tool("list_directory")
                if tool is None:
                    return "Error: list_directory tool is not available."
                recursive = bool(intent.get("recursive", False))
                limit = max(1, min(int(intent.get("limit", 20) or 20), 2000))
                result = tool.execute(path=path, recursive=recursive, limit=limit)
                return self._format_evidence_result("已列出目录内容。", "file", [f"tool=list_directory args={{'path': {path!r}, 'recursive': {recursive}, 'limit': {limit}}}"], result)
            if action == "search_content":
                tool = _tool("grep_in_files") or _tool("grep")
                if tool is None:
                    return "Error: grep_in_files tool is not available."
                query = str(intent.get("query", "")).strip() or self._extract_after_keyword(text, ("搜索", "查找", "grep")) or ""
                include = str(intent.get("include", "")).strip()
                result = tool.execute(query=query, path=path, include=include)
                return self._format_evidence_result("已完成文件内容搜索。", "file", [f"tool=grep_in_files args={{'query': {query!r}, 'path': {path!r}, 'include': {include!r}}}"], result)
            if action == "search_name":
                tool = _tool("search_files")
                if tool is None:
                    return "Error: search_files tool is not available."
                query = str(intent.get("query", "")).strip() or self._extract_after_keyword(text, ("搜索", "查找", "文件名")) or ""
                limit = max(1, min(int(intent.get("limit", 20) or 20), 2000))
                result = tool.execute(query=query, path=path, limit=limit)
                return self._format_evidence_result("已完成文件名搜索。", "file", [f"tool=search_files args={{'query': {query!r}, 'path': {path!r}, 'limit': {limit}}}"], result)
            if action == "file_info":
                tool = _tool("file_info")
                if tool is None:
                    return "Error: file_info tool is not available."
                result = tool.execute(path=path)
                return self._format_evidence_result("已获取文件信息。", "file", [f"tool=file_info args={{'path': {path!r}}}"], result)

        return self._run_agent_task_with_evidence("file", user_input)

    @staticmethod
    def _extract_result_limit(user_input: str, default: int = 20) -> int:
        text = user_input.lower()
        m = re.search(r"(?:前|返回|只返回|给我|输出)?\s*(\d+)\s*(?:条|个)", text)
        if m:
            return max(1, min(int(m.group(1)), 100))
        cn_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        m = re.search(r"前([一二两三四五六七八九十])条", text)
        if m:
            return cn_map.get(m.group(1), default)
        if "前三条" in text:
            return 3
        if "前五条" in text:
            return 5
        if "前十条" in text:
            return 10
        return default

    @staticmethod
    def _extract_windows_path(text: str) -> str:
        m = re.search(r"([A-Za-z]:\\[^\s，。,.!?！？；;]+)", text)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_after_keyword(text: str, keywords: tuple[str, ...]) -> str:
        for kw in keywords:
            idx = text.find(kw)
            if idx >= 0:
                return text[idx + len(kw):].strip(" ：:，,。")
        return ""

    @staticmethod
    def _extract_named_output_file(user_input: str) -> str:
        text = user_input.strip()
        m = re.search(r"(?:写入|保存到|写到|存成)\s*([^\s，。,.!?！？；;]+?)\s*文件", text)
        return m.group(1) if m else ""

    @staticmethod
    def _normalize_sandbox_file_path(file_path: str) -> str:
        fp = (file_path or "").strip().strip("\"'")
        if not fp:
            return str(sandbox_path("browser_history_report.txt"))
        is_absolute_windows = bool(re.match(r"^[A-Za-z]:\\", fp))
        if not is_absolute_windows and "\\" not in fp and "/" not in fp:
            if "." not in fp:
                fp = fp + ".txt"
            return str(sandbox_path(fp))
        if not is_absolute_windows:
            norm_rel = fp.replace("/", "\\").lstrip("\\")
            return str(sandbox_path(*Path(norm_rel).parts))
        return fp

    @staticmethod
    def _extract_json_followup_path(text: str) -> str:
        wp = FileTaskMixin._extract_windows_path(text)
        if wp and wp.lower().endswith(".json"):
            return wp
        m = re.search(r"\b([\w.\-]+\.json)\b", text, re.I)
        if m:
            return FileTaskMixin._normalize_sandbox_file_path(m.group(1))
        return ""

    @staticmethod
    def _user_wants_json_file_rewrite(text: str) -> bool:
        low = text.lower()
        if FileTaskMixin._user_wants_plaintext_file_rewrite(text):
            return False
        if not (".json" in low or "json格式" in low or bool(re.search(r"合法\s*json", low))):
            return False
        return any(t in text for t in ("内容", "合法", "修正", "改成", "重写", "结构化", "json格式", "也是json", "格式正确"))

    @staticmethod
    def _user_wants_plaintext_file_rewrite(text: str) -> bool:
        lower = text.lower()
        text_touch = any(k in text for k in ("普通文本", "纯文本", "文本格式", "转为文本", "转成文本", "改成文本")) or ("plain text" in lower)
        overwrite_touch = any(k in text for k in ("覆盖", "替换", "重写", "覆盖原本"))
        return text_touch and overwrite_touch

    def _resolve_followup_file_path(self, text: str) -> str:
        path = self._extract_windows_path(text) or self._extract_json_followup_path(text)
        if path:
            return self._normalize_sandbox_file_path(path)
        for name in re.findall(r"[A-Za-z0-9_\-]{3,}", text):
            if "." in name:
                continue
            try:
                candidates = [p for p in SANDBOX_ROOT.glob(f"{name}.*") if p.is_file()]
                if candidates:
                    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    return str(candidates[0].resolve())
            except OSError:
                continue
        if self.context.last_file_path and any(k in text for k in ("刚才", "这个文件", "该文件", "其中", "原本", "persist_demo", "内容")):
            return str(self.context.last_file_path)
        return ""

    def _repair_missing_sandbox_path(self, path: str) -> str:
        p = (path or "").strip()
        if not p:
            return ""
        norm = self._normalize_sandbox_file_path(p)
        try:
            rp = Path(norm).expanduser().resolve()
            if rp.is_file():
                return str(rp)
            stem = rp.stem if rp.suffix else rp.name
            if stem:
                candidates = [c for c in SANDBOX_ROOT.glob(f"{stem}.*") if c.is_file()]
                if candidates:
                    candidates.sort(key=lambda c: c.stat().st_mtime, reverse=True)
                    return str(candidates[0].resolve())
        except OSError:
            return norm
        return norm

    @staticmethod
    def _json_to_plain_text(value: object, indent: int = 0) -> list[str]:
        pad = "  " * indent
        if isinstance(value, dict):
            lines: list[str] = []
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{pad}{k}:")
                    lines.extend(FileTaskMixin._json_to_plain_text(v, indent + 1))
                else:
                    lines.append(f"{pad}{k}: {v}")
            return lines
        if isinstance(value, list):
            lines: list[str] = []
            for i, item in enumerate(value, start=1):
                if isinstance(item, (dict, list)):
                    lines.append(f"{pad}- item {i}:")
                    lines.extend(FileTaskMixin._json_to_plain_text(item, indent + 1))
                else:
                    lines.append(f"{pad}- {item}")
            return lines
        return [f"{pad}{value}"]

    @staticmethod
    def _decode_text_scalar(raw: str) -> object:
        v = raw.strip()
        if v in ("", "None", "null", "NULL"):
            return None
        if re.fullmatch(r"-?\d+", v):
            try:
                return int(v)
            except ValueError:
                return v
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        return v

    @staticmethod
    def _parse_plaintext_kv_history(raw: str) -> dict[str, object] | None:
        lines = [ln.rstrip() for ln in raw.splitlines() if ln.strip()]
        if not lines or ":" not in lines[0]:
            return None
        out: dict[str, object] = {}
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith(" "):
                i += 1
                continue
            m = re.match(r"^([A-Za-z_][\w]*)\s*:\s*(.*)$", line)
            if not m:
                i += 1
                continue
            key, value = m.group(1), m.group(2)
            if key != "records":
                out[key] = FileTaskMixin._decode_text_scalar(value)
                i += 1
                continue
            records: list[dict[str, object]] = []
            i += 1
            while i < len(lines):
                item_line = lines[i]
                if not item_line.startswith("  "):
                    break
                if re.match(r"^\s*-\s*item\s+\d+:\s*$", item_line):
                    rec: dict[str, object] = {}
                    i += 1
                    while i < len(lines):
                        field_line = lines[i]
                        if not field_line.startswith("    "):
                            break
                        fm = re.match(r"^\s*([A-Za-z_][\w]*)\s*:\s*(.*)$", field_line)
                        if fm:
                            rec[fm.group(1)] = FileTaskMixin._decode_text_scalar(fm.group(2))
                        i += 1
                    records.append(rec)
                    continue
                i += 1
            out["records"] = records
        if not out:
            return None
        if "records" in out and isinstance(out["records"], list):
            out["record_count"] = len(out["records"])
        return out

    def _file_task_json_to_plaintext(self, file_path: str) -> str:
        expert = self._experts["file"]
        write_tool = next((t for t in expert.tools if t.name == "write_file"), None)
        if write_tool is None:
            return "Error: write_file tool is not available for file expert."
        file_path = self._repair_missing_sandbox_path(file_path)
        p = Path(file_path).expanduser().resolve()
        denied = ensure_within_sandbox(p)
        if denied:
            return self._format_evidence_result(f"无法读取文件：{denied}", "file", [f"path={file_path!r}"], "")
        if not p.is_file():
            return self._format_evidence_result(f"文件不存在或不是普通文件：{file_path}", "file", [f"path={file_path!r}"], "")
        raw_file = p.read_text(encoding="utf-8", errors="replace")
        try:
            parsed = json.loads(raw_file)
        except json.JSONDecodeError:
            return self._format_evidence_result("目标文件不是合法 JSON，无法执行“转为普通文本”操作。", "file", [f"path={file_path!r}", "parse=json.loads FAILED"], raw_file[:2000] + ("..." if len(raw_file) > 2000 else ""))
        plain = "\n".join(self._json_to_plain_text(parsed)).strip() + "\n"
        wr = write_tool.execute(file_path=file_path, content=plain)
        return self._format_evidence_result("已将 JSON 内容转换为普通文本并覆盖原文件。", "file", [f"path={file_path!r}", "tool=write_file args=plain-text overwrite"], f"{wr}\n\n---\n{plain[:2500]}{'...' if len(plain) > 2500 else ''}")

    def _parse_rewrite_intent(self, user_input: str) -> dict:
        schema = {"target_format": "unknown", "overwrite": False, "path": "", "confidence": 0.0, "needs_clarification": False, "reason": ""}
        prompt = (
            "你是文件改写意图解析器。把用户句子解析为JSON，仅输出JSON。\n"
            "字段：target_format(json/plaintext/unknown), overwrite(bool), path, confidence(0~1), needs_clarification(bool), reason。\n"
            "规则：\n1) “普通文本/纯文本/文本格式/plain text” => target_format=plaintext。\n"
            "2) “合法JSON/标准JSON/json格式/结构化” => target_format=json。\n"
            "3) 同时出现两类信号时：若有“转为/改成文本/普通文本”优先 plaintext；若有“规范/合法JSON”优先 json。\n"
            "4) 若无法判断，target_format=unknown, needs_clarification=true, confidence<=0.6。\n"
            f"默认值: {json.dumps(schema, ensure_ascii=False)}\n用户输入: {user_input}"
        )
        parsed = self._parse_intent_json(prompt, schema)
        txt = user_input
        low = txt.lower()
        text_tokens = ("普通文本", "纯文本", "文本格式", "转为文本", "转成文本", "改成文本", "plain text")
        json_tokens = ("合法json", "标准json", "json格式", "结构化", "规范为json", "规范成json")
        text_hit = any(t in txt for t in text_tokens) or ("plain text" in low)
        json_hit = any(t in low for t in json_tokens)
        overwrite_hit = any(k in txt for k in ("覆盖", "替换", "重写", "覆盖原本"))
        path = str(parsed.get("path", "")).strip() or self._resolve_followup_file_path(txt)
        target = str(parsed.get("target_format", "unknown")).lower()
        if target not in ("json", "plaintext", "unknown"):
            target = "unknown"
        if text_hit and not json_hit:
            target, conf = "plaintext", (0.9 if overwrite_hit else 0.8)
        elif json_hit and not text_hit:
            target, conf = "json", 0.9
        elif text_hit and json_hit:
            if any(k in txt for k in ("转为", "转成", "改成文本", "普通文本", "纯文本")):
                target, conf = "plaintext", 0.86
            elif any(k in txt for k in ("合法JSON", "标准JSON", "规范为", "规范成")):
                target, conf = "json", 0.84
            else:
                target, conf = "unknown", 0.55
        else:
            conf = max(0.0, min(float(parsed.get("confidence", 0.0) or 0.0), 1.0))
        needs = bool(parsed.get("needs_clarification", False))
        if target == "unknown" or conf < 0.75:
            needs = True
        return {"target_format": target, "overwrite": bool(parsed.get("overwrite", False) or overwrite_hit), "path": self._repair_missing_sandbox_path(path) if path else "", "confidence": conf, "needs_clarification": needs, "reason": str(parsed.get("reason", "")).strip()}

    def _file_task_json_normalize(self, user_input: str, file_path: str) -> str:
        expert = self._experts["file"]
        write_tool = next((t for t in expert.tools if t.name == "write_file"), None)
        if write_tool is None:
            return "Error: write_file tool is not available for file expert."
        file_path = self._repair_missing_sandbox_path(file_path)
        p = Path(file_path).expanduser().resolve()
        denied = ensure_within_sandbox(p)
        if denied:
            return self._format_evidence_result(f"无法读取文件：{denied}", "file", [f"path={file_path!r}"], "")
        if not p.is_file():
            return self._format_evidence_result(f"文件不存在或不是普通文件：{file_path}", "file", [f"path={file_path!r}"], "")
        raw_file = p.read_text(encoding="utf-8", errors="replace")
        stripped = raw_file.lstrip()
        new_body = None
        try:
            new_body = json.dumps(json.loads(stripped), ensure_ascii=False, indent=2) + "\n"
        except json.JSONDecodeError:
            pass
        if new_body is None:
            recs = self._parse_browser_history_plaintext(raw_file)
            if recs:
                payload = {"source": "chrome_browser_history", "exported_at": datetime.now(timezone.utc).isoformat(), "record_count": len(recs), "records": recs, "note": "从先前保存的纯文本浏览记录解析并转换为 JSON。"}
                new_body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if new_body is None:
            parsed_text = self._parse_plaintext_kv_history(raw_file)
            if parsed_text:
                new_body = json.dumps(parsed_text, ensure_ascii=False, indent=2) + "\n"
        if new_body is None:
            return self._format_evidence_result("未能将该文件识别为合法 JSON，也无法按浏览记录纯文本格式解析。", "file", [f"tool=read_file(raw) path={file_path!r}", "parse=json.loads FAILED; browser_history plaintext parse=0 records"], raw_file[:4000] + ("..." if len(raw_file) > 4000 else ""))
        wr = write_tool.execute(file_path=file_path, content=new_body)
        return self._format_evidence_result("已将文件内容规范为合法 JSON 并写回。", "file", [f"path={file_path!r}", "tool=write_file args=canonical JSON (UTF-8)"], f"{wr}\n\n---\n{new_body[:2500]}{'...' if len(new_body) > 2500 else ''}")

    def _parse_file_intent(self, user_input: str) -> dict:
        schema = {"action": "", "path": "", "query": "", "include": "", "limit": 20, "recursive": False}
        prompt = (
            "你是文件任务意图解析器。把用户请求解析为JSON，仅返回JSON，不要解释。\n"
            "action可选: list, search_content, search_name, file_info, unknown。\n其余字段: path, query, include, limit, recursive。\n"
            f"默认值: {json.dumps(schema, ensure_ascii=False)}\n用户输入: {user_input}"
        )
        return self._parse_intent_json(prompt, schema)
