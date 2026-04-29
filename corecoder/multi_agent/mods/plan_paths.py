from __future__ import annotations

from collections import deque
import json
import re
from typing import Any


class PlanPathMixin:
    def _parse_semantic_slots(self, user_input: str) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "intent": "unknown",
            "source": "unknown",
            "transform": "none",
            "target": "unknown",
            "constraints": {
                "limit": self._extract_result_limit(user_input, default=5),
                "subject": "",
                "recipient": "",
                "recipients": [],
                "path": "",
                "also_save_file": False,
                "file_formats": [],
                "local_formats": [],
                "email_formats": [],
                "email_delivery_mode": "combined",
                "transform_conflict": False,
            },
            "confidence": 0.0,
            "ambiguities": [],
        }
        semantic_hint = ""
        if hasattr(self, "context") and hasattr(self.context, "format_semantic_hint"):
            semantic_hint = self.context.format_semantic_hint()
        prompt = (
            "你是语义槽位解析器。只输出 JSON。\n"
            "字段：intent(export/send/summarize/read/unknown), "
            "source(browser/mail/file/unknown), transform(none/summarize/json/plaintext), "
            "target(file/mail/unknown), constraints(limit,subject,recipient,path), confidence(0~1), ambiguities(list[str])。\n"
            "规则：若信息缺失或冲突，把冲突放入 ambiguities。\n"
            f"用户偏好先验:\n{semantic_hint or '(none)'}\n"
            f"默认值: {json.dumps(defaults, ensure_ascii=False)}\n"
            f"用户输入: {user_input}"
        )
        parsed = self._parse_intent_json(prompt, defaults)
        if not isinstance(parsed, dict):
            return dict(defaults)
        out = dict(defaults)
        out.update({k: parsed.get(k, out[k]) for k in out.keys() if k in parsed})
        c = out.get("constraints", {})
        if not isinstance(c, dict):
            c = {}
        subject, _prefix = self._parse_mail_subject_body(user_input)
        out["constraints"] = {
            "limit": int(c.get("limit", defaults["constraints"]["limit"]) or defaults["constraints"]["limit"]),
            "subject": str(c.get("subject", "") or subject or "").strip(),
            "recipient": str(c.get("recipient", "") or self._extract_email_address(user_input) or "").strip(),
            "recipients": c.get("recipients", []),
            "path": str(c.get("path", "") or self._extract_windows_path(user_input) or self._extract_named_output_file(user_input) or "").strip(),
            "also_save_file": bool(c.get("also_save_file", False)),
            "file_formats": c.get("file_formats", []),
            "local_formats": c.get("local_formats", []),
            "email_formats": c.get("email_formats", []),
            "email_delivery_mode": str(c.get("email_delivery_mode", "combined") or "combined"),
            "transform_conflict": False,
        }
        out["intent"] = str(out.get("intent", "unknown")).lower()
        out["source"] = str(out.get("source", "unknown")).lower()
        out["transform"] = str(out.get("transform", "none")).lower()
        out["target"] = str(out.get("target", "unknown")).lower()
        out["confidence"] = max(0.0, min(float(out.get("confidence", 0.0) or 0.0), 1.0))
        ambiguities = out.get("ambiguities", [])
        out["ambiguities"] = [str(x)[:120] for x in ambiguities] if isinstance(ambiguities, list) else []
        # Deterministic fill from lexical hints as stage-B guard.
        txt = user_input
        low = txt.lower()
        # Heuristics for deterministic post-parsing adjustment.
        if out["source"] == "unknown":
            if any(k in txt for k in ("浏览器", "历史", "记录")) or any(k in low for k in ("chrome", "history")):
                out["source"] = "browser"
            elif any(k in txt for k in ("邮件", "邮箱")) or any(k in low for k in ("mail", "gmail")):
                out["source"] = "mail"
            elif any(k in txt for k in ("文件", ".txt", ".json")):
                out["source"] = "file"
        if out["target"] == "unknown":
            if out["constraints"]["recipient"] or any(k in txt for k in ("发送给", "发给", "发邮件")):
                out["target"] = "mail"
            elif any(k in txt for k in ("写入", "保存", "导出", "文件")):
                out["target"] = "file"
        if out["constraints"]["recipient"] and any(k in txt for k in ("同时", "并且", "也")) and any(
            k in txt for k in ("保存", "写入", "本地", "文件", "命名")
        ):
            out["constraints"]["also_save_file"] = True
        recipients = self._extract_email_addresses(user_input)
        if recipients:
            out["constraints"]["recipients"] = recipients
            if not out["constraints"]["recipient"]:
                out["constraints"]["recipient"] = recipients[0]
        fmt_hits: list[str] = []
        if any(k in low for k in ("json格式", "合法json", "标准json", ".json")) or "json" in low:
            fmt_hits.append("json")
        if any(k in low for k in ("markdown", "md格式", ".md")):
            fmt_hits.append("markdown")
        if any(k in txt for k in ("文本", "纯文本", "txt")) or ".txt" in low:
            fmt_hits.append("text")
        if fmt_hits:
            # de-duplicate while preserving order
            out["constraints"]["file_formats"] = list(dict.fromkeys(fmt_hits))

        # Split formats by scope (local vs email) when possible.
        wants_local = any(k in txt for k in ("本地", "保存到本地", "保留到本地", "本地文件", "本地保存", "保留到文件", "保存到文件"))
        wants_email = bool(
            self._normalize_recipients(
                out["constraints"].get("recipients", []),
                str(out["constraints"].get("recipient", "")).strip(),
            )
        ) or any(k in txt for k in ("发送到", "发送给", "发给", "发邮件"))
        if wants_local and not wants_email:
            out["constraints"]["local_formats"] = out["constraints"]["file_formats"]
        elif wants_email and not wants_local:
            out["constraints"]["email_formats"] = out["constraints"]["file_formats"]
        elif wants_local and wants_email:
            # Heuristic: formats mentioned near "原文/文本" tend to be local; others default to email.
            if any(k in txt for k in ("原文", "文本格式", "纯文本")):
                out["constraints"]["local_formats"] = ["text"]
                out["constraints"]["email_formats"] = out["constraints"]["file_formats"] or ["json", "text"]
            else:
                out["constraints"]["local_formats"] = out["constraints"]["file_formats"] or ["text"]
                out["constraints"]["email_formats"] = out["constraints"]["file_formats"] or ["text"]

        # Backward-compat: if old file_formats exists but split not inferred, default to local when also_save_file.
        if not out["constraints"]["local_formats"] and out["constraints"]["also_save_file"]:
            out["constraints"]["local_formats"] = out["constraints"]["file_formats"] or ["text"]
        if not out["constraints"]["email_formats"] and self._normalize_recipients(
            out["constraints"].get("recipients", []),
            str(out["constraints"].get("recipient", "")).strip(),
        ):
            out["constraints"]["email_formats"] = out["constraints"]["file_formats"] or ["text"]
        if any(k in txt for k in ("分别发送", "分别发给", "每个格式单独发送", "多封邮件", "每个收件人单独发送")):
            out["constraints"]["email_delivery_mode"] = "separate"
        elif any(k in txt for k in ("一封邮件", "合并发送", "放在同一封")):
            out["constraints"]["email_delivery_mode"] = "combined"
        has_summary = any(k in txt for k in ("概括", "总结", "摘要", "逐封", "逐条"))
        has_json = any(k in low for k in ("json格式", "合法json", "标准json", "以json")) or "json" in low
        has_raw = any(k in txt for k in ("原文", "不要概括", "保持原样"))
        if out["transform"] == "none" and has_summary:
            out["transform"] = "summarize"
        if out["transform"] == "none" and has_json:
            out["transform"] = "json"
        # Detect transform conflicts: summarize + json + raw style hints mixed together.
        conflict = has_summary and (has_json or has_raw)
        if conflict:
            out["constraints"]["transform_conflict"] = True
            out["ambiguities"].append(
                "检测到同时包含“概括/摘要”和“JSON/原文”信号，需确认是要“摘要版 JSON”还是“原文 JSON”。"
            )
        return out

    def _plan_from_semantic_slots(self, slots: dict[str, Any]) -> list[dict[str, Any]]:
        c = dict(slots.get("constraints", {}) or {})
        intent = str(slots.get("intent", "unknown"))
        source = str(slots.get("source", "unknown"))
        target = str(slots.get("target", "unknown"))
        transform = str(slots.get("transform", "none"))

        # Direct-read path: for read/summarize intents without explicit sink target,
        # prefer in-chat output instead of forcing file/mail clarification.
        if target == "unknown" and intent in ("read", "summarize") and source in ("browser", "mail", "file"):
            direct_actions: list[str] = []
            if source == "browser":
                direct_actions.append("recent_history")
            elif source == "mail":
                direct_actions.append("summarize_recent" if transform == "summarize" else "list_recent")
            elif source == "file":
                direct_actions.append("read_file")
            if transform == "summarize" and source in ("browser", "file"):
                direct_actions.append("summarize_text")
            elif transform == "json":
                direct_actions.append("format_json_text")

            steps: list[dict[str, Any]] = []
            for action in direct_actions:
                route = str(self._get_action_spec(action).get("route", "")).strip()
                if not route:
                    return []
                args = self._build_step_args(action, source, target, transform, c)
                if args is None:
                    self._last_path_search_debug["slots"] = slots
                    self._last_path_search_debug["reason"] = f"args_build_failed:{action}"
                    return []
                steps.append({"route": route, "action": action, "args": args})
            if steps:
                self._last_path_search_debug["slots"] = slots
                self._last_path_search_debug["reason"] = "ok_direct_read"
                self._last_path_search_debug["selected_path"] = [str(s["action"]) for s in steps]
                return steps

        requested_targets = self._derive_requested_targets(target, c)
        primary_target = requested_targets[0] if requested_targets else target
        action_path = self._find_shortest_action_path(source, primary_target, transform, c)
        if not action_path:
            self._last_path_search_debug["slots"] = slots
            self._last_path_search_debug["reason"] = "no_path_found"
            return []
        # Multi-target extension: dynamically append additional sinks required by one utterance.
        for sink in requested_targets[1:]:
            sink_action = "send_email" if sink == "mail" else ("write_file" if sink == "file" else "")
            if sink_action and sink_action not in action_path:
                action_path = list(action_path) + [sink_action]
        if len(requested_targets) > 1:
            self._last_path_search_debug["selected_path"] = list(action_path)
            self._last_path_search_debug["reason"] = "ok_multi_target_dynamic"
        steps: list[dict[str, Any]] = []
        pending_write_formats = self._normalize_file_formats(c.get("local_formats", []))
        write_seq = 0
        for action in action_path:
            route = str(self._get_action_spec(action).get("route", "")).strip()
            if not route:
                return []
            args = self._build_step_args(
                action,
                source,
                primary_target,
                transform,
                c,
                write_format=(pending_write_formats[0] if (action == "write_file" and pending_write_formats) else None),
                write_seq=write_seq,
            )
            if args is None:
                self._last_path_search_debug["slots"] = slots
                self._last_path_search_debug["reason"] = f"args_build_failed:{action}"
                return []
            steps.append({"route": route, "action": action, "args": args})
            if action == "write_file":
                write_seq += 1
                # Local multi-format: only expand when user explicitly asked local multi-format.
                extra_formats = pending_write_formats[1:] if pending_write_formats else []
                for fmt in extra_formats:
                    extra_args = self._build_step_args(
                        "write_file",
                        source,
                        "file",
                        transform,
                        c,
                        write_format=fmt,
                        write_seq=write_seq,
                    )
                    if extra_args is None:
                        continue
                    steps.append({"route": "file", "action": "write_file", "args": extra_args})
                    write_seq += 1
                pending_write_formats = []
        self._last_path_search_debug["slots"] = slots
        self._last_path_search_debug["reason"] = "ok"
        return steps

    @staticmethod
    def _normalize_file_formats(raw_formats: Any) -> list[str]:
        if not isinstance(raw_formats, list):
            return []
        out: list[str] = []
        mapping = {
            "json": "json",
            "md": "markdown",
            "markdown": "markdown",
            "txt": "text",
            "text": "text",
            "plaintext": "text",
        }
        for item in raw_formats:
            key = str(item).strip().lower()
            v = mapping.get(key)
            if v and v not in out:
                out.append(v)
        return out

    @staticmethod
    def _extract_email_addresses(text: str) -> list[str]:
        matches = re.findall(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", text or "")
        out: list[str] = []
        for item in matches:
            if item not in out:
                out.append(item)
        return out

    @staticmethod
    def _normalize_recipients(raw: Any, fallback: str = "") -> list[str]:
        out: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                v = str(item).strip()
                if v and v not in out:
                    out.append(v)
        fb = (fallback or "").strip()
        if fb and fb not in out:
            out.append(fb)
        return out

    @staticmethod
    def _derive_requested_targets(target: str, constraints: dict[str, Any]) -> list[str]:
        out: list[str] = []
        if target in ("mail", "file"):
            out.append(target)
        has_mail_target = bool(
            PlanPathMixin._normalize_recipients(
                constraints.get("recipients", []),
                str(constraints.get("recipient", "")).strip(),
            )
        )
        if has_mail_target and "mail" not in out:
            out.append("mail")
        if bool(constraints.get("also_save_file", False)) and "file" not in out:
            out.append("file")
        if not out and str(constraints.get("path", "")).strip():
            out.append("file")
        return out

    def _find_shortest_action_path(
        self,
        source: str,
        target: str,
        transform: str,
        constraints: dict[str, Any],
    ) -> list[str]:
        source_node = {
            "browser": "source_browser",
            "mail": "source_mail",
            "file": "source_file",
        }.get(source, "")
        target_sink = {
            "mail": "sink_mail",
            "file": "sink_file",
        }.get(target, "")
        if not source_node or not target_sink:
            self._last_path_search_debug = {
                "slots": {},
                "source_node": source_node,
                "target_sink": target_sink,
                "required_summary": transform == "summarize",
                "candidate_paths": [],
                "selected_path": [],
                "reason": "invalid_source_or_target",
            }
            return []
        # Quick feasibility checks for terminal constraints.
        recipients = self._normalize_recipients(
            constraints.get("recipients", []),
            str(constraints.get("recipient", "")).strip(),
        )
        if target == "mail" and not recipients:
            self._last_path_search_debug = {
                "slots": {},
                "source_node": source_node,
                "target_sink": target_sink,
                "required_summary": transform == "summarize",
                "candidate_paths": [],
                "selected_path": [],
                "reason": "missing_recipient",
            }
            return []
        required_summary = transform == "summarize"
        required_json = transform == "json"

        queue: deque[tuple[str, list[str]]] = deque([(source_node, [])])
        visited: set[tuple[str, tuple[str, ...]]] = set()
        candidate_paths: list[list[str]] = []

        def _type_match(node_type: str, action_input: str) -> bool:
            if action_input == "text_any":
                return node_type.startswith("text_")
            return node_type == action_input

        while queue:
            node_type, path = queue.popleft()
            state_key = (node_type, tuple(path[-2:]))
            if state_key in visited:
                continue
            visited.add(state_key)
            if len(path) > 6:
                continue
            for action, spec in self._all_action_specs().items():
                in_type = str(spec.get("input_type", ""))
                out_type = str(spec.get("output_type", ""))
                if not in_type or not out_type:
                    continue
                if not _type_match(node_type, in_type):
                    continue
                # Prevent repeated same action loops.
                if path and path[-1] == action:
                    continue
                new_path = path + [action]
                if out_type == target_sink:
                    candidate_paths.append(list(new_path))
                    has_summary = any(a in ("summarize_recent", "summarize_text") for a in new_path)
                    has_json = any(a == "format_json_text" for a in new_path)
                    summary_ok = (not required_summary) or has_summary
                    json_ok = (not required_json) or has_json
                    if summary_ok and json_ok:
                        self._last_path_search_debug = {
                            "slots": {},
                            "source_node": source_node,
                            "target_sink": target_sink,
                            "required_summary": required_summary,
                            "candidate_paths": [p for p in candidate_paths[:8]],
                            "selected_path": list(new_path),
                            "reason": "ok",
                        }
                        return new_path
                queue.append((out_type, new_path))
        self._last_path_search_debug = {
            "slots": {},
            "source_node": source_node,
            "target_sink": target_sink,
            "required_summary": required_summary,
            "candidate_paths": [p for p in candidate_paths[:8]],
            "selected_path": [],
            "reason": "no_feasible_path",
        }
        return []

    def path_search_debug_preview(self) -> str:
        dbg = dict(getattr(self, "_last_path_search_debug", {}) or {})
        slots = dbg.get("slots", {})
        lines = [
            "[Path Search Debug]",
            "mode=runtime_bfs",
            f"source_node={dbg.get('source_node', '') or '(unknown)'}",
            f"target_sink={dbg.get('target_sink', '') or '(unknown)'}",
            f"required_summary={bool(dbg.get('required_summary', False))}",
            f"reason={dbg.get('reason', 'n/a')}",
        ]
        if isinstance(slots, dict) and slots:
            lines.append(
                "semantic_slots="
                + json.dumps(
                    {
                        "intent": slots.get("intent"),
                        "source": slots.get("source"),
                        "transform": slots.get("transform"),
                        "target": slots.get("target"),
                    },
                    ensure_ascii=False,
                )
            )
        candidates = dbg.get("candidate_paths", [])
        if isinstance(candidates, list) and candidates:
            lines.append(f"candidate_paths({len(candidates)}):")
            for p in candidates:
                if isinstance(p, list) and p:
                    lines.append(f"  - {' -> '.join(str(x) for x in p)}")
        else:
            lines.append("candidate_paths(0): (none)")
        selected = dbg.get("selected_path", [])
        if isinstance(selected, list) and selected:
            lines.append(f"selected_shortest: {' -> '.join(str(x) for x in selected)}")
        else:
            lines.append("selected_shortest: (none)")
        return "\n".join(lines)

    def _build_step_args(
        self,
        action: str,
        source: str,
        target: str,
        transform: str,
        constraints: dict[str, Any],
        write_format: str | None = None,
        write_seq: int = 0,
    ) -> dict[str, Any] | None:
        limit = max(1, min(int(constraints.get("limit", 5) or 5), 50))
        if action in ("recent_history", "list_recent", "summarize_recent"):
            return {"limit": limit}
        if action == "read_file":
            fp = str(constraints.get("path", "")).strip() or self._resolve_followup_file_path("")
            if not fp:
                return None
            return {"file_path": fp}
        if action == "write_file":
            path = str(constraints.get("path", "")).strip()
            if not path:
                path = self._default_export_path(source, "dynamic_export.txt")
            fmt = (write_format or "").strip().lower()
            if not fmt:
                fmts = self._normalize_file_formats(constraints.get("file_formats", []))
                fmt = fmts[0] if fmts else ""
            ext_map = {"json": ".json", "markdown": ".md", "text": ".txt"}
            ext = ext_map.get(fmt, "")
            if ext:
                p = str(path)
                lp = p.lower()
                if not lp.endswith((".json", ".md", ".txt")):
                    p = p + ext
                elif not lp.endswith(ext):
                    p = p.rsplit(".", 1)[0] + ext
                if write_seq > 0:
                    base = p.rsplit(".", 1)[0]
                    p = f"{base}_{fmt}{ext}"
                path = p
            return {"file_path": path, "format": (fmt or "text")}
        if action == "send_email":
            recipients = self._normalize_recipients(
                constraints.get("recipients", []),
                str(constraints.get("recipient", "")).strip(),
            )
            if not recipients:
                return None
            subject = str(constraints.get("subject", "")).strip()
            if not subject:
                if source == "browser":
                    subject = "浏览器记录"
                elif source == "file":
                    subject = "文件内容"
                elif transform == "summarize":
                    subject = "内容摘要"
                else:
                    subject = "自动发送"
            include_formats = self._normalize_file_formats(constraints.get("email_formats", [])) or ["text"]
            delivery_mode = str(constraints.get("email_delivery_mode", "combined") or "combined").strip().lower()
            if delivery_mode not in ("combined", "separate"):
                delivery_mode = "combined"
            return {
                "to": recipients[0],
                "recipients": recipients,
                "subject": subject,
                "prefix": "",
                "include_formats": include_formats,
                "delivery_mode": delivery_mode,
            }
        if action == "summarize_text":
            return {}
        if action == "format_json_text":
            return {}
        return {}

    def _minimal_clarify_question(self, slots: dict[str, Any]) -> str:
        if not slots:
            return ""
        c = dict(slots.get("constraints", {}) or {})
        intent = str(slots.get("intent", "unknown"))
        target = str(slots.get("target", "unknown"))
        source = str(slots.get("source", "unknown"))
        recipients = self._normalize_recipients(
            c.get("recipients", []),
            str(c.get("recipient", "")).strip(),
        )
        if bool(c.get("transform_conflict", False)):
            return "你更希望哪种输出？A) 摘要版 JSON（先概括再转为 JSON）B) 原文 JSON（保持原始内容结构，仅包装为 JSON）"
        if target == "unknown" and intent in ("read", "summarize") and source in ("browser", "mail"):
            return ""
        if target == "unknown":
            return "你希望输出到哪里？A) 文件 B) 邮件"
        if target == "mail" and not recipients:
            return "请提供收件人邮箱地址。"
        if source == "unknown":
            return "你的数据来源是？A) 浏览器记录 B) 邮件 C) 文件"
        return ""

    @staticmethod
    def _plan_preview(plan: dict[str, object]) -> str:
        steps = plan.get("steps", [])
        lines = [
            f"任务：{plan.get('summary', '')}",
            f"风险：{plan.get('risk', 'unknown')}",
            "执行链路：",
        ]
        for i, s in enumerate(steps, start=1):
            lines.append(f"{i}) {s.get('route')} -> {s.get('action')} args={s.get('args')}")
        lines.append("\n回复：确认执行 / 修改为 ... / 取消")
        return "\n".join(lines)
