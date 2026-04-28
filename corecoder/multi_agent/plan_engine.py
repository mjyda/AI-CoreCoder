from __future__ import annotations

import json
from collections import deque
import re
from typing import Any

from ..tools.sandbox import sandbox_path


class ExecutionPlanMixin:
    def _init_execution_plan_engine(self) -> None:
        """Initialize planner registry and centralized risk/confirmation policy table."""
        self._plan_registry = [
            ("dynamic_orchestration", self._plan_dynamic_orchestration),
        ]
        self._plan_risk_policy = {
            "low": {"requires_confirmation": False},
            "medium": {"requires_confirmation": True},
            "high": {"requires_confirmation": True},
        }
        self._last_path_search_debug: dict[str, Any] = {
            "slots": {},
            "source_node": "",
            "target_sink": "",
            "required_summary": False,
            "candidate_paths": [],
            "selected_path": [],
            "reason": "init",
        }
        self._pending_semantic_clarify: dict[str, Any] | None = None
        self._step_executor_registry = {
            "recent_history": self._exec_step_recent_history,
            "list_recent": self._exec_step_list_recent,
            "summarize_recent": self._exec_step_summarize_recent,
            "summarize_text": self._exec_step_summarize_text,
            "format_json_text": self._exec_step_format_json_text,
            "read_file": self._exec_step_read_file,
            "write_file": self._exec_step_write_file,
            "send_email": self._exec_step_send_email,
        }
        # Capability graph metadata: action contracts + side effects.
        self._action_specs = {
            "recent_history": {
                "input_schema": {"limit": "int"},
                "output_schema": "browser_history_text",
                "side_effect_level": "none",
                "route": "browser",
                "input_type": "source_browser",
                "output_type": "text_browser",
            },
            "list_recent": {
                "input_schema": {"limit": "int"},
                "output_schema": "mail_list_text",
                "side_effect_level": "none",
                "route": "mail",
                "input_type": "source_mail",
                "output_type": "text_mail_list",
            },
            "summarize_recent": {
                "input_schema": {"limit": "int"},
                "output_schema": "mail_summary_text",
                "side_effect_level": "none",
                "route": "mail",
                "input_type": "source_mail",
                "output_type": "text_summary",
            },
            "summarize_text": {
                "input_schema": {},
                "output_schema": "generic_summary_text",
                "side_effect_level": "none",
                "route": "file",
                "input_type": "text_any",
                "output_type": "text_summary",
            },
            "format_json_text": {
                "input_schema": {},
                "output_schema": "generic_json_text",
                "side_effect_level": "none",
                "route": "file",
                "input_type": "text_any",
                "output_type": "text_json",
            },
            "read_file": {
                "input_schema": {"file_path": "str"},
                "output_schema": "file_text",
                "side_effect_level": "none",
                "route": "file",
                "input_type": "source_file",
                "output_type": "text_file",
            },
            "write_file": {
                "input_schema": {"file_path": "str"},
                "output_schema": "write_result",
                "side_effect_level": "write",
                "route": "file",
                "input_type": "text_any",
                "output_type": "sink_file",
            },
            "send_email": {
                "input_schema": {"to": "str", "subject": "str"},
                "output_schema": "send_result",
                "side_effect_level": "external_send",
                "route": "mail",
                "input_type": "text_any",
                "output_type": "sink_mail",
            },
        }

    @staticmethod
    def _default_export_path(source: str, filename: str) -> str:
        base_name = {
            "mail": "mail_export.txt",
            "browser": "browser_export.txt",
        }.get(source, filename)
        return str(sandbox_path(base_name))

    @staticmethod
    def _is_confirm_text(text: str) -> bool:
        return any(k in text for k in ("确认", "确认执行", "开始执行", "yes", "ok", "执行吧"))

    @staticmethod
    def _is_cancel_text(text: str) -> bool:
        return any(k in text for k in ("取消", "撤销", "不用了", "算了", "no"))

    @staticmethod
    def _extract_modify_instruction(text: str) -> str:
        for kw in ("修改为", "改成", "改为", "调整为", "修改:"):
            idx = text.find(kw)
            if idx >= 0:
                return text[idx + len(kw) :].strip()
        return ""

    def _handle_pending_execution_plan(self, user_input: str) -> str | None:
        if self._pending_execution_plan is None:
            return None
        text = user_input.strip()
        if not text:
            return None
        if self._is_cancel_text(text):
            self._pending_execution_plan = None
            return self._format_evidence_result(
                summary="已取消待执行草案。",
                route_key="file",
                evidence_lines=["pending_plan=cleared"],
                detail="未执行任何副作用操作。",
            )
        if self._is_confirm_text(text):
            plan = self._pending_execution_plan
            self._pending_execution_plan = None
            return self._execute_execution_plan(plan)
        mod = self._extract_modify_instruction(text)
        if mod:
            rebuilt = self._build_execution_plan(mod)
            if rebuilt is None:
                return self._format_evidence_result(
                    summary="收到修改请求，但无法根据修改内容重建草案。",
                    route_key="file",
                    evidence_lines=[f"modify={mod!r}"],
                    detail="请更具体，例如：改为发送最近5条浏览记录到 xxx@qq.com，主题为xxx。",
                )
            self._pending_execution_plan = rebuilt
            return self._format_evidence_result(
                summary="已根据你的修改更新执行草案，请确认。",
                route_key="file",
                evidence_lines=[f"plan={rebuilt}"],
                detail=self._plan_preview(rebuilt),
            )
        return self._format_evidence_result(
            summary="当前有待执行草案，请先确认、修改或取消。",
            route_key="file",
            evidence_lines=[f"plan={self._pending_execution_plan}"],
            detail="回复：确认执行 / 修改为 ... / 取消",
        )

    def _handle_pending_semantic_clarify(self, user_input: str) -> str | None:
        pending = self._pending_semantic_clarify
        if pending is None:
            return None
        text = (user_input or "").strip()
        if not text:
            return None
        if self._is_cancel_text(text):
            self._pending_semantic_clarify = None
            return self._format_evidence_result(
                summary="已取消当前语义澄清。",
                route_key="file",
                evidence_lines=["pending_semantic_clarify=cleared"],
                detail="未继续生成执行草案。",
            )

        slots = dict(pending.get("semantic_slots", {}) or {})
        constraints = dict(slots.get("constraints", {}) or {})
        low = text.lower()

        if bool(constraints.get("transform_conflict", False)):
            if text in ("A", "a", "选择A", "选A") or "摘要版" in text:
                slots["transform"] = "summarize"
                constraints["transform_conflict"] = False
            elif text in ("B", "b", "选择B", "选B") or "原文json" in low or "原文 json" in low:
                slots["transform"] = "json"
                constraints["transform_conflict"] = False
            else:
                return self._format_evidence_result(
                    summary="当前还在等待你选择冲突格式。",
                    route_key="file",
                    evidence_lines=[f"semantic_slots={slots}"],
                    detail="请选择：A) 摘要版 JSON  B) 原文 JSON",
                )

        if str(slots.get("target", "unknown")) == "unknown":
            if text in ("A", "a", "文件") or "文件" in text:
                slots["target"] = "file"
            elif text in ("B", "b", "邮件") or "邮件" in text:
                slots["target"] = "mail"

        if str(slots.get("source", "unknown")) == "unknown":
            if text in ("A", "a") or "浏览器" in text:
                slots["source"] = "browser"
            elif text in ("B", "b") or "邮件" in text:
                slots["source"] = "mail"
            elif text in ("C", "c") or "文件" in text:
                slots["source"] = "file"

        slots["constraints"] = constraints
        self._pending_semantic_clarify = None
        steps = self._plan_from_semantic_slots(slots)
        if not steps:
            q = self._minimal_clarify_question(slots)
            if q:
                self._pending_semantic_clarify = {
                    "semantic_slots": slots,
                    "clarify_question": q,
                }
                return self._format_evidence_result(
                    summary="我已理解大方向，但还差一个关键槽位。",
                    route_key="file",
                    evidence_lines=[f"semantic_slots={slots}"],
                    detail=q,
                )
            return None
        actions = [str(s.get("action", "")) for s in steps]
        plan = self._apply_plan_policy(
            {
                "name": f"dynamic_{'_to_'.join(actions)}",
                "summary": f"按语义能力图执行：{' -> '.join(actions)}",
                "semantic_slots": slots,
                "steps": steps,
                "_user_input": str(pending.get("_user_input", "")),
            }
        )
        if not bool(plan.get("requires_confirmation", True)):
            return self._execute_execution_plan(plan)
        self._pending_execution_plan = plan
        return self._format_evidence_result(
            summary="已根据你的澄清补全执行草案，请确认后执行。",
            route_key="file",
            evidence_lines=[f"plan={plan}"],
            detail=self._plan_preview(plan),
        )

    def _maybe_stage_execution_plan(self, user_input: str) -> str | None:
        plan = self._build_execution_plan(user_input)
        if plan is None:
            return None
        if str(plan.get("name", "")) == "semantic_clarify":
            self._pending_semantic_clarify = {
                "semantic_slots": dict(plan.get("semantic_slots", {}) or {}),
                "clarify_question": str(plan.get("clarify_question", "")),
                "_user_input": user_input,
            }
            return self._format_evidence_result(
                summary="我已理解大方向，但还差一个关键槽位。",
                route_key="file",
                evidence_lines=[f"semantic_slots={plan.get('semantic_slots', {})}"],
                detail=str(plan.get("clarify_question", "请补充关键信息。")),
            )
        if not bool(plan.get("requires_confirmation", True)):
            return self._execute_execution_plan(plan)
        self._pending_execution_plan = plan
        return self._format_evidence_result(
            summary="我已生成执行草案，请确认后执行。",
            route_key="file",
            evidence_lines=[f"plan={plan}"],
            detail=self._plan_preview(plan),
        )

    def _build_execution_plan(self, user_input: str) -> dict[str, object] | None:
        for planner_name, planner in self._plan_registry:
            plan = planner(user_input)
            if plan is None:
                continue
            plan.setdefault("name", planner_name)
            plan["_user_input"] = user_input
            return self._apply_plan_policy(plan)
        return None

    def _apply_plan_policy(self, plan: dict[str, object]) -> dict[str, object]:
        risk = str(plan.get("risk", "")).strip().lower()
        if risk not in self._plan_risk_policy:
            risk = self._infer_plan_risk(plan)
        policy = self._plan_risk_policy.get(risk, {"requires_confirmation": True})
        merged = dict(plan)
        merged["risk"] = risk
        merged["requires_confirmation"] = bool(policy.get("requires_confirmation", True))
        return merged

    def _infer_plan_risk(self, plan: dict[str, object]) -> str:
        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            return "high"
        actions = {str(s.get("action", "")) for s in steps if isinstance(s, dict)}
        if {"send_email", "delete", "overwrite"} & actions:
            return "high"
        if {"write_file", "move_to_trash"} & actions:
            return "medium"
        return "low"

    def _plan_dynamic_orchestration(self, user_input: str) -> dict[str, object] | None:
        slots = self._parse_semantic_slots(user_input)
        steps = self._plan_from_semantic_slots(slots)
        if not steps:
            q = self._minimal_clarify_question(slots)
            if q:
                return {
                    "name": "semantic_clarify",
                    "summary": "语义解析存在歧义，先澄清一个关键问题",
                    "risk": "low",
                    "requires_confirmation": False,
                    "steps": [],
                    "clarify_question": q,
                    "semantic_slots": slots,
                }
            return None

        actions = [str(s.get("action", "")) for s in steps]
        summary = " -> ".join(actions)
        return {
            "name": f"dynamic_{'_to_'.join(actions)}",
            "summary": f"按语义能力图执行：{summary}",
            "semantic_slots": slots,
            "steps": steps,
        }

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
        source = str(slots.get("source", "unknown"))
        target = str(slots.get("target", "unknown"))
        transform = str(slots.get("transform", "none"))
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
            route = str(self._action_specs.get(action, {}).get("route", "")).strip()
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
            ExecutionPlanMixin._normalize_recipients(
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
            for action, spec in self._action_specs.items():
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
        target = str(slots.get("target", "unknown"))
        source = str(slots.get("source", "unknown"))
        recipients = self._normalize_recipients(
            c.get("recipients", []),
            str(c.get("recipient", "")).strip(),
        )
        if bool(c.get("transform_conflict", False)):
            return "你更希望哪种输出？A) 摘要版 JSON（先概括再转为 JSON）B) 原文 JSON（保持原始内容结构，仅包装为 JSON）"
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

    def _execute_execution_plan(self, plan: dict[str, object]) -> str:
        name = str(plan.get("name", ""))
        steps = plan.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return "Error: invalid execution plan."

        evidence: list[str] = [f"plan={name}"]
        intermediate: dict[str, str] = {}

        for s in steps:
            action = str(s.get("action", ""))
            executor = self._step_executor_registry.get(action)
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
        return self._format_evidence_result(
            summary="执行草案已完成。",
            route_key="file",
            evidence_lines=evidence,
            detail="\n\n".join(v for v in (intermediate.get("write_output"), intermediate.get("send_output")) if v) or "Done.",
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
