from __future__ import annotations

import json
from typing import Any


class PlanStepMixin:
    def _execute_execution_plan(self, plan: dict[str, object]) -> str:
        name = str(plan.get("name", ""))
        steps = plan.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return "Error: invalid execution plan."

        evidence: list[str] = [f"plan={name}"]
        intermediate: dict[str, str] = {}

        for s in steps:
            action = str(s.get("action", ""))
            executor = self._get_step_executor(action)
            if executor is None:
                return self._format_evidence_result(
                    summary="执行草案中止：遇到未注册步骤。",
                    route_key="file",
                    evidence_lines=evidence + [f"unknown_step={s!r}"],
                    detail="请调整草案或注册新的 planner/step 执行器。",
                )
            err = executor(s, intermediate, evidence)
            if err is not None:
                return err

        if hasattr(self, "context") and isinstance(plan.get("semantic_slots"), dict):
            slots = plan.get("semantic_slots", {})
            intent_sig = (
                f"intent={slots.get('intent')} source={slots.get('source')} "
                f"transform={slots.get('transform')} target={slots.get('target')}"
            )
            self.context.record_semantic_success(str(plan.get("_user_input", "")), intent_sig)
            # preference learning: export+mail defaults to summarize.
            if slots.get("intent") in ("export", "summarize") and slots.get("source") == "mail":
                self.context.semantic_preferences["mail_export_default"] = str(slots.get("transform", "none"))
        detail_candidates = (
            intermediate.get("write_output"),
            intermediate.get("send_output"),
            intermediate.get("working_text"),
            intermediate.get("browser_output"),
            intermediate.get("mail_output"),
            intermediate.get("file_output"),
        )
        detail_parts: list[str] = []
        seen: set[str] = set()
        for part in detail_candidates:
            if not part:
                continue
            text = str(part).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            detail_parts.append(text)
        return self._format_evidence_result(
            summary="执行草案已完成。",
            route_key="file",
            evidence_lines=evidence,
            detail="\n\n".join(detail_parts) or "Done.",
        )

    def _get_tool(self, route: str, name: str):
        exp = self._experts.get(route)
        if exp is None:
            return None
        return next((t for t in exp.tools if t.name == name), None)

    def _exec_step_recent_history(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        t = self._get_tool("browser", "browser_history")
        if t is None:
            return "Error: browser_history tool is not available."
        args = dict(step.get("args", {}) or {})
        limit = int(args.get("limit", 3) or 3)
        out = t.execute(query="", limit=limit, strict_domain="")
        if hasattr(self, "_repair_text_mojibake"):
            out = self._repair_text_mojibake(out)
        evidence.append(f"tool=browser_history args={{'query': '', 'limit': {limit}, 'strict_domain': ''}}")
        intermediate["browser_output"] = out
        intermediate["working_text"] = out
        intermediate["variant_text"] = out
        if out.startswith("Error:") or out.startswith("No "):
            return self._format_evidence_result(
                summary="执行草案中止：浏览记录读取失败。",
                route_key="browser",
                evidence_lines=evidence,
                detail=out,
            )
        return None

    def _exec_step_list_recent(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        t = self._get_tool("mail", "gmail_list_recent")
        if t is None:
            return "Error: gmail_list_recent tool is not available."
        args = dict(step.get("args", {}) or {})
        limit = int(args.get("limit", 5) or 5)
        out = t.execute(limit=limit)
        evidence.append(f"tool=gmail_list_recent args={{'limit': {limit}}}")
        intermediate["mail_output"] = out
        intermediate["working_text"] = out
        if out.startswith("Error:"):
            return self._format_evidence_result(
                summary="执行草案中止：邮件读取失败。",
                route_key="mail",
                evidence_lines=evidence,
                detail=out,
            )
        return None

    def _exec_step_summarize_recent(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        list_tool = self._get_tool("mail", "gmail_list_recent")
        get_tool = self._get_tool("mail", "gmail_get_content")
        if list_tool is None or get_tool is None:
            return "Error: gmail_list_recent/gmail_get_content tool is not available."
        args = dict(step.get("args", {}) or {})
        limit = int(args.get("limit", 5) or 5)
        listed = list_tool.execute(limit=limit)
        evidence.append(f"tool=gmail_list_recent args={{'limit': {limit}}}")
        if listed.startswith("Error:"):
            return self._format_evidence_result(
                summary="执行草案中止：邮件读取失败。",
                route_key="mail",
                evidence_lines=evidence,
                detail=listed,
            )
        uids = self._extract_mail_uids(listed)
        if not uids:
            intermediate["mail_output"] = listed
            return None
        summaries: list[str] = []
        for uid in uids[:limit]:
            raw_mail = get_tool.execute(uid=uid, max_chars=6000)
            evidence.append(f"tool=gmail_get_content args={{'uid': {uid!r}, 'max_chars': 6000}}")
            if raw_mail.startswith("Error:"):
                summaries.append(f"- uid={uid}: 读取失败：{raw_mail}")
                continue
            one = self._summarize_single_email(raw_mail)
            summaries.append(f"- uid={uid}: {one}")
        intermediate["mail_output"] = "\n".join(summaries) if summaries else listed
        intermediate["working_text"] = intermediate["mail_output"]
        intermediate["variant_summary"] = intermediate["mail_output"]
        return None

    def _exec_step_summarize_text(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        source_text = (
            intermediate.get("mail_output")
            or intermediate.get("browser_output")
            or intermediate.get("file_output")
            or ""
        ).strip()
        if not source_text:
            return self._format_evidence_result(
                summary="执行草案中止：缺少可概括内容。",
                route_key="file",
                evidence_lines=evidence,
                detail="上一步没有产出可用文本。",
            )
        prompt = (
            "请将以下内容概括为简洁要点，中文输出，尽量 3-8 条：\n\n"
            + source_text[:7000]
        )
        try:
            resp = self.llm.chat(
                messages=[
                    {"role": "system", "content": "你是内容摘要助手，只输出摘要结果。"},
                    {"role": "user", "content": prompt},
                ]
            )
            text = (resp.content or "").strip()
            if not text:
                text = "（摘要为空）"
        except Exception as exc:
            return self._format_evidence_result(
                summary="执行草案中止：内容摘要失败。",
                route_key="file",
                evidence_lines=evidence,
                detail=f"Error: summarize failed: {exc}",
            )
        evidence.append("tool=llm_summarize_text args={'max_chars': 7000}")
        intermediate["mail_output"] = text
        intermediate["working_text"] = text
        intermediate["variant_summary"] = text
        return None

    def _exec_step_format_json_text(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        source_text = (
            intermediate.get("working_text")
            or intermediate.get("browser_output")
            or intermediate.get("file_output")
            or intermediate.get("mail_output")
            or ""
        ).strip()
        if not source_text:
            return self._format_evidence_result(
                summary="执行草案中止：缺少可转换 JSON 的内容。",
                route_key="file",
                evidence_lines=evidence,
                detail="上一步没有产出可用文本。",
            )
        records = self._parse_browser_history_plaintext(source_text)
        if records:
            payload: dict[str, Any] = {
                "source": "browser_history",
                "record_count": len(records),
                "records": records,
            }
        else:
            payload = {"text": source_text}
        json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        evidence.append("tool=format_json_text args={'strategy': 'browser_history_or_wrapped_text'}")
        intermediate["json_output"] = json_text
        intermediate["working_text"] = json_text
        intermediate["variant_json"] = json_text
        return None

    def _exec_step_read_file(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        t = self._get_tool("file", "read_file")
        if t is None:
            return "Error: read_file tool is not available."
        args = dict(step.get("args", {}) or {})
        fp = self._repair_missing_sandbox_path(str(args.get("file_path", "")))
        out = t.execute(file_path=fp, offset=1, limit=2000)
        if hasattr(self, "_repair_text_mojibake"):
            out = self._repair_text_mojibake(out)
        evidence.append(f"tool=read_file args={{'file_path': {fp!r}}}")
        intermediate["file_output"] = out
        intermediate["working_text"] = out
        if out.startswith("Error:"):
            return self._format_evidence_result(
                summary="执行草案中止：文件读取失败。",
                route_key="file",
                evidence_lines=evidence,
                detail=out,
            )
        return None

    def _exec_step_write_file(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        t = self._get_tool("file", "write_file")
        if t is None:
            return "Error: write_file tool is not available."
        args = dict(step.get("args", {}) or {})
        fp = self._normalize_sandbox_file_path(str(args.get("file_path", "")))
        fmt = str(args.get("format", "text")).strip().lower()
        content = (
            intermediate.get("working_text")
            or intermediate.get("json_output")
            or intermediate.get("mail_output")
            or intermediate.get("browser_output")
            or intermediate.get("file_output")
            or ""
        )
        if fmt == "markdown":
            content = self._to_markdown_document(content)
        elif fmt == "text":
            content = self._to_plain_text_document(content)
        out = t.execute(file_path=fp, content=content + "\n")
        evidence.append(f"tool=write_file args={{'file_path': {fp!r}, 'format': {fmt!r}}}")
        if out.startswith("Error:"):
            return self._format_evidence_result(
                summary="执行草案中止：文件写入失败。",
                route_key="file",
                evidence_lines=evidence,
                detail=out,
            )
        intermediate["write_output"] = out
        return None

    @staticmethod
    def _to_markdown_document(content: str) -> str:
        body = (content or "").strip()
        if not body:
            return "# Export\n\n(empty)"
        if body.startswith("{") or body.startswith("["):
            return "# Export (JSON)\n\n```json\n" + body + "\n```"
        return "# Export\n\n" + body

    @staticmethod
    def _to_plain_text_document(content: str) -> str:
        body = (content or "").strip()
        if not body:
            return "(empty)"
        if body.startswith("{") or body.startswith("["):
            try:
                parsed = json.loads(body)
                return json.dumps(parsed, ensure_ascii=False, indent=2)
            except Exception:
                return body
        return body

    def _exec_step_send_email(self, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
        t = self._get_tool("mail", "gmail_send_email")
        if t is None:
            return "Error: gmail_send_email tool is not available."
        args = dict(step.get("args", {}) or {})
        to = str(args.get("to", "")).strip()
        recipients = self._normalize_recipients(args.get("recipients", []), to)
        subject = str(args.get("subject", "")).strip() or "自动发送"
        prefix = str(args.get("prefix", "")).strip()
        include_formats = args.get("include_formats", ["text"])
        include = self._normalize_file_formats(include_formats) or ["text"]
        delivery_mode = str(args.get("delivery_mode", "combined") or "combined").strip().lower()
        parts: list[str] = []
        if "json" in include:
            j = (intermediate.get("variant_json") or intermediate.get("json_output") or "").strip()
            if j:
                parts.append("【JSON】\n" + j)
        if "markdown" in include:
            # markdown uses current working_text as source
            msrc = (intermediate.get("variant_summary") or intermediate.get("variant_text") or intermediate.get("working_text") or "").strip()
            if msrc:
                parts.append("【Markdown】\n" + self._to_markdown_document(msrc))
        if "text" in include:
            ttxt = (intermediate.get("variant_text") or intermediate.get("variant_summary") or intermediate.get("working_text") or "").strip()
            if ttxt:
                parts.append("【文本】\n" + ttxt)
        merged = "\n\n".join(p for p in parts if p) or "(empty)"
        results: list[str] = []
        if delivery_mode == "separate":
            format_payloads: list[tuple[str, str]] = []
            for part in parts:
                if part.startswith("【JSON】"):
                    format_payloads.append(("json", part))
                elif part.startswith("【Markdown】"):
                    format_payloads.append(("markdown", part))
                elif part.startswith("【文本】"):
                    format_payloads.append(("text", part))
            if not format_payloads:
                format_payloads.append(("text", "(empty)"))
            for recipient in recipients:
                for fmt, payload in format_payloads:
                    body = (prefix + "\n\n" if prefix else "") + payload
                    fmt_subject = f"{subject} [{fmt}]"
                    out = t.execute(to=recipient, subject=fmt_subject, body=body)
                    evidence.append(
                        f"tool=gmail_send_email args={{'to': {recipient!r}, 'subject': {fmt_subject!r}, 'body': '<generated>'}}"
                    )
                    if out.startswith("Error:"):
                        return self._format_evidence_result(
                            summary="执行草案中止：邮件发送失败。",
                            route_key="mail",
                            evidence_lines=evidence,
                            detail=out,
                        )
                    results.append(out)
        else:
            body = (prefix + "\n\n" if prefix else "") + merged
            for recipient in recipients:
                out = t.execute(to=recipient, subject=subject, body=body)
                evidence.append(
                    f"tool=gmail_send_email args={{'to': {recipient!r}, 'subject': {subject!r}, 'body': '<generated>'}}"
                )
                if out.startswith("Error:"):
                    return self._format_evidence_result(
                        summary="执行草案中止：邮件发送失败。",
                        route_key="mail",
                        evidence_lines=evidence,
                        detail=out,
                    )
                results.append(out)
        intermediate["send_output"] = "\n".join(results)
        return None
