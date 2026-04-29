from __future__ import annotations

import json

from ...agent import Agent
from .shared import AssistantState, EXPERTS, route_intent

try:
    from langgraph.graph import END, StateGraph
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    END = "__end__"
    StateGraph = None


class SupervisorFlowMixin:
    def run(self, user_input: str) -> str:
        temp_intent = self._classify_temp_capability_intent(user_input)
        toolsmith_enabled = bool(getattr(self, "_coding_toolsmith_enabled", False))
        text_low = (user_input or "").strip().lower()
        if toolsmith_enabled and (text_low == "temp-retry" or text_low.startswith("temp-retry ")):
            arg = text_low[len("temp-retry") :].strip()
            rounds = 1
            customization = ""
            if arg:
                parts = arg.split(maxsplit=1)
                try:
                    rounds = max(1, min(int(parts[0]), 5))
                    customization = parts[1].strip() if len(parts) > 1 else ""
                except ValueError:
                    rounds = 1
                    customization = arg
            return self.retry_last_temp_failure(rounds, customization, iteration_mode="fix")
        if toolsmith_enabled and (text_low == "temp-evolve" or text_low.startswith("temp-evolve ")):
            arg = text_low[len("temp-evolve") :].strip()
            rounds = 1
            customization = ""
            if arg:
                parts = arg.split(maxsplit=1)
                try:
                    rounds = max(1, min(int(parts[0]), 5))
                    customization = parts[1].strip() if len(parts) > 1 else ""
                except ValueError:
                    rounds = 1
                    customization = arg
            return self.retry_last_temp_failure(rounds, customization, iteration_mode="evolve")
        if toolsmith_enabled:
            # Runtime dialog (open/execute temp capabilities UX) has higher priority
            # than "create temp capability" pending flow.
            temp_runtime_dialog = getattr(self, "_handle_pending_temp_capability_dialog", None)
            if callable(temp_runtime_dialog):
                dialog_followup = temp_runtime_dialog(user_input)
                if dialog_followup is not None:
                    return dialog_followup
            temp_followup = self._handle_pending_temp_capability(user_input)
            if temp_followup is not None:
                return temp_followup
        elif self._pending_temp_capability is not None:
            self._pending_temp_capability = None
        temp_query = self._maybe_run_temp_capability_query(user_input)
        if temp_query is not None:
            return temp_query
        if (
            not toolsmith_enabled
            and str(temp_intent.get("intent", "normal_task")) == "create_temp_capability"
            and float(temp_intent.get("confidence", 0.0) or 0.0) >= 0.75
        ):
            return self._format_evidence_result(
                summary="编程专家临时工具生成功能当前处于关闭状态。",
                route_key="coding",
                evidence_lines=[f"intent_decision={temp_intent}", "coding_toolsmith=off"],
                detail="如需创建临时工具，请先在 CLI 输入：/coding-toolsmith on",
            )
        if (
            str(temp_intent.get("intent", "normal_task")) == "create_temp_capability"
            and float(temp_intent.get("confidence", 0.0) or 0.0) >= 0.75
            and self._pending_semantic_clarify is not None
        ):
            self._pending_semantic_clarify = None
        clarify_followup = self._handle_pending_semantic_clarify(user_input)
        if clarify_followup is not None:
            return clarify_followup
        plan_followup = self._handle_pending_execution_plan(user_input)
        if plan_followup is not None:
            return plan_followup
        if toolsmith_enabled:
            temp_stage = self._maybe_stage_temp_capability(user_input)
            if temp_stage is not None:
                return temp_stage
        stage = self._maybe_stage_execution_plan(user_input)
        if stage is not None:
            return stage
        cross = self._run_cross_expert_task(user_input)
        if cross is not None:
            return cross
        clarify = self._maybe_ask_route_clarification(user_input)
        if clarify:
            return clarify
        state: AssistantState = {
            "user_input": user_input,
            "route": "coding",
            "outputs": {},
            "final_response": "",
        }
        if self._graph is not None:
            result = self._graph.invoke(state)
            return result["final_response"]
        return self._run_fallback(state)

    def _build_expert(self, profile: ExpertProfile) -> Agent:
        tools = self.mcp_registry.tools_for_capabilities(list(profile.capabilities))
        return Agent(llm=self.llm, tools=tools, skills=[])

    def _run_fallback(self, state: AssistantState) -> str:
        state["route"] = self._decide_route_with_confidence(state["user_input"])["route"]
        result = self._run_route_task(state["route"], state["user_input"])
        state["outputs"][state["route"]] = result
        state["final_response"] = self._format_supervisor_response(state["route"], result)
        self._record_session_turn(state["user_input"], state["route"], result)
        return state["final_response"]

    def _build_graph(self):
        graph = StateGraph(AssistantState)
        graph.add_node("supervisor", self._supervisor_node)
        for profile in EXPERTS:
            graph.add_node(profile.route_key, self._make_expert_node(profile.route_key))
        graph.add_node("finalize", self._finalize_node)

        graph.set_entry_point("supervisor")
        for profile in EXPERTS:
            graph.add_edge(profile.route_key, "finalize")
        graph.add_conditional_edges(
            "supervisor",
            lambda state: state["route"],
            {profile.route_key: profile.route_key for profile in EXPERTS},
        )
        graph.add_edge("finalize", END)
        return graph.compile()

    def _supervisor_node(self, state: AssistantState) -> AssistantState:
        state["route"] = self._decide_route_with_confidence(state["user_input"])["route"]
        return state

    def _parse_route_intent(self, user_input: str) -> dict:
        schema = {
            "route": "unknown",
            "confidence": 0.0,
            "needs_clarification": False,
            "reason": "",
        }
        prompt = (
            "你是主管路由意图解析器。把用户输入解析为JSON，仅返回JSON。\n"
            "字段：route(mail/browser/file/coding/unknown), confidence(0~1), needs_clarification(bool), reason。\n"
            "规则：\n"
            "1) 邮件/Gmail -> mail；浏览记录/网页历史/网址 -> browser；文件读写整理/格式转换 -> file；其余代码开发 -> coding。\n"
            "2) 当表达含糊且可能跨两个及以上专家时，needs_clarification=true。\n"
            f"默认值: {json.dumps(schema, ensure_ascii=False)}\n"
            f"用户输入: {user_input}"
        )
        parsed = self._parse_intent_json(prompt, schema)
        route = str(parsed.get("route", "unknown")).strip().lower()
        if route not in ("mail", "browser", "file", "coding", "unknown"):
            route = "unknown"
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        conf = max(0.0, min(conf, 1.0))
        return {
            "route": route,
            "confidence": conf,
            "needs_clarification": bool(parsed.get("needs_clarification", False)),
            "reason": str(parsed.get("reason", "")).strip(),
        }

    def _decide_route_with_confidence(self, user_input: str) -> dict[str, object]:
        parsed = self._parse_route_intent(user_input)
        fallback = route_intent(user_input, self.context)
        text = user_input.lower()
        signals = {
            "mail": any(k in text for k in ("邮件", "gmail", "mail")),
            "browser": any(k in text for k in ("浏览", "chrome", "history", "网页", "链接", "站点", "记录", "网址")),
            "file": any(k in user_input for k in ("文件", "读取", "写入", "保存", "覆盖", "格式", "json", "文本", ".json", ".txt")),
            "coding": any(k in text for k in ("代码", "函数", "编程", "bug", "修复", "重构", "python", "java", "typescript", "git")),
        }
        active = [k for k, v in signals.items() if v]
        route = parsed["route"] if parsed["route"] != "unknown" else fallback
        conf = float(parsed["confidence"])
        if route == "unknown":
            route = fallback
            conf = 0.5
        # deterministic guardrail: when parser and fallback conflict, trust fallback on low confidence
        if route != fallback and conf < 0.78:
            route = fallback
            conf = max(conf, 0.68)
        needs = bool(parsed["needs_clarification"])
        if len(active) >= 2 and conf < 0.72:
            needs = True
        return {
            "route": route,
            "confidence": conf,
            "needs_clarification": needs,
            "reason": parsed["reason"],
            "signals": active,
            "fallback_route": fallback,
        }

    def _maybe_ask_route_clarification(self, user_input: str) -> str | None:
        decision = self._decide_route_with_confidence(user_input)
        if not decision.get("needs_clarification", False):
            return None
        if float(decision.get("confidence", 0.0)) >= 0.72:
            return None
        return self._format_evidence_result(
            summary="检测到跨专家意图，先确认你的目标再执行，避免误解。",
            route_key="file",
            evidence_lines=[
                f"route_decision={decision}",
                f"context_last_route={self.context.last_route!r}",
            ],
            detail=(
                "请回复一个选项：\n"
                "A) 浏览器任务（查/导出浏览记录）\n"
                "B) 邮件任务（发送/回复/删除）\n"
                "C) 文件任务（读写、格式转换、覆盖）\n"
                "D) 编程任务（改代码/调试）\n"
                "也可以直接说：'把浏览器最近3条发送到 xx@qq.com，主题 xx'。"
            ),
        )

    def _run_cross_expert_task(self, user_input: str) -> str | None:
        """
        Deterministic bridge for high-frequency multi-expert intent:
        browser-history -> email send.
        """
        text = user_input.strip()
        lower = text.lower()
        has_browser = any(k in text for k in ("浏览器", "历史", "记录")) or any(k in lower for k in ("chrome", "history"))
        has_send_mail = any(k in text for k in ("发送给", "发给", "发送到", "发邮件给", "邮箱")) or "send" in lower
        to = self._extract_email_address(text)
        if not (has_browser and has_send_mail and to):
            return None

        browser_expert = self._experts.get("browser")
        mail_expert = self._experts.get("mail")
        if browser_expert is None or mail_expert is None:
            return None
        btool = next((t for t in browser_expert.tools if t.name == "browser_history"), None)
        send_tool = next((t for t in mail_expert.tools if t.name == "gmail_send_email"), None)
        if btool is None or send_tool is None:
            return None

        limit = self._extract_result_limit(text, default=3)
        raw = btool.execute(query="", limit=limit, strict_domain="")
        if raw.startswith("Error:") or raw.startswith("No "):
            return self._format_evidence_result(
                summary="跨专家任务触发成功，但浏览记录读取失败。",
                route_key="browser",
                evidence_lines=[f"tool=browser_history args={{'query': '', 'limit': {limit}, 'strict_domain': ''}}"],
                detail=raw,
            )
        subject, body_hint = self._parse_mail_subject_body(text)
        subject = subject or "浏览器记录"
        mail_body = (
            (body_hint.strip() + "\n\n" if body_hint.strip() else "")
            + f"以下是最近 {limit} 条浏览器记录：\n\n"
            + raw
        )
        send_result = send_tool.execute(to=to, subject=subject, body=mail_body)
        return self._format_evidence_result(
            summary="已执行跨专家任务：浏览记录整理并发送邮件。",
            route_key="mail",
            evidence_lines=[
                f"tool=browser_history args={{'query': '', 'limit': {limit}, 'strict_domain': ''}}",
                f"tool=gmail_send_email args={{'to': {to!r}, 'subject': {subject!r}, 'body': '<browser history>'}}",
            ],
            detail=send_result,
        )

    def _make_expert_node(self, route_key: str):
        def _node(state: AssistantState) -> AssistantState:
            result = self._run_route_task(route_key, state["user_input"])
            state["outputs"][route_key] = result
            return state

        return _node

    def _finalize_node(self, state: AssistantState) -> AssistantState:
        route = state["route"]
        result = state["outputs"].get(route, "")
        state["final_response"] = self._format_supervisor_response(route, result)
        self._record_session_turn(state["user_input"], route, result)
        return state

    @staticmethod
    def _format_supervisor_response(route: str, expert_result: str) -> str:
        role_map = {
            "mail": "邮件专家",
            "browser": "浏览器专家",
            "coding": "编程专家",
            "file": "文件专家",
        }
        role = role_map.get(route, "专家")
        return f"【Supervisor】已分派给{role}，执行结果如下：\n\n{expert_result}"

    def _build_expert_prompt(self, route_key: str, user_input: str) -> str:
        hint = self._hints.get(route_key, "你是专业助手。")
        browser_hint = ""
        if route_key == "browser":
            browser_hint = (
                "\n你必须优先使用 browser_history 工具完成任务。"
                "该工具支持 query(关键词过滤) 与 limit(数量上限)。"
            )
        return (
            f"角色约束：{hint}\n"
            "请基于当前角色完成任务。如果任务超出职责，明确说明需要主管转派。"
            f"{browser_hint}\n\n"
            f"用户任务：{user_input}"
        )
