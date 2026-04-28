"""LangGraph-style multi-agent orchestration for CoreCoder."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict
from pathlib import Path
from urllib.parse import urlparse

from ..agent import Agent
from ..llm import LLM
from ..mcp import MCPRegistry
from .multi_agent_browser import BrowserTaskMixin
from .multi_agent_file import FileTaskMixin
from .multi_agent_mail import MailTaskMixin
from .plan_engine import ExecutionPlanMixin
from .session_context import AssistantSessionContext
from ..tools.sandbox import (
    DEFAULT_SESSION_SNAPSHOT_NAME,
    SANDBOX_ROOT,
    ensure_within_sandbox,
)

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    END = "__end__"
    StateGraph = None


class AssistantState(TypedDict):
    user_input: str
    route: str
    outputs: dict[str, str]
    final_response: str


@dataclass(frozen=True)
class ExpertProfile:
    name: str
    route_key: str
    system_hint: str
    capabilities: tuple[str, ...]


EXPERTS: tuple[ExpertProfile, ...] = (
    ExpertProfile(
        name="邮件专家",
        route_key="mail",
        system_hint="你只负责邮件信息检索与总结。若超出邮件范围，返回需要转交主管。",
        capabilities=("mail", "notes"),
    ),
    ExpertProfile(
        name="浏览器专家",
        route_key="browser",
        system_hint="你只负责浏览记录和网页链接分析。若工具不足，请明确说明。",
        capabilities=("browser-history", "notes"),
    ),
    ExpertProfile(
        name="编程专家",
        route_key="coding",
        system_hint="你是资深软件工程师，只做代码相关任务，优先给出可执行结果。",
        capabilities=("filesystem", "shell", "code-search"),
    ),
    ExpertProfile(
        name="文件专家",
        route_key="file",
        system_hint="你只负责文件整理、写作、总结与归档。",
        capabilities=("filesystem", "notes"),
    ),
)

_DEFAULT_SITE_MAPPINGS = {
    "query_aliases": {
        "知乎": "zhihu",
        "github": "github",
        "deepseek": "deepseek",
        "学习通": "chaoxing",
        "超星": "chaoxing",
        "chaoxing": "chaoxing",
        "b站": "bilibili",
        "哔哩哔哩": "bilibili",
        "微博": "weibo",
        "微信": "wechat",
        "代码仓库": "github",
        "仓库": "github",
        "开源项目": "github",
        "论文": "arxiv",
        "技术博客": "blog",
    },
    "canonical_query": {
        "学习通": "chaoxing",
        "超星学习通": "chaoxing",
        "超星": "chaoxing",
        "github": "github",
        "知乎": "zhihu",
        "微信": "wechat",
    },
    "strict_domains": {
        "知乎": "zhihu.com",
        "zhihu": "zhihu.com",
        "github": "github.com",
        "gitlab": "gitlab.com",
        "deepseek": "deepseek.com",
        "学习通": "chaoxing.com",
        "超星": "chaoxing.com",
        "chaoxing": "chaoxing.com",
        "b站": "bilibili.com",
        "哔哩哔哩": "bilibili.com",
        "微博": "weibo.com",
        "微信": "weixin.qq.com",
        "x": "x.com",
        "twitter": "x.com",
    },
}


def _load_site_mappings() -> dict:
    cfg_path = Path(__file__).resolve().parent / "browser_site_mappings.json"
    if not cfg_path.exists():
        return _DEFAULT_SITE_MAPPINGS
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return _DEFAULT_SITE_MAPPINGS

    merged = {
        "query_aliases": dict(_DEFAULT_SITE_MAPPINGS["query_aliases"]),
        "canonical_query": dict(_DEFAULT_SITE_MAPPINGS["canonical_query"]),
        "strict_domains": dict(_DEFAULT_SITE_MAPPINGS["strict_domains"]),
    }
    for key in merged:
        value = data.get(key)
        if isinstance(value, dict):
            merged[key].update({str(k): str(v) for k, v in value.items()})
    return merged


_SITE_MAPPINGS = _load_site_mappings()


def _route_intent(user_input: str, ctx: AssistantSessionContext | None = None) -> str:
    text = user_input.lower()
    if any(k in text for k in ("邮件", "gmail", "mail")):
        return "mail"
    # Session-aware follow-ups (same REPL instance keeps `ctx`).
    if ctx:
        if ctx.last_file_path and ctx.last_route == "browser":
            if any(k in user_input for k in ("刚才", "刚刚", "上面", "这个文件", "保存的", "写入的")) and any(
                k in user_input for k in ("内容", "格式", "json", "合法", "修正", "改成", "读取", "验证")
            ):
                return "file"
        if ctx.last_route == "file" and len(user_input.strip()) < 40:
            if any(k in user_input for k in ("继续", "同样", "再来一遍", "再试")):
                return "file"
        if ctx.last_file_path and ctx.last_route == "file":
            if any(k in user_input for k in ("继续", "再保存", "覆盖", "重写")):
                return "file"
        if ctx.last_file_path and ctx.last_file_path.lower().endswith(".json"):
            if len(user_input.strip()) < 72 and any(
                k in user_input for k in ("合法", "标准json", "json格式", "格式化", "美化", "修正内容")
            ):
                if "浏览" not in text and "chrome" not in text:
                    return "file"
    if any(k in text for k in ("浏览", "chrome", "history", "网页", "链接", "站点", "记录", "网址")):
        return "browser"
    # Follow-ups about files under sandbox / JSON content (avoid mis-routing to coding).
    norm = text.replace("\\\\", "\\")
    if "corecodertest" in norm or ".json" in text or ".txt" in text:
        if any(
            k in user_input
            for k in (
                "读取",
                "验证",
                "格式",
                "重写",
                "修正",
                "改成",
                "保存",
                "写入",
                "刚刚",
                "内容",
                "合法",
                "文件",
                "test1",
            )
        ):
            return "file"
    if "json" in text and any(k in user_input for k in ("内容", "格式", "合法", "修正", "改成")):
        return "file"
    if any(k in text for k in ("文档", "文件", "总结", "简报", "笔记")):
        return "file"
    return "coding"


class SuperAssistant(ExecutionPlanMixin, BrowserTaskMixin, MailTaskMixin, FileTaskMixin):
    """Supervisor + experts orchestration with optional LangGraph backend."""

    def __init__(
        self,
        llm: LLM,
        mcp_registry: MCPRegistry | None = None,
        *,
        persist_session: bool = False,
        session_snapshot_path: str | None = None,
    ):
        self.llm = llm
        self.mcp_registry = mcp_registry or MCPRegistry()
        self._experts = {profile.route_key: self._build_expert(profile) for profile in EXPERTS}
        self._hints = {profile.route_key: profile.system_hint for profile in EXPERTS}
        self._graph = self._build_graph() if HAS_LANGGRAPH else None
        self._site_mappings = _SITE_MAPPINGS
        self._session_snapshot_path = self._resolve_snapshot_path(session_snapshot_path)
        self._session_persist = bool(persist_session)
        self.context = AssistantSessionContext()
        self._pending_mail_action: dict[str, object] | None = None
        self._pending_execution_plan: dict[str, object] | None = None
        self._plan_registry: list[tuple[str, Callable[[str], dict[str, object] | None]]] = []
        self._plan_risk_policy: dict[str, dict[str, object]] = {}
        self._step_executor_registry: dict[str, Callable[[dict[str, Any], dict[str, str], list[str]], str | None]] = {}
        self._init_execution_plan_engine()
        if self._session_persist:
            loaded = AssistantSessionContext.read_snapshot(self._session_snapshot_path)
            if loaded is not None:
                self.context = loaded

    def reload_mappings(self) -> dict[str, int]:
        """Hot-reload browser site mappings from JSON config."""
        global _SITE_MAPPINGS
        _SITE_MAPPINGS = _load_site_mappings()
        self._site_mappings = _SITE_MAPPINGS
        return {
            "query_aliases": len(self._site_mappings.get("query_aliases", {})),
            "canonical_query": len(self._site_mappings.get("canonical_query", {})),
            "strict_domains": len(self._site_mappings.get("strict_domains", {})),
        }

    def show_mappings(self) -> dict[str, dict[str, str]]:
        """Return current effective mappings for REPL display."""
        return {
            "query_aliases": dict(self._site_mappings.get("query_aliases", {})),
            "canonical_query": dict(self._site_mappings.get("canonical_query", {})),
            "strict_domains": dict(self._site_mappings.get("strict_domains", {})),
        }

    def show_file_tools(self) -> list[str]:
        """Return currently available tool names for file expert."""
        expert = self._experts.get("file")
        if expert is None:
            return []
        return sorted(tool.name for tool in expert.tools)

    def show_registered_planners(self) -> list[str]:
        """Return current registered planner names."""
        return [name for name, _planner in self._plan_registry]

    def show_registered_actions(self) -> list[str]:
        """Return current registered step action executors."""
        return sorted(self._step_executor_registry.keys())

    def orchestration_debug_preview(self) -> str:
        """Human-readable orchestration registry debug panel."""
        planners = self.show_registered_planners()
        actions = self.show_registered_actions()
        lines = [
            "[Orchestration Registry]",
            f"planners({len(planners)}): {', '.join(planners) if planners else '(none)'}",
            f"actions({len(actions)}): {', '.join(actions) if actions else '(none)'}",
            "risk_policy:",
        ]
        for k in sorted(self._plan_risk_policy.keys()):
            v = self._plan_risk_policy[k]
            lines.append(f"  - {k}: requires_confirmation={bool(v.get('requires_confirmation', True))}")
        if hasattr(self, "path_search_debug_preview"):
            lines.append("")
            lines.append(self.path_search_debug_preview())
        return "\n".join(lines)

    @staticmethod
    def _resolve_snapshot_path(session_snapshot_path: str | None) -> Path:
        default = (SANDBOX_ROOT / DEFAULT_SESSION_SNAPSHOT_NAME).resolve()
        if not (session_snapshot_path or "").strip():
            return default
        p = Path(session_snapshot_path).expanduser().resolve()
        if ensure_within_sandbox(p) is not None:
            return default
        return p

    def _record_session_turn(self, user_input: str, route: str, result: str) -> None:
        self.context.record_turn(user_input, route, result)
        self._persist_session_snapshot()

    def _persist_session_snapshot(self) -> str | None:
        """Write snapshot to disk. Returns error message on failure, None on success."""
        if not self._session_persist:
            return None
        try:
            self._session_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self.context.write_snapshot(self._session_snapshot_path)
            return None
        except OSError as exc:
            return str(exc)

    def set_session_persist(self, enabled: bool, *, reload_from_disk: bool = False) -> dict[str, object]:
        """Toggle persistence; optional reload from snapshot. Returns status for REPL messaging."""
        self._session_persist = bool(enabled)
        status: dict[str, object] = {
            "enabled": self._session_persist,
            "loaded_from_disk": False,
            "turns_loaded": 0,
            "snapshot_path": str(self._session_snapshot_path),
            "snapshot_existed": self._session_snapshot_path.is_file(),
            "write_error": None,
        }
        if not self._session_persist:
            return status
        if reload_from_disk:
            loaded = AssistantSessionContext.read_snapshot(self._session_snapshot_path)
            if loaded is not None:
                self.context = loaded
                status["loaded_from_disk"] = True
                status["turns_loaded"] = len(loaded.turns)
                return status
            status["loaded_from_disk"] = False
            status["note"] = (
                "persist 之前为关时，退出 REPL 后内存上下文不会写入磁盘，因此通常没有旧快照可读。"
            )
        err = self._persist_session_snapshot()
        status["write_error"] = err
        status["snapshot_existed"] = self._session_snapshot_path.is_file()
        return status

    def session_persist_status(self) -> str:
        return (
            f"persist={'on' if self._session_persist else 'off'}, "
            f"path={self._session_snapshot_path}"
        )

    def clear_session_context(self) -> None:
        """Clear in-memory supervisor session (routes, last file path, turn summaries)."""
        self.context.clear()
        if self._session_persist and self._session_snapshot_path.is_file():
            try:
                self._session_snapshot_path.unlink()
            except OSError:
                pass

    def session_context_preview(self) -> str:
        """Human-readable session context for REPL /debug."""
        head = [self.session_persist_status()]
        if not self.context.turns and not self.context.last_file_path and not self.context.last_route:
            head.append("(会话上下文为空)")
            return "\n".join(head)
        parts = head + [
            f"last_route={self.context.last_route!r}",
            f"last_file_path={self.context.last_file_path!r}",
            f"turns={len(self.context.turns)}",
        ]
        block = self.context.format_for_prompt()
        if block:
            parts.append(block)
        return "\n".join(parts)

    def run(self, user_input: str) -> str:
        clarify_followup = self._handle_pending_semantic_clarify(user_input)
        if clarify_followup is not None:
            return clarify_followup
        plan_followup = self._handle_pending_execution_plan(user_input)
        if plan_followup is not None:
            return plan_followup
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
        fallback = _route_intent(user_input, self.context)
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

    def _run_browser_task(self, user_input: str) -> str:
        return BrowserTaskMixin._run_browser_task(self, user_input)

    def _run_route_task(self, route_key: str, user_input: str) -> str:
        if route_key == "browser":
            return self._run_browser_task(user_input)
        if route_key == "mail":
            return self._run_mail_task(user_input)
        if route_key == "file":
            return self._run_file_task(user_input)
        return self._run_agent_task_with_evidence(route_key, user_input)

    def _run_mail_task(self, user_input: str) -> str:
        return MailTaskMixin._run_mail_task(self, user_input)

    def _should_get_mail_by_uid(self, text: str, intent: dict, has_uid: bool) -> tuple[bool, bool]:
        return MailTaskMixin._should_get_mail_by_uid(text, intent, has_uid)

    def _should_search_mail(self, text: str, intent: dict) -> tuple[bool, bool]:
        return MailTaskMixin._should_search_mail(text, intent)

    def _parse_mail_intent(self, user_input: str) -> dict:
        return MailTaskMixin._parse_mail_intent(self, user_input)

    def _extract_email_address(self, text: str) -> str:
        return MailTaskMixin._extract_email_address(text)

    def _is_mail_confirm(self, text: str) -> bool:
        return MailTaskMixin._is_mail_confirm(text)

    def _parse_mail_subject_body(self, text: str) -> tuple[str, str]:
        return MailTaskMixin._parse_mail_subject_body(text)

    def _should_summarize_each_mail(self, text: str, intent: dict) -> tuple[bool, bool]:
        return MailTaskMixin._should_summarize_each_mail(text, intent)

    def _extract_mail_uids(self, raw_list: str) -> list[str]:
        return MailTaskMixin._extract_mail_uids(raw_list)

    def _summarize_single_email(self, raw_mail: str) -> str:
        return MailTaskMixin._summarize_single_email(self, raw_mail)

    def _run_file_task(self, user_input: str) -> str:
        return FileTaskMixin._run_file_task(self, user_input)

    def _run_agent_task_with_evidence(self, route_key: str, user_input: str) -> str:
        expert = self._experts[route_key]
        ctx_block = self.context.format_for_prompt()
        if ctx_block and route_key in ("coding", "file", "mail"):
            effective_input = f"{ctx_block}\n\n用户任务：\n{user_input}"
        else:
            effective_input = user_input
        prompt = self._build_expert_prompt(route_key, effective_input)
        tool_calls: list[str] = []

        def _on_tool(name, kwargs):
            tool_calls.append(f"tool={name} args={kwargs}")

        result = expert.chat_with_xml_tools(prompt, on_tool=_on_tool)
        if route_key == "coding":
            return result
        if not tool_calls:
            return self._format_evidence_result(
                summary="本次未发生工具调用，结论仅为模型推断，不能视为已执行。",
                route_key=route_key,
                evidence_lines=["tool_calls=[]"],
                detail=result,
            )
        return self._format_evidence_result(
            summary="任务已执行，见下方工具调用证据。",
            route_key=route_key,
            evidence_lines=tool_calls,
            detail=result,
        )

    @staticmethod
    def _format_evidence_result(summary: str, route_key: str, evidence_lines: list[str], detail: str) -> str:
        role_map = {
            "mail": "邮件专家",
            "browser": "浏览器专家",
            "coding": "编程专家",
            "file": "文件专家",
        }
        role = role_map.get(route_key, "专家")
        evidence = "\n".join(f"- {line}" for line in evidence_lines) if evidence_lines else "- (none)"
        return (
            f"[{role}] {summary}\n\n"
            "证据输出：\n"
            f"{evidence}\n\n"
            "执行详情：\n"
            f"{detail}"
        )

    def _extract_browser_query(self, user_input: str) -> str:
        return BrowserTaskMixin._extract_browser_query(self, user_input)

    def _normalize_browser_query(self, raw_query: str) -> str:
        return BrowserTaskMixin._normalize_browser_query(self, raw_query)

    def _should_use_recent_history(self, user_input: str) -> bool:
        return BrowserTaskMixin._should_use_recent_history(user_input)

    def _extract_strict_domain(self, user_input: str, query: str) -> str:
        return BrowserTaskMixin._extract_strict_domain(self, user_input, query)

    def _extract_result_limit(self, user_input: str, default: int = 20) -> int:
        return FileTaskMixin._extract_result_limit(user_input, default)

    def _extract_windows_path(self, text: str) -> str:
        return FileTaskMixin._extract_windows_path(text)

    def _extract_after_keyword(self, text: str, keywords: tuple[str, ...]) -> str:
        return FileTaskMixin._extract_after_keyword(text, keywords)

    def _extract_named_output_file(self, user_input: str) -> str:
        return FileTaskMixin._extract_named_output_file(user_input)

    def _normalize_sandbox_file_path(self, file_path: str) -> str:
        return FileTaskMixin._normalize_sandbox_file_path(file_path)

    def _browser_plaintext_slice(self, raw: str) -> str:
        return BrowserTaskMixin._browser_plaintext_slice(raw)

    def _parse_browser_history_plaintext(self, raw: str) -> list[dict[str, object]]:
        return BrowserTaskMixin._parse_browser_history_plaintext(self, raw)

    def _browser_history_to_json_file_body(self, raw: str, *, query: str, limit: int, strict_domain: str) -> str:
        return BrowserTaskMixin._browser_history_to_json_file_body(self, raw, query=query, limit=limit, strict_domain=strict_domain)

    def _extract_json_followup_path(self, text: str) -> str:
        return FileTaskMixin._extract_json_followup_path(text)

    def _user_wants_json_file_rewrite(self, text: str) -> bool:
        return FileTaskMixin._user_wants_json_file_rewrite(text)

    def _user_wants_plaintext_file_rewrite(self, text: str) -> bool:
        return FileTaskMixin._user_wants_plaintext_file_rewrite(text)

    def _resolve_followup_file_path(self, text: str) -> str:
        return FileTaskMixin._resolve_followup_file_path(self, text)

    def _repair_missing_sandbox_path(self, path: str) -> str:
        return FileTaskMixin._repair_missing_sandbox_path(self, path)

    def _json_to_plain_text(self, value: object, indent: int = 0) -> list[str]:
        return FileTaskMixin._json_to_plain_text(value, indent)

    def _decode_text_scalar(self, raw: str) -> object:
        return FileTaskMixin._decode_text_scalar(raw)

    def _parse_plaintext_kv_history(self, raw: str) -> dict[str, object] | None:
        return FileTaskMixin._parse_plaintext_kv_history(raw)

    def _file_task_json_to_plaintext(self, file_path: str) -> str:
        return FileTaskMixin._file_task_json_to_plaintext(self, file_path)

    def _parse_rewrite_intent(self, user_input: str) -> dict:
        return FileTaskMixin._parse_rewrite_intent(self, user_input)

    def _file_task_json_normalize(self, user_input: str, file_path: str) -> str:
        return FileTaskMixin._file_task_json_normalize(self, user_input, file_path)

    def _parse_browser_action_intent(self, user_input: str) -> dict:
        return BrowserTaskMixin._parse_browser_action_intent(self, user_input)

    def _parse_browser_intent(self, user_input: str) -> dict:
        return BrowserTaskMixin._parse_browser_intent(self, user_input)

    def _parse_file_intent(self, user_input: str) -> dict:
        return FileTaskMixin._parse_file_intent(self, user_input)

    def _parse_intent_json(self, parser_prompt: str, defaults: dict) -> dict:
        try:
            resp = self.llm.chat(
                messages=[
                    {"role": "system", "content": "你是严格JSON解析器，只输出合法JSON对象。"},
                    {"role": "user", "content": parser_prompt},
                ]
            )
            text = (resp.content or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
            data = json.loads(text)
            if not isinstance(data, dict):
                return dict(defaults)
            merged = dict(defaults)
            for k in merged:
                if k in data:
                    merged[k] = data[k]
            return merged
        except Exception:
            return dict(defaults)

    def _top_domains_from_history(self, raw: str, top_n: int = 3) -> list[tuple[str, int]]:
        return BrowserTaskMixin._top_domains_from_history(raw, top_n)

    def _repair_text_mojibake(self, text: str) -> str:
        return BrowserTaskMixin._repair_text_mojibake(text)
