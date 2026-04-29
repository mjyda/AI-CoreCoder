from __future__ import annotations

from typing import Any


class PlanCoreMixin:
    @staticmethod
    def _is_smalltalk_input(user_input: str) -> bool:
        text = (user_input or "").strip().lower()
        if not text:
            return True
        greetings = (
            "你好",
            "您好",
            "hello",
            "hi",
            "hey",
            "早上好",
            "中午好",
            "晚上好",
            "在吗",
            "在不在",
        )
        return any(text == g or text.startswith(g + " ") for g in greetings)

    @staticmethod
    def _has_task_signal(user_input: str) -> bool:
        text = (user_input or "").strip().lower()
        if not text:
            return False
        task_keywords = (
            "读取",
            "导出",
            "发送",
            "写入",
            "保存",
            "总结",
            "概括",
            "分析",
            "查找",
            "搜索",
            "邮件",
            "文件",
            "浏览器",
            "history",
            "email",
            "json",
            ".txt",
            ".json",
            "http://",
            "https://",
        )
        return any(k in text for k in task_keywords)

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

    def _all_action_specs(self) -> dict[str, dict[str, Any]]:
        specs = dict(self._action_specs)
        if hasattr(self, "_temp_registry"):
            specs.update(self._temp_registry.action_specs)
        return specs

    def _get_action_spec(self, action: str) -> dict[str, Any]:
        return dict(self._all_action_specs().get(action, {}))

    def _get_step_executor(self, action: str):
        executor = self._step_executor_registry.get(action)
        if executor is not None:
            return executor
        if hasattr(self, "_temp_registry"):
            return self._temp_registry.step_executors.get(action)
        return None

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
        # Guardrail: avoid asking semantic clarification for small talk.
        if self._is_smalltalk_input(user_input):
            return None
        slots = self._parse_semantic_slots(user_input)
        if (
            str(slots.get("intent", "unknown")) == "unknown"
            and float(slots.get("confidence", 0.0) or 0.0) < 0.55
            and not self._has_task_signal(user_input)
        ):
            return None
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
