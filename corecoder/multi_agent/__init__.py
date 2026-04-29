"""LangGraph-style multi-agent orchestration for CoreCoder."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import textwrap
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict
from pathlib import Path
from urllib.parse import urlparse

from ..agent import Agent
from ..llm import LLM
from ..mcp import MCPRegistry
from .mods.shared import AssistantState, EXPERTS, SITE_MAPPINGS, load_site_mappings
from .mods.supervisor_flow import HAS_LANGGRAPH, SupervisorFlowMixin
from .mods.temp_caps import TempCapabilityMixin
from .multi_agent_browser import BrowserTaskMixin
from .multi_agent_file import FileTaskMixin
from .multi_agent_mail import MailTaskMixin
from .plan_engine import ExecutionPlanMixin
from .session_context import AssistantSessionContext
from .temp_input_adapters import create_default_temp_input_adapter_registry, extract_first_url
from .temp_registry import TempCapabilityRecord, TempCapabilityRegistry
from .temp_tool_template import TEMP_TOOL_TEMPLATE
from ..tools.sandbox import (
    DEFAULT_SESSION_SNAPSHOT_NAME,
    SANDBOX_ROOT,
    ensure_within_sandbox,
    sandbox_path,
)


class SuperAssistant(TempCapabilityMixin, SupervisorFlowMixin, ExecutionPlanMixin, BrowserTaskMixin, MailTaskMixin, FileTaskMixin):
    """Supervisor + experts orchestration with optional LangGraph backend."""

    def __init__(
        self,
        llm: LLM,
        mcp_registry: MCPRegistry | None = None,
        *,
        persist_session: bool = False,
        session_snapshot_path: str | None = None,
        autoload_kept_temp_tools: bool = False,
    ):
        self.llm = llm
        self.mcp_registry = mcp_registry or MCPRegistry()
        self._experts = {profile.route_key: self._build_expert(profile) for profile in EXPERTS}
        self._hints = {profile.route_key: profile.system_hint for profile in EXPERTS}
        self._graph = self._build_graph() if HAS_LANGGRAPH else None
        self._site_mappings = SITE_MAPPINGS
        self._session_snapshot_path = self._resolve_snapshot_path(session_snapshot_path)
        self._session_persist = bool(persist_session)
        self.context = AssistantSessionContext()
        self._pending_mail_action: dict[str, object] | None = None
        self._pending_execution_plan: dict[str, object] | None = None
        self._plan_registry: list[tuple[str, Callable[[str], dict[str, object] | None]]] = []
        self._plan_risk_policy: dict[str, dict[str, object]] = {}
        self._step_executor_registry: dict[str, Callable[[dict[str, Any], dict[str, str], list[str]], str | None]] = {}
        self._temp_registry = TempCapabilityRegistry(
            self,
            sandbox_path(".temp_tools"),
            sandbox_path(".kept_tools"),
        )
        self._temp_registry.set_autoload_kept(bool(autoload_kept_temp_tools))
        self._pending_temp_capability: dict[str, Any] | None = None
        # For "open/execute" UX: an interactive runtime dialog that collects/confirm args
        # before invoking the temporary capability.
        self._pending_temp_capability_dialog: dict[str, Any] | None = None
        self._last_temp_capability_failure: dict[str, Any] | None = None
        self._temp_input_adapters = create_default_temp_input_adapter_registry()
        self._coding_toolsmith_enabled: bool = False
        self._coding_quality_team_enabled: bool = False
        api_key = (
            os.getenv("CORECODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        base_url = (
            os.getenv("CORECODER_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("DEEPSEEK_BASE_URL")
            or None
        )
        self._review_llm = self._build_specialist_llm(
            role_env="REVIEW",
            default_model=self.llm.model or "qwen3-coder-30b",
            default_api_key=api_key,
            default_base_url=base_url,
            temperature=0.0,
            max_tokens=4096,
        )
        self._repair_llm = self._build_specialist_llm(
            role_env="FIX",
            default_model=self.llm.model or "qwen3-coder-30b",
            default_api_key=api_key,
            default_base_url=base_url,
            temperature=0.1,
            max_tokens=4096,
        )
        self._verify_llm = self._build_specialist_llm(
            role_env="VERIFY",
            default_model=self.llm.model or "qwen3-coder-30b",
            default_api_key=api_key,
            default_base_url=base_url,
            temperature=0.0,
            max_tokens=4096,
        )
        self._format_llm = self._build_specialist_llm(
            role_env="FORMAT",
            default_model=self.llm.model or "qwen3-coder-30b",
            default_api_key=api_key,
            default_base_url=base_url,
            temperature=0.0,
            max_tokens=2048,
        )

        # Coding expert LLM for temporary tool chain.
        # 普通对话仍使用 self.llm（主模型）；临时工具链生成/自修复才走这里。
        self._temp_tool_coding_llm = self._build_specialist_llm(
            role_env="CODING_TOOLSMITH",
            default_model=self.llm.model or "qwen3-coder-30b",
            default_api_key=api_key,
            default_base_url=base_url,
            temperature=0.0,
            max_tokens=8192,
        )
        self._init_execution_plan_engine()
        if bool(autoload_kept_temp_tools):
            self._temp_registry.autoload_kept_capabilities()
        if self._session_persist:
            loaded = AssistantSessionContext.read_snapshot(self._session_snapshot_path)
            if loaded is not None:
                self.context = loaded

    def reload_mappings(self) -> dict[str, int]:
        """Hot-reload browser site mappings from JSON config."""
        self._site_mappings = load_site_mappings()
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
        temp_actions = []
        if hasattr(self, "_temp_registry"):
            temp_actions = list(self._temp_registry.step_executors.keys())
        return sorted(set(self._step_executor_registry.keys()) | set(temp_actions))

    def show_temp_capabilities(self) -> list[dict[str, object]]:
        if not hasattr(self, "_temp_registry"):
            return []
        rows: list[dict[str, object]] = []
        for item in self._temp_registry.list_records():
            row = self._temp_registry.export_record_dict(item.name)
            if row:
                rows.append(row)
        return rows

    def show_kept_capabilities(self) -> list[str]:
        if not hasattr(self, "_temp_registry"):
            return []
        return [str(path) for path in self._temp_registry.list_kept_files()]

    def show_qc_reports(self, limit: int = 10) -> list[dict[str, str]]:
        root = sandbox_path(".temp_tools", "_qc_reports")
        try:
            rows: list[dict[str, str]] = []
            for p in sorted(root.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[: max(1, int(limit or 10))]:
                rows.append(
                    {
                        "name": p.name,
                        "path": str(p),
                        "modified_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                    }
                )
            return rows
        except Exception:
            return []

    def get_qc_report_summary(self, name_or_path: str) -> tuple[bool, dict[str, Any] | str]:
        value = str(name_or_path or "").strip()
        if not value:
            return False, "empty report name"
        root = sandbox_path(".temp_tools", "_qc_reports")
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / value
        if candidate.suffix.lower() != ".json":
            candidate = candidate.with_suffix(".json")
        if not candidate.exists():
            return False, f"report not found: {candidate.name}"
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            result = data.get("result", {}) if isinstance(data, dict) else {}
            models = data.get("models", {}) if isinstance(data, dict) else {}
            round_data = data.get("round", {}) if isinstance(data, dict) else {}
            review = round_data.get("review", {}) if isinstance(round_data, dict) else {}
            verify = round_data.get("verify", {}) if isinstance(round_data, dict) else {}
            summary = {
                "name": candidate.name,
                "path": str(candidate),
                "timestamp_utc": str(data.get("timestamp_utc", "")),
                "requirement": str(data.get("requirement", "")),
                "iteration_mode": str(data.get("iteration_mode", "fix")),
                "capability_delta": data.get("capability_delta", []) if isinstance(data.get("capability_delta", []), list) else [],
                "models": {
                    "review": str(models.get("review", "")),
                    "repair": str(models.get("repair", "")),
                    "verify": str(models.get("verify", "")),
                },
                "review": {
                    "approved": bool(review.get("approved", False)),
                    "risk_level": str(review.get("risk_level", "")),
                    "issues_count": len(review.get("issues", [])) if isinstance(review.get("issues", []), list) else 0,
                },
                "verify": {
                    "pass": bool(verify.get("pass", False)),
                    "reason": str(verify.get("reason", "")),
                    "must_fix_count": len(verify.get("must_fix", [])) if isinstance(verify.get("must_fix", []), list) else 0,
                },
                "trace": result.get("trace", []) if isinstance(result.get("trace", []), list) else [],
            }
            return True, summary
        except Exception as exc:
            return False, str(exc)

    def set_autoload_kept_temp_tools(self, enabled: bool) -> bool:
        self._temp_registry.set_autoload_kept(enabled)
        return self._temp_registry.autoload_kept

    def autoload_kept_temp_tools(self) -> list[str]:
        return self._temp_registry.autoload_kept_capabilities()

    def load_temp_capability(
        self,
        value: str,
        *,
        source_requirement: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        try:
            target = self._temp_registry.resolve_candidate(value)
            record = self._temp_registry.load_from_file(
                target,
                source_requirement=source_requirement,
                metadata=metadata,
            )
            return True, f"{record.name} ({record.action_name})"
        except Exception as exc:
            return False, str(exc)

    def discard_temp_capability(self, name: str) -> bool:
        return self._temp_registry.discard(name, delete_file=True)

    def keep_temp_capability(self, name: str) -> tuple[bool, str]:
        return self._temp_registry.keep(name)

    def clear_temp_capabilities(self) -> int:
        return self._temp_registry.clear_temp()

    def temp_capabilities_debug_preview(self) -> str:
        return self._temp_registry.debug_preview()

    @staticmethod
    def _build_specialist_llm(
        *,
        role_env: str,
        default_model: str,
        default_api_key: str,
        default_base_url: str | None,
        temperature: float,
        max_tokens: int,
    ) -> LLM:
        model = (
            os.getenv(f"CORECODER_{role_env}_EXPERT_MODEL", "").strip()
            or os.getenv("CORECODER_SPECIALIST_MODEL", "").strip()
            or default_model
            or "qwen3-coder-30b"
        )
        api_key = (
            os.getenv(f"CORECODER_{role_env}_EXPERT_API_KEY", "").strip()
            or os.getenv("CORECODER_SPECIALIST_API_KEY", "").strip()
            or default_api_key
            or "dummy"
        )
        base_url = (
            os.getenv(f"CORECODER_{role_env}_EXPERT_BASE_URL", "").strip()
            or os.getenv("CORECODER_SPECIALIST_BASE_URL", "").strip()
            or default_base_url
            or None
        )
        return LLM(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def sync_specialist_llms(self, *, model: str, base_url: str | None, api_key: str) -> None:
        for attr in ("_review_llm", "_repair_llm", "_verify_llm", "_format_llm"):
            llm_obj = getattr(self, attr, None)
            if llm_obj is None:
                continue
            setattr(
                self,
                attr,
                LLM(
                    model=model,
                    api_key=api_key or "dummy",
                    base_url=base_url,
                    temperature=llm_obj.temperature,
                    max_tokens=llm_obj.max_tokens,
                    timeout=llm_obj.timeout,
                ),
            )

    def set_coding_toolsmith(self, enabled: bool) -> bool:
        self._coding_toolsmith_enabled = bool(enabled)
        self._coding_quality_team_enabled = bool(enabled)
        if not self._coding_toolsmith_enabled:
            self._pending_temp_capability = None
            self._pending_temp_capability_dialog = None
            # Do not persist temp capabilities into kept tools; no save dialog.
        return self._coding_toolsmith_enabled

    def coding_toolsmith_enabled(self) -> bool:
        return bool(self._coding_toolsmith_enabled)

    def coding_quality_team_enabled(self) -> bool:
        return bool(self._coding_quality_team_enabled)

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