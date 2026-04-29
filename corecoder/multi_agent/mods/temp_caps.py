from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import textwrap
from typing import Any

from ..temp_input_adapters import extract_first_url
from ..temp_registry import TempCapabilityRecord
from ..temp_tool_template import TEMP_TOOL_TEMPLATE
from ...tools.sandbox import sandbox_path


class TempCapabilityMixin:
    def _build_temp_planner_context(self, spec: dict[str, Any], user_input: str) -> dict[str, Any]:
        """Deterministic planner context for temp executor patching."""
        input_type = str(spec.get("input_type", "") or "").strip().lower()
        side_effect = str(spec.get("side_effect_level", "") or "none").strip().lower()
        source_requirement = str(spec.get("source_requirement", "") or "") + "\n" + str(spec.get("description", "") or "")
        need_web = any(
            k in (user_input or "").lower()
            or k in source_requirement.lower()
            for k in ("网页", "webpage", "页面内容", "提取网页", "extract webpage", "抓取")
        )
        return {
            "input_type": input_type,
            "side_effect_level": side_effect,
            "needs_fetch_url_content": bool(need_web),
            "success_contract": {
                "must_write": "intermediate['working_text']",
                "must_return": "None",
            },
            "failure_contract": {
                "must_return_prefix": "Error:",
            },
            "url_contract": {
                "requires_http_https": input_type == "url",
            },
            "save_contract": {
                "requires_file_write": side_effect == "write",
                "preferred_args_keys": ["file_path", "save_path"],
            },
        }

    def _temp_input_format_example(self, record: TempCapabilityRecord, action_spec: dict[str, Any]) -> str:
        input_type = str(action_spec.get("input_type", "") or "").strip().lower()
        side_effect = str(action_spec.get("side_effect_level", "none") or "none").strip().lower()
        name = str(record.name or "temp_tool")
        examples: list[str] = [f"可用输入示例（{name}）："]
        if input_type == "url":
            examples.append("- 仅 URL：")
            examples.append("  https://example.com/article")
            if side_effect == "write":
                examples.append("- URL + 保存路径：")
                examples.append("  当前链接：https://example.com/article，保存到 D:\\corecodertest\\my_result.txt")
                examples.append("- 强制指定工具调用：")
                examples.append(
                    f"  调用临时工具 {name}，链接是：https://example.com/article，保存到 D:\\corecodertest\\my_result.txt"
                )
            else:
                examples.append("- 概括类调用：")
                examples.append(f"  调用临时工具 {name}，链接是：https://example.com/article")
        elif input_type in ("source_file", "file_path", "path"):
            examples.append("- 文件路径输入：")
            examples.append("  读取 D:\\corecodertest\\notes.txt 并处理")
        elif input_type in ("email", "recipient"):
            examples.append("- 邮箱输入：")
            examples.append("  发送到 2276716701@qq.com")
        else:
            examples.append("- 通用文本输入：")
            examples.append(f"  调用临时工具 {name}，参数为：<你的输入>")
        return "\n".join(examples)

    def _extract_save_path_from_text(self, text: str) -> str:
        raw = str(text or "")
        if hasattr(self, "_extract_windows_path"):
            try:
                win_path = str(self._extract_windows_path(raw) or "").strip()
                if win_path:
                    return win_path
            except Exception:
                pass
        m = re.search(r"(?:保存到|写入到|输出到)\s*[:：]?\s*([^\n\r,，;；]+)", raw, flags=re.IGNORECASE)
        if m:
            candidate = str(m.group(1)).strip().strip("'\"")
            if candidate:
                return candidate
        return ""

    def _inject_temp_runtime_args(
        self,
        user_input: str,
        action_spec: dict[str, Any],
        step_args: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        fixed = dict(step_args)
        trace: list[str] = []
        side_effect = str(action_spec.get("side_effect_level", "none") or "none").strip().lower()
        if side_effect != "write":
            return fixed, trace

        # For write-type temp tools, auto-fill save path from user input/customization.
        save_path = str(fixed.get("save_path", "") or fixed.get("file_path", "")).strip()
        if not save_path:
            extracted = self._extract_save_path_from_text(user_input)
            if extracted:
                save_path = extracted
                trace.append(f"runtime_arg_inject=save_path_from_input:{extracted}")
        if not save_path:
            save_path = str(
                sandbox_path(
                    "temp_retry_exports",
                    f"temp_output_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt",
                )
            )
            trace.append(f"runtime_arg_inject=save_path_default:{save_path}")
        fixed["save_path"] = save_path
        if "file_path" not in fixed or not str(fixed.get("file_path", "")).strip():
            fixed["file_path"] = save_path
        return fixed, trace

    @staticmethod
    def _is_temp_chain_context_input(text: str) -> bool:
        raw = str(text or "").strip()
        low = raw.lower()
        return (
            "调用刚刚的临时工具" in raw
            or "调用临时工具" in raw
            or "当前这个链接" in raw
            or "当前的链接" in raw
            or low.startswith("http://")
            or low.startswith("https://")
            or "http://" in low
            or "https://" in low
        )

    @staticmethod
    def _is_temp_capability_dialog_trigger(text: str) -> bool:
        """User says "open/execute" -> enter runtime dialog each time (UX requirement)."""
        raw = (text or "").strip()
        low = raw.lower()
        if not raw:
            return False
        # B:只要命中“打开/执行/运行”，就进入临时能力运行时交互对话。
        # （是否命中某个 temp capability 仍由上层的 record 匹配决定。）
        return any(k in low for k in ("打开", "执行", "运行", "调用", "开始执行"))

    def _required_args_for_action_spec(self, action_spec: dict[str, Any]) -> list[str]:
        input_type = str(action_spec.get("input_type", "")).strip().lower()
        adapter = self._temp_input_adapters.get(input_type)
        if adapter is None:
            return []
        return [adapter.arg_name]

    @staticmethod
    def _format_step_args_for_display(step_args: dict[str, Any], arg_names: list[str] | None = None) -> list[str]:
        arg_names = arg_names or list(step_args.keys())
        rows: list[str] = []
        for k in arg_names:
            v = step_args.get(k, "")
            if v is None:
                v = ""
            s = str(v).strip()
            if not s:
                rows.append(f"- {k}=<empty>")
            else:
                # Don't print huge content.
                rows.append(f"- {k}={s[:200]}{'...' if len(s) > 200 else ''}")
        return rows

    def _format_temp_capability_dialog_prompt(
        self,
        *,
        record: TempCapabilityRecord,
        action_spec: dict[str, Any],
        step_args: dict[str, Any],
        required_args: list[str],
        missing_args: list[str],
    ) -> str:
        route_key = record.route or action_spec.get("route", "file")
        arg_display = self._format_step_args_for_display(step_args, arg_names=required_args)
        if missing_args:
            detail = (
                "已进入临时能力交互窗口。\n\n"
                f"当前能力：{record.name}（{record.action_name}）\n"
                "已解析参数：\n"
                + "\n".join(arg_display)
                + "\n\n"
                f"缺少参数：{', '.join(missing_args)}\n"
                "请在下一条回复中补全（例如：url=... / 保存到 D:\\... / 文件路径...）。"
            )
        else:
            detail = (
                "已进入临时能力交互窗口。\n\n"
                f"当前能力：{record.name}（{record.action_name}）\n"
                "已解析参数：\n"
                + "\n".join(arg_display)
                + "\n\n"
                "回复：确认执行 / 取消 / 或直接提供修改参数（例如发新的 url）。"
            )
        evidence_lines = [f"temp_runtime_dialog=1", f"temp_match={record.name}", f"required_args={required_args}"]
        return self._format_evidence_result(
            summary="临时能力进入对话确认（open/execute dialog）",
            route_key=route_key,
            evidence_lines=evidence_lines,
            detail=detail,
        )

    @staticmethod
    def _failure_snapshot_dir() -> Path:
        return sandbox_path(".temp_tools", "_failure_snapshots")

    def _persist_failure_snapshot(self, snapshot: dict[str, Any]) -> str:
        root = self._failure_snapshot_dir()
        root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = self._normalize_temp_capability_name(str(snapshot.get("record_name", "") or "temp_failure"))
        path = root / f"{name}_{ts}.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[20:]:
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
        return str(path)

    def _set_last_temp_failure(self, snapshot: dict[str, Any]) -> None:
        self._last_temp_capability_failure = dict(snapshot)
        try:
            saved = self._persist_failure_snapshot(dict(snapshot))
            self._last_temp_capability_failure["snapshot_file"] = saved
        except Exception:
            pass

    def _get_last_temp_failure(self) -> dict[str, Any] | None:
        last = getattr(self, "_last_temp_capability_failure", None)
        if isinstance(last, dict):
            return last
        root = self._failure_snapshot_dir()
        try:
            files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not files:
                return None
            data = json.loads(files[0].read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["snapshot_file"] = str(files[0])
                self._last_temp_capability_failure = data
                return data
        except Exception:
            return None
        return None

    @staticmethod
    def _extract_capability_delta(customization: str) -> list[str]:
        text = str(customization or "").strip()
        if not text:
            return []
        parts = re.split(r"[;\n；]+", text)
        out: list[str] = []
        for item in parts:
            seg = str(item).strip(" -\t")
            if seg:
                out.append(seg)
        return out[:8]

    def retry_last_temp_failure(
        self,
        rounds: int = 1,
        customization: str = "",
        iteration_mode: str = "fix",
    ) -> str:
        rounds = max(1, min(int(rounds or 1), 5))
        mode = str(iteration_mode or "fix").strip().lower()
        if mode not in {"fix", "evolve"}:
            mode = "fix"
        capability_delta = self._extract_capability_delta(customization)
        last = self._get_last_temp_failure()
        if not isinstance(last, dict):
            return self._format_evidence_result(
                summary="当前没有可重试的临时能力失败记录。",
                route_key="coding",
                evidence_lines=[f"temp_retry.mode={mode}", "temp_retry=none"],
                detail="请先触发一次临时能力并发生失败，再使用 /temp-retry 或 /temp-evolve。",
            )
        raw_input = str(last.get("user_input", "")).strip()
        if not raw_input:
            return self._format_evidence_result(
                summary="失败记录缺少原始输入，无法自动重试。",
                route_key="coding",
                evidence_lines=["temp_retry=invalid_failure_snapshot"],
                detail="请重新发起一次临时能力调用。",
            )
        base_input = str(last.get("base_user_input", "")).strip()
        if not base_input:
            marker = "\n重试定制要求："
            base_input = raw_input.split(marker, 1)[0].strip() if marker in raw_input else raw_input
        custom = str(customization or "").strip()
        last_error = str(last.get("error", "") or "").strip()
        auto_custom_parts: list[str] = []
        if not custom:
            err_text = str(last.get("error", "") or "").lower()
            failure = last.get("failure", {})
            failure_type = str(failure.get("failure_type", "")).strip().lower() if isinstance(failure, dict) else ""
            step_args = last.get("step_args", {}) if isinstance(last.get("step_args", {}), dict) else {}
            detected_url = self._extract_first_url(base_input)
            if (failure_type == "input_extraction" or "missing url" in err_text) and detected_url:
                auto_custom_parts.append(f"url={detected_url}")
            save_path = str(step_args.get("save_path", "") or step_args.get("file_path", "")).strip()
            if (failure_type == "input_extraction" and ("save path" in err_text or "save_path" in err_text)) or (
                "missing required arguments" in err_text and not save_path
            ):
                if not save_path:
                    save_path = str(
                        sandbox_path(
                            "temp_retry_exports",
                            f"retry_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt",
                        )
                    )
                auto_custom_parts.append(f"save_path={save_path}")
        auto_custom = "；".join(x for x in auto_custom_parts if x)
        previous_custom = str(last.get("customization", "") or "").strip()
        custom_parts = [x for x in (previous_custom, auto_custom, custom) if x]
        if last_error:
            custom_parts.append(f"历史失败原因：{last_error}")
        effective_custom = "；".join(dict.fromkeys(custom_parts))
        merged_input = (
            f"{base_input}\n重试定制要求：{effective_custom}"
            if effective_custom
            else base_input
        )
        stage = str(last.get("stage", "runtime_query_failed") or "runtime_query_failed").strip()
        logs: list[str] = [
            f"temp_retry.rounds={rounds}",
            f"temp_retry.mode={mode}",
            f"temp_retry.stage={stage}",
            f"temp_retry.input={base_input!r}",
            f"temp_retry.record={str(last.get('record_name', '') or '')}",
        ]
        if capability_delta:
            logs.append(f"temp_retry.capability_delta={capability_delta}")
        if effective_custom:
            logs.append(f"temp_retry.customization={effective_custom!r}")
        final_output = ""
        for idx in range(1, rounds + 1):
            if stage == "create_validate_load":
                # Retry the creation chain with optional customized requirements.
                out = self._maybe_stage_temp_capability(
                    merged_input,
                    iteration_mode=mode,
                    capability_delta=capability_delta,
                )
            else:
                # Default: retry runtime invocation chain.
                out = self._maybe_run_temp_capability_query(
                    merged_input,
                    preferred_record_name=str(last.get("record_name", "") or ""),
                    skip_dialog_trigger=True,
                )
            if out is None:
                final_output = self._format_evidence_result(
                    summary="重试过程中未命中临时能力。",
                    route_key="coding",
                    evidence_lines=logs + [f"temp_retry.round={idx}: no_match"],
                    detail="可能该临时能力已被卸载或关键词不再匹配。",
                )
                break
            final_output = out
            fail = getattr(self, "_last_temp_capability_failure", None)
            logs.append(f"temp_retry.round={idx}: invoked")
            if not isinstance(fail, dict):
                # Success path clears last failure snapshot.
                return self._format_evidence_result(
                    summary="已触发临时工具链迭代重试并成功。",
                    route_key="coding",
                    evidence_lines=logs + ["temp_retry.result=success"],
                    detail=out,
                )
        return self._format_evidence_result(
            summary="已按请求触发临时工具链迭代重试，但仍未完全成功。",
            route_key="coding",
            evidence_lines=logs + ["temp_retry.result=still_failed"],
            detail=final_output or "请补充输入参数后再试。",
        )

    def _handle_pending_temp_capability_dialog(self, user_input: str) -> str | None:
        pending = getattr(self, "_pending_temp_capability_dialog", None)
        if not isinstance(pending, dict):
            return None
        text = (user_input or "").strip()
        if not text:
            return None

        if self._is_cancel_text(text):
            self._pending_temp_capability_dialog = None
            return self._format_evidence_result(
                summary="已取消临时能力交互窗口。",
                route_key=str(pending.get("route", "file") or "file"),
                evidence_lines=["temp_runtime_dialog=cancel"],
                detail="未执行任何临时能力。",
            )

        record_name = str(pending.get("record_name", "")).strip()
        if not record_name:
            self._pending_temp_capability_dialog = None
            return "Error: temp runtime dialog missing record_name."

        record = self._temp_registry.get_record(record_name) if hasattr(self, "_temp_registry") else None
        if record is None:
            self._pending_temp_capability_dialog = None
            return "Error: temp runtime dialog record not found."

        action_name = str(record.action_name or "").strip()
        action_spec = self._get_action_spec(action_name)
        step_args = dict(pending.get("step_args", {}) or {})
        required_args = list(pending.get("required_args", []) or [])
        missing_args = list(pending.get("missing_args", []) or [])

        if self._is_confirm_text(text):
            self._pending_temp_capability_dialog = None
            # Execute using stored step_args, and skip dialog trigger to prevent re-entry.
            executed = self._maybe_run_temp_capability_query(
                text,
                preferred_record_name=record_name,
                step_args_override=step_args,
                skip_dialog_trigger=True,
            )
            if executed is None:
                return self._format_evidence_result(
                    summary="临时能力确认执行失败：未能命中临时能力或参数缺失。",
                    route_key=record.route,
                    evidence_lines=["temp_runtime_dialog=confirm_exec_failed"],
                    detail="请重试。",
                )
            executed_text = str(executed)
            ok = ("已命中临时能力并执行完成" in executed_text) and ("失败" not in executed_text)
            if ok:
                # Temp capabilities are session-only by default.
                # Do not enter a "keep/save to kept tools" flow.
                return executed_text
            return executed_text

        # Treat any non-confirm input as "parameter update".
        input_type = str(action_spec.get("input_type", "")).strip().lower()
        adapter = self._temp_input_adapters.get(input_type)
        if adapter is not None:
            candidate = adapter.normalizer(adapter.extractor(self, text))
            if candidate:
                step_args[adapter.arg_name] = candidate

        step_args, _injected_trace = self._inject_temp_runtime_args(text, action_spec, step_args)
        missing_args = [arg for arg in required_args if not str(step_args.get(arg, "")).strip()]

        self._pending_temp_capability_dialog = {
            **pending,
            "step_args": step_args,
            "missing_args": missing_args,
        }
        return self._format_temp_capability_dialog_prompt(
            record=record,
            action_spec=action_spec,
            step_args=step_args,
            required_args=required_args,
            missing_args=missing_args,
        )

    def _handle_pending_temp_capability_save_dialog(self, user_input: str) -> str | None:
        pending = getattr(self, "_pending_temp_capability_save_dialog", None)
        if not isinstance(pending, dict):
            return None

        text = (user_input or "").strip()
        if not text:
            return None

        record_name = str(pending.get("record_name", "")).strip()
        if not record_name:
            self._pending_temp_capability_save_dialog = None
            return "Error: temp save dialog missing record_name."

        lower = text.lower()
        if self._is_cancel_text(text):
            self._pending_temp_capability_save_dialog = None
            return self._format_evidence_result(
                summary="已取消保存选择（临时能力仍保持在内存中）。",
                route_key="file",
                evidence_lines=["temp_save_dialog=cancel"],
                detail=f"record={record_name}",
            )

        if any(k in lower for k in ("keep", "保存", "保留")):
            self._pending_temp_capability_save_dialog = None
            ok, detail = self.keep_temp_capability(record_name)
            if ok:
                return self._format_evidence_result(
                    summary="已保存该临时能力（并卸载自内存）。",
                    route_key="file",
                    evidence_lines=["temp_save=keep"],
                    detail=str(detail),
                )
            return self._format_evidence_result(
                summary="保存失败。",
                route_key="file",
                evidence_lines=["temp_save=keep", "temp_save_failed=1"],
                detail=str(detail),
            )

        if any(k in lower for k in ("discard", "丢弃", "删除", "不保存", "不用了")):
            self._pending_temp_capability_save_dialog = None
            ok = self.discard_temp_capability(record_name)
            return self._format_evidence_result(
                summary="已丢弃该临时能力。",
                route_key="file",
                evidence_lines=["temp_save=discard", f"temp_discard_ok={ok}"],
                detail=record_name,
            )

        return self._format_evidence_result(
            summary="保存选择输入不明。",
            route_key="file",
            evidence_lines=["temp_save_dialog=help"],
            detail="请回复：keep / discard / 取消",
        )

    def _format_language_assistant(
        self,
        user_input: str,
        *,
        iteration_mode: str = "fix",
        capability_delta: list[str] | None = None,
    ) -> dict[str, Any]:
        raw_req = self._extract_temp_request_text(user_input)
        mode = str(iteration_mode or "fix").strip().lower()
        if mode not in {"fix", "evolve"}:
            mode = "fix"
        delta_rows = [str(x).strip() for x in (capability_delta or []) if str(x).strip()]
        defaults = {
            "normalized_requirement": raw_req,
            "inferred_inputs": [],
            "recommended_coding_expert_count": 1,
            "reason": "",
            "confidence": 0.0,
            "iteration_mode": mode,
            "capability_delta": delta_rows,
        }
        prompt = (
            "你是“格式语言助手”。只输出 JSON。\n"
            "任务：把用户原始需求归一化，提取结构化输入槽位，并估计编程专家并行个数。\n"
            "字段：normalized_requirement(str), inferred_inputs(list[{name,type,required,example}]), "
            "recommended_coding_expert_count(int,1~3), reason(str), confidence(0~1), "
            "iteration_mode(fix/evolve), capability_delta(list[str])。\n"
            "约束：\n"
            "1) normalized_requirement 必须保留用户目标，不要扩展新目标。\n"
            "2) inferred_inputs 仅提取执行所需外部输入。\n"
            "3) recommended_coding_expert_count：简单任务=1，中等=2，复杂或多阶段=3。\n"
            f"4) 当前迭代模式={mode}；若为 evolve，capability_delta 表示期望新增/增强能力，需保留。\n"
            f"capability_delta输入: {json.dumps(delta_rows, ensure_ascii=False)}\n"
            f"用户输入: {user_input}\n"
            f"默认值: {json.dumps(defaults, ensure_ascii=False)}"
        )
        parsed = self._parse_intent_json_with_llm(
            getattr(self, "_format_llm", self.llm),
            prompt,
            defaults,
            label="format_assistant",
        )
        out = dict(defaults)
        if isinstance(parsed, dict):
            out.update({k: parsed.get(k, out[k]) for k in out})
        out["normalized_requirement"] = str(out.get("normalized_requirement", "") or raw_req).strip() or raw_req
        raw_inputs = out.get("inferred_inputs", [])
        if not isinstance(raw_inputs, list):
            raw_inputs = []
        norm_inputs: list[dict[str, Any]] = []
        for item in raw_inputs[:8]:
            if not isinstance(item, dict):
                continue
            norm_inputs.append(
                {
                    "name": str(item.get("name", "")).strip(),
                    "type": str(item.get("type", "text")).strip(),
                    "required": bool(item.get("required", True)),
                    "example": str(item.get("example", "")).strip(),
                }
            )
        out["inferred_inputs"] = [x for x in norm_inputs if x["name"]]
        try:
            n = int(out.get("recommended_coding_expert_count", 1) or 1)
        except Exception:
            n = 1
        out["recommended_coding_expert_count"] = max(1, min(n, 3))
        out["reason"] = str(out.get("reason", "") or "").strip()
        out["confidence"] = max(0.0, min(float(out.get("confidence", 0.0) or 0.0), 1.0))
        out["iteration_mode"] = mode
        raw_delta = out.get("capability_delta", [])
        if not isinstance(raw_delta, list):
            raw_delta = []
        out["capability_delta"] = [str(x).strip() for x in raw_delta[:8] if str(x).strip()]
        return out

    @staticmethod
    def _strict_skill_rules() -> str:
        return (
            "统一技能规范（所有专家必须遵守）：\n"
            "A) 协议一致性：外部输入仅允许 step['args']；成功写入 intermediate['working_text']；失败返回 Error: ...。\n"
            "B) 安全合规：禁止硬编码密钥/密码/token；敏感信息只能从环境变量读取；不得在日志/输出中泄露密钥。\n"
            "C) 可维护性：优先复用 owner 现有方法；避免重复实现；输出可读、可验证。\n"
            "D) 可测试性：self_test_plan 至少包含 args，且尽量提供 expect_working_text_contains。\n"
            "E) 修复边界：修复不得改变用户目标，只能提高稳定性、正确性和可执行性。\n"
        )

    @staticmethod
    def _temp_protocol_spec() -> str:
        return (
            "【临时工具底座协议（统一规范）】\n"
            "P1. executor_body 仅允许函数体，严禁再定义 def execute(...) 或其他 def/class。\n"
            "P2. 外部输入仅可读取 step['args']；禁止读取 intermediate 中的原始输入。\n"
            "P3. 成功路径必须：写入 intermediate['working_text']，并 return None。\n"
            "P4. 失败路径必须：return 'Error: ...'（字符串），不得返回 dict/list 作为错误。\n"
            "P5. 禁止硬编码密钥/密码/token；敏感信息仅可从环境变量读取，且不得输出到日志/回复。\n"
            "P6. 若 input_type 命中已支持适配器，优先使用受支持 input_type；避免无效自定义类型。\n"
            "P7. self_test_plan 必须包含 args(dict)，建议包含 expect_working_text_contains(list[str])。\n"
            "P8. URL 类能力必须做 URL 基础合法性校验（http/https、空值、明显非法格式）。\n"
            "P9. 不改变用户目标语义；仅做实现与稳定性增强。\n"
            "P10. 生成前请先自检：以上 1~9 若任一不满足，必须先修正再输出。\n"
        )

    @staticmethod
    def _normalize_temp_capability_name(value: str) -> str:
        raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
        normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_")
        if not normalized:
            normalized = f"temp_tool_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        if normalized[0].isdigit():
            normalized = f"temp_{normalized}"
        return normalized[:64]

    @staticmethod
    def _indent_executor_body(body: str) -> str:
        cleaned = textwrap.dedent((body or "").rstrip())
        if not cleaned:
            cleaned = "return 'Error: empty temp capability executor.'"
        return "\n".join(f"    {line}" if line.strip() else "" for line in cleaned.splitlines())

    @staticmethod
    def _extract_executor_body_from_temp_code(code: str) -> str:
        text = code or ""
        match = re.search(
            r"def execute\(owner: Any, step: dict\[str, Any\], intermediate: dict\[str, str\], evidence: list\[str\]\) -> str \| None:\n(.*?)\n\ndef register\(\)",
            text,
            flags=re.DOTALL,
        )
        if not match:
            return ""
        block = match.group(1)
        return textwrap.dedent(block).rstrip()

    @staticmethod
    def _extract_temp_request_text(user_input: str) -> str:
        text = (user_input or "").strip()
        for kw in (
            "需求是",
            "需求：",
            "实现需求：",
            "实现一个临时工具",
            "创建一个临时工具",
            "做一个临时工具",
            "生成一个临时工具",
            "临时能力",
            "临时工具",
        ):
            idx = text.find(kw)
            if idx >= 0:
                remain = text[idx + len(kw) :].strip(" ：:，,")
                if remain:
                    return remain
        return text

    def _protocolize_temp_requirement(self, user_input: str, fmt: dict[str, Any] | None = None) -> str:
        base = self._extract_temp_request_text(user_input)
        f = fmt if isinstance(fmt, dict) else {}
        normalized = str(f.get("normalized_requirement", "") or "").strip()
        if normalized:
            base = normalized
        input_rows = f.get("inferred_inputs", [])
        input_names: list[str] = []
        if isinstance(input_rows, list):
            for row in input_rows[:8]:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name", "")).strip()
                if name:
                    input_names.append(name)
        if not input_names:
            input_names = ["url"] if self._extract_first_url(user_input) else ["user_input"]
        io_hint = "、".join(dict.fromkeys(input_names))
        return (
            f"{base}\n"
            "实现约束（必须遵守底座协议）：\n"
            f"- 外部输入只能从 step['args'] 读取（建议使用：{io_hint}）。\n"
            "- executor_body 只能是函数体，不得再定义 def/class。\n"
            "- 成功必须写入 intermediate['working_text']，并 return None。\n"
            "- 失败必须返回字符串：Error: ...。\n"
            "- 禁止硬编码密钥/密码/token，敏感信息仅可从环境变量读取且不得输出。\n"
            "- 若是 URL 类任务，必须先做 URL 合法性校验（http/https、非空、基础格式）。"
            "- 若需求包含“网页/网页内容/页面内容/提取网页”，必须通过 `fetch_tool = owner._get_tool('browser', 'fetch_url_content')` 获取工具实例，再调用 `fetch_tool.execute(url=..., max_chars=...)` 获取页面文本；"
            "若为保存类能力（side_effect_level=write），保存路径必须优先使用 step['args']['file_path'] 或 step['args']['save_path']，不得硬编码如 `/tmp/...` 这类固定路径；"
            "然后将真实提取/处理结果（或截断摘要）写入 intermediate['working_text']（不得返回模板 demo）。"
        )

    @staticmethod
    def _local_protocol_precheck(spec: dict[str, Any]) -> dict[str, Any]:
        issues: list[str] = []
        body = str(spec.get("executor_body", "") or "").strip()
        input_type = str(spec.get("input_type", "") or "").strip().lower()
        low = body.lower()
        if not body:
            issues.append("executor_body_empty")
        if re.search(r"^\s*def\s+\w+\s*\(", body, flags=re.MULTILINE):
            issues.append("executor_body_contains_def")
        if re.search(r"^\s*class\s+\w+\s*[:(]", body, flags=re.MULTILINE):
            issues.append("executor_body_contains_class")
        if "step['args']" not in body and 'step["args"]' not in body:
            issues.append("missing_step_args_protocol")
        if "intermediate['working_text']" not in body and 'intermediate["working_text"]' not in body:
            issues.append("missing_working_text_write")
        if "return none" not in low:
            issues.append("missing_return_none_success_path")
        if "error:" not in low:
            issues.append("missing_error_prefix")
        if input_type == "url":
            if "url" not in low:
                issues.append("missing_url_arg_usage")
            # URL validation hint:
            # Accept common patterns generated by temp tools, such as:
            # - url.startswith("http") / url.startswith('http')
            # - re.match(r'^https?://', url)
            # - re.compile(...) + pattern.match(...)
            # - urllib.parse.urlparse(url)
            has_startswith = "startswith('http')" in low or 'startswith("http")' in low
            has_re_match = "re.match" in low and ("https?://" in low or "http" in low)
            has_re_compile = "re.compile" in low
            has_urlparse = "urlparse" in low
            if not (has_startswith or has_re_match or has_re_compile or has_urlparse):
                issues.append("missing_url_validation_hint")
            need_web_fetch = any(
                kw in (str(spec.get("source_requirement", "") or "") + str(spec.get("description", "") or "")).lower()
                for kw in ("网页内容", "网页", "webpage", "页面内容", "提取网页", "extract webpage", "抓取")
            )
            if need_web_fetch and "fetch_url_content" not in low:
                issues.append("missing_fetch_url_content_call")
            # Hardening: enforce correct tool calling style.
            if "owner.fetch_url_content" in low:
                issues.append("use_owner_get_tool_for_fetch_url_content")
            if "owner.llm" in low:
                # Temp executor should not directly call LLM via owner.llm; use simple deterministic logic.
                issues.append("temp_executor_should_not_call_owner_llm")
            if "/tmp/" in low or "open('/tmp" in low or "open(\"/tmp" in low:
                issues.append("hardcoded_tmp_path_forbidden")
        score = max(0, 100 - len(issues) * 15)
        return {"ok": len(issues) == 0, "score": score, "issues": issues}

    def _pre_qc_rebuild_spec(
        self,
        *,
        user_input: str,
        spec: dict[str, Any],
        decision: dict[str, Any],
        iteration_mode: str = "fix",
        capability_delta: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        trace: list[str] = []
        current = dict(spec)
        mode = str(iteration_mode or "fix").strip().lower()
        delta_rows = [str(x).strip() for x in (capability_delta or []) if str(x).strip()]
        for attempt in range(1, 3):
            check = self._local_protocol_precheck(current)
            trace.append(f"pre_qc_protocol.round={attempt}")
            trace.append(f"pre_qc_protocol.score={int(check.get('score', 0))}")
            trace.append(f"pre_qc_protocol.issues={check.get('issues', [])}")
            if bool(check.get("ok", False)):
                trace.append("pre_qc_protocol.result=pass")
                return current, trace
            # 先本地自修复，不通过再强制重生
            current = self._repair_temp_capability_spec(
                current,
                f"pre_qc_protocol_failed: {', '.join(str(x) for x in check.get('issues', []))}",
                {"stage": "pre_qc", "local_protocol": check},
            )
            recheck = self._local_protocol_precheck(current)
            if bool(recheck.get("ok", False)):
                trace.append("pre_qc_protocol.rebuild=repair_spec")
                trace.append("pre_qc_protocol.result=pass_after_repair")
                return current, trace
            if self._should_force_second_generation(user_input, current, decision):
                forced = self._force_draft_temp_capability(
                    user_input,
                    current,
                    decision,
                    iteration_mode=mode,
                    capability_delta=delta_rows,
                )
                if isinstance(forced, dict) and forced:
                    current = forced
                    trace.append("pre_qc_protocol.rebuild=force_regenerate")
        final = self._local_protocol_precheck(current)
        trace.append(f"pre_qc_protocol.final_score={int(final.get('score', 0))}")
        trace.append(f"pre_qc_protocol.final_issues={final.get('issues', [])}")
        return current, trace

    def _extract_first_url(self, text: str) -> str:
        return extract_first_url(self, text)

    def _build_temp_step_args(self, user_input: str, action_spec: dict[str, Any]) -> dict[str, Any] | None:
        input_type = str(action_spec.get("input_type", "")).strip().lower()
        return self._temp_input_adapters.build_step_args(self, user_input, input_type)

    def _rule_based_runtime_failure(
        self,
        *,
        record: TempCapabilityRecord,
        action_spec: dict[str, Any],
        step_args: dict[str, Any],
        error_text: str,
        runtime_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        err = (error_text or "").lower()
        input_type = str(action_spec.get("input_type", "")).strip().lower()
        failure_type = "tool_logic"
        reason = "默认按工具实现错误处理。"
        if input_type == "url":
            url = str(step_args.get("url", "")).strip()
            if url and any(k in url for k in ("这个网页", "网页内容", "这个链接", "链接内容")):
                failure_type = "input_extraction"
                reason = "URL 参数包含尾随自然语言，属于输入提取错误。"
            elif "404" in err:
                failure_type = "input_extraction"
                reason = "HTTP 404 更可能是 URL 错误或目标不存在。"
            elif "no url" in err or "'url'" in err:
                failure_type = "input_extraction"
                reason = "url 参数缺失或注入失败。"
        elif input_type in ("source_file", "file_path", "path") and any(k in err for k in ("not found", "不存在", "no such file")):
            failure_type = "input_extraction"
            reason = "文件路径提取失败或目标文件不存在。"
        elif input_type in ("email", "recipient") and any(k in err for k in ("recipient", "email", "邮箱")):
            failure_type = "input_extraction"
            reason = "邮箱地址提取失败。"
        elif any(k in err for k in ("401", "403", "unauthorized", "forbidden", "missing github_token")):
            failure_type = "external_env"
            reason = "认证/环境问题，不应优先修工具代码。"
        elif any(k in err for k in ("timeout", "timed out", "connection", "dns")):
            failure_type = "external_env"
            reason = "网络环境问题。"
        return {
            "failure_type": failure_type,
            "reason": reason,
            "input_type": input_type,
            "record_name": record.name,
            "step_args": dict(step_args),
            "snapshot": dict(runtime_snapshot),
            "classifier": "rules",
            "confidence": 0.65,
        }

    def _classify_runtime_failure(
        self,
        *,
        record: TempCapabilityRecord,
        action_spec: dict[str, Any],
        step_args: dict[str, Any],
        error_text: str,
        runtime_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._rule_based_runtime_failure(
            record=record,
            action_spec=action_spec,
            step_args=step_args,
            error_text=error_text,
            runtime_snapshot=runtime_snapshot,
        )
        defaults = {
            "failure_type": str(fallback.get("failure_type", "tool_logic")),
            "reason": str(fallback.get("reason", "")),
            "confidence": float(fallback.get("confidence", 0.65) or 0.65),
        }
        prompt = (
            "你是 CoreCoder 的运行失败分类器。只输出 JSON。\n"
            "任务：根据临时工具运行失败现场，将失败分类为以下之一：\n"
            "1) input_extraction: 输入提取/参数归一化错误\n"
            "2) tool_logic: 临时工具自身逻辑错误\n"
            "3) external_env: 缺少环境变量、认证、网络、权限等外部环境错误\n"
            "4) external_http: 目标网页/接口本身返回 4xx/5xx，且看起来不是工具代码错误\n"
            "字段：failure_type, reason, confidence(0~1)。\n"
            "要求：\n"
            "- 不要输出额外文本。\n"
            "- 若把握不大，参考默认值。\n"
            f"默认值: {json.dumps(defaults, ensure_ascii=False)}\n"
            f"record_name: {record.name}\n"
            f"action_spec: {json.dumps(action_spec, ensure_ascii=False)}\n"
            f"step_args: {json.dumps(step_args, ensure_ascii=False)}\n"
            f"error_text: {error_text}\n"
            f"runtime_snapshot: {json.dumps(runtime_snapshot, ensure_ascii=False)}"
        )
        parsed = self._parse_intent_json_with_llm(
            getattr(self, "_temp_tool_coding_llm", self.llm),
            prompt,
            defaults,
            label="coding_expert_runtime_classifier",
        )
        failure_type = str(parsed.get("failure_type", defaults["failure_type"])).strip().lower()
        if failure_type not in {"input_extraction", "tool_logic", "external_env", "external_http"}:
            return fallback
        confidence = float(parsed.get("confidence", defaults["confidence"]) or defaults["confidence"])
        if confidence < 0.55:
            return fallback
        return {
            "failure_type": failure_type,
            "reason": str(parsed.get("reason", defaults["reason"])),
            "input_type": str(action_spec.get("input_type", "")).strip().lower(),
            "record_name": record.name,
            "step_args": dict(step_args),
            "snapshot": dict(runtime_snapshot),
            "classifier": "llm",
            "confidence": confidence,
            "fallback_failure_type": str(fallback.get("failure_type", "")),
        }

    def _repair_runtime_inputs(
        self,
        *,
        failure: dict[str, Any],
        user_input: str,
        step_args: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        trace: list[str] = []
        fixed = dict(step_args)
        if str(failure.get("failure_type", "")) != "input_extraction":
            return fixed, trace
        input_type = str(failure.get("input_type", ""))
        return self._temp_input_adapters.repair_args(self, user_input, input_type, fixed)

    def _classify_temp_capability_intent(self, user_input: str) -> dict[str, Any]:
        text = (user_input or "").strip()
        low = text.lower()
        defaults: dict[str, Any] = {
            "intent": "normal_task",
            "confidence": 0.0,
            "reason": "",
            "missing_info": [],
        }
        if not text:
            return dict(defaults)
        if any(low.startswith(prefix) for prefix in ("/temp-tool", "/temp-tools", "/help", "/debug")):
            return dict(defaults)
        prompt = (
            "你是 CoreCoder 的临时能力意图分类器。只输出 JSON。\n"
            "判断用户这句话是在：\n"
            "1) create_temp_capability: 希望系统新生成一个可复用的临时工具/能力/action\n"
            "2) normal_task: 只是让系统直接执行一次已有能力任务\n"
            "字段：intent(create_temp_capability/normal_task), confidence(0~1), reason(str), missing_info(list[str])。\n"
            "判定原则：\n"
            "- 如果用户在描述一个以后可反复调用的新能力、工具、接口、动作，偏向 create_temp_capability。\n"
            "- 如果用户只是想完成当前一次任务，偏向 normal_task。\n"
            "- 不要靠单个关键词；看整体语义是否在要求“长出一个新能力”。\n"
            f"默认值: {json.dumps(defaults, ensure_ascii=False)}\n"
            f"用户输入: {text}"
        )
        parsed = self._parse_intent_json_with_llm(
            getattr(self, "_temp_tool_coding_llm", self.llm),
            prompt,
            defaults,
            label="coding_expert_temp_intent_classifier",
        )
        out = dict(defaults)
        if isinstance(parsed, dict):
            out.update({k: parsed.get(k, out[k]) for k in out})
        intent = str(out.get("intent", "normal_task")).strip().lower()
        if intent not in ("create_temp_capability", "normal_task"):
            intent = "normal_task"
        confidence = max(0.0, min(float(out.get("confidence", 0.0) or 0.0), 1.0))
        reason = str(out.get("reason", "")).strip()
        missing_info = out.get("missing_info", [])
        if not isinstance(missing_info, list):
            missing_info = []

        # Low-confidence lexical fallback only as guardrail.
        fallback_create = False
        if any(k in text for k in ("临时工具", "临时能力", "临时 action", "临时工具文件", "自动加载注册")):
            fallback_create = True
        elif re.search(r"临时.{0,20}工具", text, flags=re.IGNORECASE):
            fallback_create = True
        elif re.search(r"(生成|创建|做|实现).{0,30}工具", text, flags=re.IGNORECASE):
            fallback_create = True
        elif re.search(r"工具.{0,20}(生成|创建|实现)", text, flags=re.IGNORECASE):
            fallback_create = True
        elif "github" in low and any(k in text for k in ("工具", "能力", "编程专家")):
            fallback_create = True
        elif "编程专家" in text and any(k in text for k in ("新需求", "需求", "生成工具", "实现功能")):
            fallback_create = True

        if intent == "normal_task" and fallback_create and confidence < 0.72:
            intent = "create_temp_capability"
            confidence = max(confidence, 0.68)
            if not reason:
                reason = "低置信度下由兜底规则判定为创建临时能力。"

        return {
            "intent": intent,
            "confidence": confidence,
            "reason": reason,
            "missing_info": [str(x) for x in missing_info[:8]],
        }

    def _draft_temp_capability(
        self,
        user_input: str,
        *,
        iteration_mode: str = "fix",
        capability_delta: list[str] | None = None,
    ) -> dict[str, Any]:
        fmt = self._format_language_assistant(
            user_input,
            iteration_mode=iteration_mode,
            capability_delta=capability_delta,
        )
        requirement = self._protocolize_temp_requirement(user_input, fmt)
        supported_inputs = self._temp_input_adapters.describe_supported_inputs()
        defaults: dict[str, Any] = {
            "should_create": False,
            "name": "",
            "description": "",
            "route": "file",
            "input_type": "text_any",
            "output_type": "text_any",
            "output_schema": "temp_result_text",
            "side_effect_level": "none",
            "source_requirement": requirement,
            "executor_body": "",
            "trigger_keywords": [],
            "default_target": "",
            "input_adapters": [],
            "self_test_plan": {},
            "clarify_question": "",
            "confidence": 0.0,
        }
        prompt = (
            "你是 CoreCoder 的临时工具生成器。只输出 JSON。\n"
            "目标：把用户自然语言需求转成一个可注册的临时 action 草案。\n"
            "字段：should_create(bool), name(str), description(str), route(browser/mail/file/coding), "
            "input_type(str), output_type(str), output_schema(str), side_effect_level(none/write/external_send), "
            "source_requirement(str), executor_body(str), trigger_keywords(list[str]), default_target(str), input_adapters(list[dict]), self_test_plan(dict), clarify_question(str), confidence(0~1)。\n"
            "严格要求：\n"
            "1) executor_body 必须是 Python 函数体，不含 def。\n"
            "2) 临时工具的外部输入一律只能从 step['args'] 读取，禁止从 intermediate 读取原始输入参数，也禁止使用未定义的裸 args 变量。\n"
            "3) executor_body 只能调用 owner 上已有方法或 intermediate/evidence；必须把结果写入 intermediate['working_text']，失败时返回 Error: ...，成功时返回 None。\n"
            "4) self_test_plan 至少包含 args(dict)，可选 expect_working_text_contains(list[str])。\n"
            "5) 若需求仍然太模糊，should_create=false，并填 clarify_question。\n"
            "6) 尽量复用 owner._get_tool / owner._format_evidence_result / owner.llm / owner._extract_result_limit 等现有能力。\n"
            "7) 若涉及 GitHub Token、邮箱密码等敏感信息，只允许从环境变量读取，不得写死进代码。\n"
            "8) 若需求输入明显属于现有适配器支持的类型，优先把 input_type 设成受支持值，不要随意造新类型；只有确实不匹配时才使用 text_any。\n"
            "9) 若需求包含“提取网页内容/网页内容/页面内容/提取网页”，executor_body 必须通过 `fetch_tool = owner._get_tool('browser', 'fetch_url_content')` 获取工具实例；然后调用 `fetch_tool.execute(url=..., max_chars=...)` 获取页面文本；保存到 write 能力时必须使用 step['args']['file_path']/step['args']['save_path']，不得硬编码如 `/tmp/...`。\n"
            "10) 只有确实需要新增输入类型时，才填写 input_adapters。每项都必须是声明式对象："
            "{input_types(list[str]), arg_name(str), pattern(str), normalizer(url/email/path/text)}。\n"
            + self._temp_protocol_spec()
            + self._strict_skill_rules()
            + f"格式语言助手归一化结果: {json.dumps(fmt, ensure_ascii=False)}\n"
            + f"iteration_mode={str(fmt.get('iteration_mode', 'fix'))}\n"
            + f"capability_delta={json.dumps(fmt.get('capability_delta', []), ensure_ascii=False)}\n"
            + f"当前可自动适配的 input_type:\n{supported_inputs}\n"
            + f"默认值: {json.dumps(defaults, ensure_ascii=False)}\n"
            + f"用户需求: {requirement}"
        )
        parsed = self._parse_intent_json_with_llm(
            getattr(self, "_temp_tool_coding_llm", self.llm),
            prompt,
            defaults,
            label="coding_expert_draft",
        )
        result = dict(defaults)
        if isinstance(parsed, dict):
            result.update({k: parsed.get(k, result[k]) for k in result})
        result["name"] = self._normalize_temp_capability_name(str(result.get("name", "")))
        result["route"] = str(result.get("route", "file")).strip().lower() or "file"
        if result["route"] not in ("browser", "mail", "file", "coding"):
            result["route"] = "file"
        result["input_type"] = str(result.get("input_type", "text_any") or "text_any").strip()
        result["output_type"] = str(result.get("output_type", "text_any") or "text_any").strip()
        result["output_schema"] = str(result.get("output_schema", "temp_result_text") or "temp_result_text").strip()
        result["side_effect_level"] = str(result.get("side_effect_level", "none") or "none").strip()
        result["description"] = str(result.get("description", "") or requirement or result["name"]).strip()
        result["source_requirement"] = str(result.get("source_requirement", "") or requirement).strip()
        result["trigger_keywords"] = self._normalize_trigger_keywords(result.get("trigger_keywords", []), result["source_requirement"])
        result["default_target"] = str(result.get("default_target", "") or "").strip()
        result["input_adapters"] = result.get("input_adapters", []) if isinstance(result.get("input_adapters", []), list) else []
        self_test_plan = result.get("self_test_plan", {})
        result["self_test_plan"] = self._normalize_self_test_plan(self_test_plan)
        # Stabilize URL-based webpage extraction self-test:
        # don't hit real network during temp-tool creation validation.
        need_web = any(k in (user_input or "").lower() for k in ("网页", "webpage", "页面内容", "提取网页", "网页内容"))
        if need_web and str(result.get("input_type", "")).strip().lower() == "url":
            # Force deterministic, offline-safe self-test for "webpage extraction" tools.
            result["self_test_plan"] = {
                "args": {"url": "self_test://webpage"},
                "expect_working_text_contains": [],
            }
        result["clarify_question"] = str(result.get("clarify_question", "")).strip()
        result["executor_body"] = str(result.get("executor_body", "")).rstrip()
        result["confidence"] = max(0.0, min(float(result.get("confidence", 0.0) or 0.0), 1.0))
        return result

    def _parse_intent_json_with_llm(
        self,
        llm: Any,
        parser_prompt: str,
        defaults: dict[str, Any],
        *,
        trace: list[str] | None = None,
        label: str = "expert",
    ) -> dict[str, Any]:
        try:
            resp = llm.chat(
                messages=[
                    {"role": "system", "content": "你是严格JSON解析器，只输出合法JSON对象。"},
                    {"role": "user", "content": parser_prompt},
                ]
            )
            text = (resp.content or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?", "", text).strip()
                text = re.sub(r"```$", "", text).strip()
            return json.loads(text)
        except Exception as exc:
            raw = str(exc).lower()
            unavailable = (
                "does not exist" in raw
                or "notfounderror" in raw
                or "404" in raw
                or "model_not_found" in raw
            )
            if unavailable and hasattr(self, "llm"):
                try:
                    fallback = self.llm
                    if trace is not None:
                        trace.append(
                            f"{label}_model_fallback={getattr(llm, 'model', 'n/a')}=>{getattr(fallback, 'model', 'n/a')}"
                        )
                    resp = fallback.chat(
                        messages=[
                            {"role": "system", "content": "你是严格JSON解析器，只输出合法JSON对象。"},
                            {"role": "user", "content": parser_prompt},
                        ]
                    )
                    text = (resp.content or "").strip()
                    if text.startswith("```"):
                        text = re.sub(r"^```(?:json)?", "", text).strip()
                        text = re.sub(r"```$", "", text).strip()
                    return json.loads(text)
                except Exception as exc2:
                    if trace is not None:
                        trace.append(f"{label}_fallback_failed={exc2}")
                    return dict(defaults)
            if trace is not None:
                trace.append(f"{label}_parse_failed={exc}")
            return dict(defaults)

    def _run_temp_capability_quality_team(
        self,
        user_input: str,
        spec: dict[str, Any],
        *,
        iteration_mode: str = "fix",
        capability_delta: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str], bool, str]:
        if not bool(getattr(self, "_coding_quality_team_enabled", False)):
            return spec, [], True, ""
        review: dict[str, Any] = {}
        repaired: dict[str, Any] = {}
        verified: dict[str, Any] = {}
        defaults_review = {
            "approved": True,
            "risk_level": "low",
            "issues": [],
            "repair_guidance": "",
            "confidence": 0.0,
        }
        mode = str(iteration_mode or "fix").strip().lower()
        if mode not in {"fix", "evolve"}:
            mode = "fix"
        delta_rows = [str(x).strip() for x in (capability_delta or []) if str(x).strip()]
        review_prompt = (
            "你是代码审查专家。只输出 JSON。\n"
            "任务：审查临时工具 spec 是否可执行、是否满足输入协议与安全约束。\n"
            "字段：approved(bool), risk_level(low/medium/high), issues(list[str]), repair_guidance(str), confidence(0~1)。\n"
            "重点：\n"
            "1) executor_body 只能从 step['args'] 读取外部输入。\n"
            "2) 成功必须写 intermediate['working_text']，失败返回 Error: ...。\n"
            "3) 不得泄露敏感信息，不得硬编码 token/password。\n"
            f"4) 当前迭代模式={mode}；若为 evolve，在保证可执行性的前提下检查是否覆盖 capability_delta。\n"
            f"capability_delta: {json.dumps(delta_rows, ensure_ascii=False)}\n"
            + self._temp_protocol_spec()
            + self._strict_skill_rules()
            + f"用户需求: {self._extract_temp_request_text(user_input)}\n"
            + f"当前 spec: {json.dumps(spec, ensure_ascii=False)}"
        )
        trace: list[str] = []
        review = self._parse_intent_json_with_llm(
            self._review_llm,
            review_prompt,
            defaults_review,
            trace=trace,
            label="review",
        )
        issues = review.get("issues", [])
        issue_rows = [str(x) for x in issues] if isinstance(issues, list) else []
        approved = bool(review.get("approved", True))

        trace.extend([
            f"quality_review.approved={approved}",
            f"quality_review.risk={str(review.get('risk_level', 'low'))}",
            f"quality_review.issues={len(issue_rows)}",
            f"quality_models=review:{getattr(self._review_llm, 'model', 'n/a')},repair:{getattr(self._repair_llm, 'model', 'n/a')},verify:{getattr(self._verify_llm, 'model', 'n/a')}",
        ])

        patched = dict(spec)
        if not approved or issue_rows:
            repair_defaults = {
                "name": str(spec.get("name", "")),
                "description": str(spec.get("description", "")),
                "route": str(spec.get("route", "file")),
                "input_type": str(spec.get("input_type", "text_any")),
                "output_type": str(spec.get("output_type", "text_any")),
                "output_schema": str(spec.get("output_schema", "temp_result_text")),
                "side_effect_level": str(spec.get("side_effect_level", "none")),
                "source_requirement": str(spec.get("source_requirement", "")),
                "executor_body": str(spec.get("executor_body", "")),
                "trigger_keywords": list(spec.get("trigger_keywords", [])),
                "default_target": str(spec.get("default_target", "")),
                "input_adapters": list(spec.get("input_adapters", []) or []),
                "self_test_plan": dict(spec.get("self_test_plan", {}) or {}),
            }
            supported_inputs = self._temp_input_adapters.describe_supported_inputs()
            validation_error = (
                f"quality_review rejected: approved={approved} risk={str(review.get('risk_level', 'low'))}; "
                f"issues={json.dumps(issue_rows, ensure_ascii=False)}"
            )
            validation_snapshot = {"quality_review": review, "issues": issue_rows}
            edit_plan = self._edit_temp_capability_patch_plan(
                defaults=repair_defaults,
                supported_inputs=supported_inputs,
                validation_error=validation_error,
                validation_snapshot=validation_snapshot,
            )
            repaired = self._patch_temp_capability_spec_from_plan(
                defaults=repair_defaults,
                supported_inputs=supported_inputs,
                validation_error=validation_error,
                validation_snapshot=validation_snapshot,
                edit_plan=edit_plan if isinstance(edit_plan, dict) else {},
            )
            if isinstance(repaired, dict):
                patched.update({k: repaired.get(k, patched.get(k)) for k in repair_defaults.keys()})
            patched["name"] = self._normalize_temp_capability_name(str(patched.get("name", "") or spec.get("name", "")))
            patched["trigger_keywords"] = self._normalize_trigger_keywords(
                patched.get("trigger_keywords", []),
                str(patched.get("source_requirement", "")),
            )
            patched["self_test_plan"] = self._normalize_self_test_plan(patched.get("self_test_plan", {}))
            patched["executor_body"] = str(patched.get("executor_body", "")).rstrip()
            trace.append("quality_edit_patch.applied=1")
        else:
            trace.append("quality_edit_patch.applied=0")

        # Claude Code 风格：以“运行自测/验证”为中心闭环，而不是只依赖 LLM verify。
        # 流程：render -> validate_candidate(self_test_plan) -> 若失败则 repair -> 重试，
        # 直到通过或达到最大轮次。
        max_verify_iters = 4
        verified: dict[str, Any] = {"pass": False, "reason": "", "must_fix": [], "confidence": 0.0}
        vpass = False
        probe_root = sandbox_path(".temp_tools", "_qc_probe")
        probe_root.mkdir(parents=True, exist_ok=True)
        planner_context = self._build_temp_planner_context(patched, user_input)
        trace.append(
            "planner.built="
            + json.dumps(
                {
                    "input_type": planner_context.get("input_type"),
                    "needs_fetch_url_content": planner_context.get("needs_fetch_url_content"),
                    "side_effect_level": planner_context.get("side_effect_level"),
                },
                ensure_ascii=False,
            )
        )

        for iter_idx in range(1, max_verify_iters + 1):
            local_check = self._local_protocol_precheck(patched)
            trace.append(
                f"quality_verify.iter{iter_idx}.local_ok={1 if bool(local_check.get('ok', False)) else 0}"
            )
            if not bool(local_check.get("ok", False)):
                # Protocol-level issues first.
                issues = local_check.get("issues", [])
                hint = "; ".join(str(x) for x in issues) if isinstance(issues, list) else str(local_check.get("error", ""))
                failure_context = {
                    "stage": "local_protocol_precheck",
                    "issues": issues,
                    "score": local_check.get("score"),
                    "hint": hint,
                }
                trace.append(f"patch.iter{iter_idx}.stage=local_protocol_precheck")
                repatched = self._repair_temp_capability_spec(
                    patched,
                    f"local_protocol_precheck failed(iter={iter_idx}): {hint}",
                    {"planner_context": planner_context, "failure_context": failure_context, "local_protocol": local_check},
                )
                if isinstance(repatched, dict):
                    patched = repatched
                    trace.append(f"quality_verify.iter{iter_idx}.repair_local=1")
                    continue
                trace.append(f"quality_verify.iter{iter_idx}.repair_local=0")
                break

            # Render and execute self-test through validate_candidate.
            probe_file = probe_root / f"{self._normalize_temp_capability_name(str(patched.get('name', 'temp_tool')))}_probe_iter{iter_idx}.py"
            try:
                probe_code = self._render_temp_capability_code(patched)
                probe_file.write_text(probe_code, encoding="utf-8")
            except Exception as exc:
                verified = {
                    "pass": False,
                    "reason": f"probe_code_render_or_write_failed: {exc}",
                    "must_fix": [],
                    "confidence": 0.1,
                }
                trace.append(f"quality_verify.iter{iter_idx}.probe_write_failed=1")
                break

            validation = self._temp_registry.validate_candidate(probe_file)
            ok = bool(validation.get("ok", False))
            trace.append(f"quality_verify.iter{iter_idx}.validate_ok={1 if ok else 0}")
            if ok:
                vpass = True
                verified = {
                    "pass": True,
                    "reason": "validate_candidate ok (self_test_plan passed)",
                    "must_fix": [],
                    "confidence": 1.0,
                }
                break

            err = str(validation.get("error", "unknown validation error"))
            snap = validation.get("snapshot", {})
            must_fix = []
            if isinstance(snap, dict):
                # Best-effort: keep snapshot context in must_fix for repair prompt.
                if snap.get("working_text"):
                    must_fix.append("self_test_working_text_present")
            # Missing token diffs for self-test expectations.
            working_text = ""
            if isinstance(snap, dict):
                working_text = str(snap.get("working_text", "") or "")
            stp = patched.get("self_test_plan", {}) if isinstance(patched.get("self_test_plan", {}), dict) else {}
            must_contain = stp.get("expect_working_text_contains", []) if isinstance(stp.get("expect_working_text_contains", []), list) else []
            missing_tokens = [t for t in must_contain if t and str(t) not in working_text]
            failure_context = {
                "stage": "validate_candidate",
                "error": err,
                "missing_tokens": missing_tokens,
                "must_contain": must_contain[:10],
                "working_text_len": len(working_text),
            }
            trace.append(
                f"patch.iter{iter_idx}.stage=validate_candidate missing_tokens={len(missing_tokens)}"
            )
            repatched = self._repair_temp_capability_spec(
                patched,
                f"validate_candidate failed(iter={iter_idx}): {err}",
                {
                    "planner_context": planner_context,
                    "failure_context": failure_context,
                    "snapshot": snap if isinstance(snap, dict) else {"snapshot": snap},
                },
            )
            if isinstance(repatched, dict):
                patched = repatched
                trace.append(f"quality_verify.iter{iter_idx}.repair_validate=1")
            else:
                trace.append(f"quality_verify.iter{iter_idx}.repair_validate=0")
                verified = {"pass": False, "reason": err, "must_fix": must_fix, "confidence": 0.2}
                break

        report_dir = sandbox_path(".temp_tools", "_qc_reports")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_name = (
            f"{self._normalize_temp_capability_name(str(patched.get('name', '') or str(spec.get('name', 'temp_tool'))))}"
            f"_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        )
        report_path = report_dir / report_name
        report_payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "user_input": user_input,
            "requirement": self._extract_temp_request_text(user_input),
            "iteration_mode": mode,
            "capability_delta": delta_rows,
            "models": {
                "review": getattr(self._review_llm, "model", ""),
                "repair": getattr(self._repair_llm, "model", ""),
                "verify": getattr(self._verify_llm, "model", ""),
            },
            "strict_skill_rules": self._strict_skill_rules(),
            "temp_protocol_spec": self._temp_protocol_spec(),
            "round": {
                "review": review,
                "repair": repaired if isinstance(repaired, dict) else {},
                "verify": verified,
            },
            "input_spec": spec,
            "output_spec": patched,
            "result": {
                "verify_pass": bool(verified.get("pass", False)),
                "trace": list(trace),
            },
        }
        try:
            report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            trace.append(f"quality_report={report_path}")
        except Exception as exc:
            trace.append(f"quality_report_write_error={exc}")
        gate_reason = ""
        if not vpass:
            gate_reason = str(verified.get("reason", "") or "验证专家未通过，请先修复约束后再创建。").strip()
        trace.append("quality_verify.repair_fallback=0")
        return patched, trace, vpass, gate_reason

    def _should_force_second_generation(self, user_input: str, spec: dict[str, Any], decision: dict[str, Any]) -> bool:
        if str(decision.get("intent", "")).strip().lower() != "create_temp_capability":
            return False
        # Generator didn't manage to produce an executable draft.
        if bool(spec.get("should_create", False)):
            return False
        if str(spec.get("clarify_question", "") or "").strip():
            return False
        if str(spec.get("executor_body", "") or "").strip():
            return False
        conf = float(decision.get("confidence", 0.0) or 0.0)
        if conf < 0.75:
            return False
        text = (user_input or "").strip()
        if len(text) < 8:
            return False
        # Avoid brittle keyword matching (encoding/locale issues).
        # If intent is high-confidence create_temp_capability but generator fails to produce an executable draft,
        # force a second generation.
        return True

    def _force_draft_temp_capability(
        self,
        user_input: str,
        spec_hint: dict[str, Any],
        decision: dict[str, Any],
        *,
        iteration_mode: str = "fix",
        capability_delta: list[str] | None = None,
    ) -> dict[str, Any]:
        fmt = self._format_language_assistant(
            user_input,
            iteration_mode=iteration_mode,
            capability_delta=capability_delta,
        )
        requirement = self._protocolize_temp_requirement(user_input, fmt)
        supported_inputs = self._temp_input_adapters.describe_supported_inputs()
        detected_url = self._extract_first_url(user_input)

        defaults: dict[str, Any] = {
            "should_create": False,
            "name": str(spec_hint.get("name", "") or ""),
            "description": str(spec_hint.get("description", "") or requirement or ""),
            "route": str(spec_hint.get("route", "") or "file"),
            "input_type": str(spec_hint.get("input_type", "") or "text_any"),
            "output_type": "text_any",
            "output_schema": "temp_result_text",
            "side_effect_level": "none",
            "source_requirement": requirement,
            "executor_body": "",
            "trigger_keywords": list(spec_hint.get("trigger_keywords", []) or []),
            "default_target": str(spec_hint.get("default_target", "") or ""),
            "input_adapters": list(spec_hint.get("input_adapters", []) or []),
            "self_test_plan": {},
            "clarify_question": "",
            "confidence": 0.0,
        }

        prompt = (
            "你是 CoreCoder 的临时工具生成器（强制模式）。只输出 JSON。\n"
            "任务：即使你一开始判断不够确定，也必须产出一个“可通过 validate_candidate 的最小可执行草案”。\n"
            "硬规则：\n"
            "- should_create 必须为 true；不得通过 should_create=false 来让系统追问用户。\n"
            "- executor_body 必须非空，且必须只读取 step['args']（禁止 intermediate['url'] 等），成功写 intermediate['working_text'] 并返回 None。\n"
            "- 如果用户要求“保存到本地文件”但没给路径：你必须在 executor_body 内使用沙盒默认路径（例如 sandbox_path/固定命名）写入；不要新增 file_path 作为外部输入。\n"
            "- 若用户需求包含 URL：优先 input_type=url，并在 self_test_plan 中填入 detected_url。\n"
            "- 若用户需求包含“提取网页内容/网页内容/页面内容/提取网页”，executor_body 必须通过 `fetch_tool = owner._get_tool('browser', 'fetch_url_content')` 获取工具实例；然后调用 `fetch_tool.execute(url=..., max_chars=...)` 获取页面文本，并将真实提取/处理结果（或截断摘要）写入 intermediate['working_text']，不得只输出模板 demo；写入后 return None。\n"
            "- 若无法解析 detected_url：仍必须产出能工作的草案（退回到 text_any，但从 step['args']['user_input'] 提取 URL/文本）。\n"
            "\n"
            + self._temp_protocol_spec()
            + self._strict_skill_rules()
            + f"格式语言助手归一化结果: {json.dumps(fmt, ensure_ascii=False)}\n"
            + f"iteration_mode={str(fmt.get('iteration_mode', 'fix'))}\n"
            + f"capability_delta={json.dumps(fmt.get('capability_delta', []), ensure_ascii=False)}\n"
            + f"当前可自动适配的 input_type:\n{supported_inputs}\n"
            + f"detected_url: {detected_url!r}\n"
            + f"默认值: {json.dumps(defaults, ensure_ascii=False)}\n"
            + f"用户需求: {requirement}\n"
        )

        parsed = self._parse_intent_json_with_llm(
            getattr(self, "_temp_tool_coding_llm", self.llm),
            prompt,
            defaults,
            label="coding_expert_force_draft",
        )
        result = dict(defaults)
        if isinstance(parsed, dict):
            result.update({k: parsed.get(k, result[k]) for k in result})

        # Hard post-guards
        result["should_create"] = True
        result["name"] = self._normalize_temp_capability_name(str(result.get("name", "") or "temp_generated"))
        result["route"] = str(result.get("route", "file") or "file").strip().lower()
        if result["route"] not in ("browser", "mail", "file", "coding"):
            result["route"] = "file"
        result["input_type"] = str(result.get("input_type", "text_any") or "text_any").strip()
        result["output_type"] = str(result.get("output_type", "text_any") or "text_any").strip()
        result["output_schema"] = str(result.get("output_schema", "temp_result_text") or "temp_result_text").strip()
        result["side_effect_level"] = str(result.get("side_effect_level", "none") or "none").strip()
        result["description"] = str(result.get("description", "") or requirement or result["name"]).strip()
        result["source_requirement"] = str(result.get("source_requirement", "") or requirement).strip()
        result["trigger_keywords"] = self._normalize_trigger_keywords(result.get("trigger_keywords", []), result["source_requirement"])
        result["default_target"] = str(result.get("default_target", "") or "").strip()
        self_test_plan = result.get("self_test_plan", {})
        result["self_test_plan"] = self._normalize_self_test_plan(self_test_plan)
        # Stabilize URL-based webpage extraction self-test:
        # don't hit real network during temp-tool creation validation.
        need_web = any(k in (user_input or "").lower() for k in ("网页", "webpage", "页面内容", "提取网页", "网页内容"))
        if need_web and str(result.get("input_type", "")).strip().lower() == "url":
            result["self_test_plan"] = {
                "args": {"url": "self_test://webpage"},
                "expect_working_text_contains": [],
            }
        result["clarify_question"] = ""
        result["executor_body"] = str(result.get("executor_body", "")).rstrip()
        result["confidence"] = max(0.5, float(result.get("confidence", 0.5) or 0.5))
        return result

    @staticmethod
    def _normalize_trigger_keywords(raw: Any, source_requirement: str = "") -> list[str]:
        out: list[str] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            items = re.split(r"[\s,，;；]+", raw)
        else:
            items = []
        for item in items:
            v = str(item).strip().lower()
            if v and v not in out:
                out.append(v)
        text = (source_requirement or "").lower()
        for kw in ("github", "仓库", "readme", "提交", "文件", "repo"):
            if kw in text and kw not in out:
                out.append(kw)
        return out

    @staticmethod
    def _normalize_self_test_plan(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {"args": {}, "expect_working_text_contains": []}
        args = raw.get("args", {})
        if not isinstance(args, dict):
            args = {}
        expect = raw.get("expect_working_text_contains", [])
        if isinstance(expect, str):
            expect = [expect]
        if not isinstance(expect, list):
            expect = []
        return {
            "args": args,
            "expect_working_text_contains": [str(x) for x in expect if str(x).strip()],
        }

    def _render_temp_capability_code(self, spec: dict[str, Any]) -> str:
        action_name = f"temp_{spec['name']}"
        return TEMP_TOOL_TEMPLATE.format(
            input_schema="{}",
            output_schema=spec["output_schema"],
            side_effect_level=spec["side_effect_level"],
            route=spec["route"],
            input_type=spec["input_type"],
            output_type=spec["output_type"],
            executor_body=self._indent_executor_body(spec["executor_body"]),
            name=spec["name"],
            action_name=action_name,
            description=spec["description"],
            input_adapters=json.dumps(spec.get("input_adapters", []), ensure_ascii=False, indent=2),
        )

    def _build_temp_metadata(self, spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "trigger_keywords": self._normalize_trigger_keywords(spec.get("trigger_keywords", []), str(spec.get("source_requirement", ""))),
            "default_target": str(spec.get("default_target", "") or "").strip(),
            "source_requirement": str(spec.get("source_requirement", "") or "").strip(),
            "self_test_plan": self._normalize_self_test_plan(spec.get("self_test_plan", {})),
        }

    def _edit_temp_capability_patch_plan(
        self,
        defaults: dict[str, Any],
        supported_inputs: list[str],
        validation_error: str,
        validation_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        edit_defaults = {
            "must_change_fields": [],
            "set_fields": {},
            "executor_body_instructions": [],
            "self_test_plan_instructions": [],
            "keep_fields": ["name", "route"],
            "confidence": 0.5,
            "reasoning": "",
        }
        edit_prompt = (
            "你是 CoreCoder 的编辑专家（edit）。只输出 JSON。\n"
            "任务：根据校验错误与失败上下文，生成一个最小补丁计划 edit_plan。\n"
            "硬规则：\n"
            "1) 不要输出最终 executor_body 源码，不要输出最终 self_test_plan 内容；只输出“指令/规则/要点”。\n"
            "2) 必须输出用于下一步 patch 专家执行的结构化计划。\n"
            "3) 必须优先依据 failure_context（若存在）定位失败发生在哪些字段/协议/自测期望上。\n"
            "字段：must_change_fields(list[str]), set_fields(dict), executor_body_instructions(list[str]), "
            "self_test_plan_instructions(list[str]), keep_fields(list[str]), confidence(0~1), reasoning(str)。\n"
            "约束：\n"
            "a) keep_fields 默认应至少包含 name、route，且 patch 不能改变它们。\n"
            "b) set_fields 仅允许放入简单标量字段（如 input_type/side_effect_level/output_schema/description/source_requirement），不要放入 executor_body 代码。\n"
            "c) executor_body_instructions 必须是可执行的“要做什么”，例如：\n"
            "- 调整 URL 校验：使用 re.match 或 urlparse/rfc3986 风格并设置 missing_url_validation_hint\n"
            "- 使用 owner._get_tool('browser','fetch_url_content').execute(...) 获取网页文本并写入 intermediate['working_text']\n"
            "- 禁止在 executor_body 中调用 owner.llm() 或 owner.fetch_url_content\n"
            "+ self._temp_protocol_spec()\n"
            "+ self._strict_skill_rules()\n"
            f"当前可自动适配的 input_type:\n{supported_inputs}\n"
            f"当前规格(defaults): {json.dumps(defaults, ensure_ascii=False)}\n"
            f"校验错误(validation_error): {validation_error}\n"
            f"失败上下文(validation_snapshot): {json.dumps(validation_snapshot, ensure_ascii=False)}\n"
            "输出：只输出 edit_plan JSON。"
        )
        return self._parse_intent_json_with_llm(
            getattr(self, "_repair_llm", self.llm),
            edit_prompt,
            edit_defaults,
            label="coding_expert_spec_edit_plan",
        )

    def _patch_temp_capability_spec_from_plan(
        self,
        defaults: dict[str, Any],
        supported_inputs: list[str],
        validation_error: str,
        validation_snapshot: dict[str, Any],
        edit_plan: dict[str, Any],
    ) -> dict[str, Any]:
        patch_defaults = dict(defaults)
        set_fields = edit_plan.get("set_fields", {}) if isinstance(edit_plan.get("set_fields", {}), dict) else {}
        must_change_fields = (
            edit_plan.get("must_change_fields", []) if isinstance(edit_plan.get("must_change_fields", []), list) else []
        )
        keep_fields = edit_plan.get("keep_fields", ["name", "route"])
        if not isinstance(keep_fields, list):
            keep_fields = ["name", "route"]

        patch_prompt = (
            "你是 CoreCoder 的补丁专家（patch）。只输出 JSON。\n"
            "任务：根据 edit_plan 修复临时工具 spec，使其满足协议与校验门禁。\n"
            "硬规则：\n"
            "1) 必须只改变 edit_plan 指定的字段与逻辑；其余字段必须保持与 defaults 一致。\n"
            "2) executor_body 必须是合法 Python 函数体，不含 def。\n"
            "3) 成功必须写入 intermediate['working_text'] 并返回 None。\n"
            "4) 临时工具的外部输入一律只能从 step['args'] 读取，禁止从 intermediate 读取原始输入参数，也禁止使用未定义的裸 args 变量。\n"
            "5) 若 edit_plan 指示为网页/抓取：必须使用 owner._get_tool('browser','fetch_url_content').execute(...)。\n"
            "6) 禁止在 executor_body 中调用 owner.llm() 或 owner.fetch_url_content。\n"
            "7) 敏感信息只能从环境变量读取。\n"
            "8) self_test_plan 至少包含 args(dict)，并尽量与需要的验证要点一致。\n"
            "\n"
            "+ self._temp_protocol_spec()\n"
            "+ self._strict_skill_rules()\n"
            f"当前可自动适配的 input_type:\n{supported_inputs}\n"
            f"defaults(spec): {json.dumps(defaults, ensure_ascii=False)}\n"
            f"edit_plan: {json.dumps(edit_plan, ensure_ascii=False)}\n"
            f"校验错误(validation_error): {validation_error}\n"
            f"失败上下文(validation_snapshot): {json.dumps(validation_snapshot, ensure_ascii=False)}\n"
            f"keep_fields(禁止修改): {json.dumps(keep_fields, ensure_ascii=False)}\n"
            f"set_fields(建议写入的简单字段): {json.dumps(set_fields, ensure_ascii=False)}\n"
            f"must_change_fields(需要重点修复的字段): {json.dumps(must_change_fields, ensure_ascii=False)}\n"
            "输出：只输出最终修复后的 spec JSON，字段必须包含：name, description, route, input_type, output_type, "
            "output_schema, side_effect_level, source_requirement, executor_body, trigger_keywords, default_target, input_adapters, self_test_plan。"
        )
        parsed = self._parse_intent_json_with_llm(
            getattr(self, "_repair_llm", self.llm),
            patch_prompt,
            patch_defaults,
            label="coding_expert_spec_patch_from_plan",
        )

        fixed = dict(defaults)
        if isinstance(parsed, dict):
            fixed.update({k: parsed.get(k, fixed[k]) for k in fixed})
        return fixed

    def _repair_temp_capability_spec(
        self,
        spec: dict[str, Any],
        validation_error: str,
        validation_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        supported_inputs = self._temp_input_adapters.describe_supported_inputs()
        defaults = {
            "name": str(spec.get("name", "")),
            "description": str(spec.get("description", "")),
            "route": str(spec.get("route", "file")),
            "input_type": str(spec.get("input_type", "text_any")),
            "output_type": str(spec.get("output_type", "text_any")),
            "output_schema": str(spec.get("output_schema", "temp_result_text")),
            "side_effect_level": str(spec.get("side_effect_level", "none")),
            "source_requirement": str(spec.get("source_requirement", "")),
            "executor_body": str(spec.get("executor_body", "")),
            "trigger_keywords": list(spec.get("trigger_keywords", [])),
            "default_target": str(spec.get("default_target", "")),
            "input_adapters": list(spec.get("input_adapters", []) or []),
            "self_test_plan": dict(spec.get("self_test_plan", {}) or {}),
        }
        snapshot = validation_snapshot if isinstance(validation_snapshot, dict) else {}

        edit_plan = self._edit_temp_capability_patch_plan(
            defaults=defaults,
            supported_inputs=supported_inputs,
            validation_error=validation_error,
            validation_snapshot=snapshot,
        )

        fixed = self._patch_temp_capability_spec_from_plan(
            defaults=defaults,
            supported_inputs=supported_inputs,
            validation_error=validation_error,
            validation_snapshot=snapshot,
            edit_plan=edit_plan if isinstance(edit_plan, dict) else {},
        )
        fixed["name"] = self._normalize_temp_capability_name(str(fixed.get("name", "") or defaults["name"]))
        fixed["trigger_keywords"] = self._normalize_trigger_keywords(
            fixed.get("trigger_keywords", []),
            str(fixed.get("source_requirement", "")),
        )
        fixed["input_adapters"] = fixed.get("input_adapters", []) if isinstance(fixed.get("input_adapters", []), list) else []
        fixed["self_test_plan"] = self._normalize_self_test_plan(fixed.get("self_test_plan", {}))
        return fixed

    def _runtime_spec_from_record(self, record: TempCapabilityRecord) -> dict[str, Any]:
        action_spec = self._get_action_spec(record.action_name)
        code = ""
        try:
            code = Path(record.file_path).read_text(encoding="utf-8")
        except Exception:
            code = ""
        register_payload: dict[str, Any] = {}
        try:
            module = self._temp_registry.modules.get(record.name)
            if module is not None and hasattr(module, "register"):
                payload = module.register()
                if isinstance(payload, dict):
                    register_payload = payload
        except Exception:
            register_payload = {}
        return {
            "name": record.name,
            "description": record.description,
            "route": record.route,
            "input_type": str(action_spec.get("input_type", "text_any") or "text_any"),
            "output_type": str(action_spec.get("output_type", "text_any") or "text_any"),
            "output_schema": str(action_spec.get("output_schema", "temp_result_text") or "temp_result_text"),
            "side_effect_level": str(action_spec.get("side_effect_level", "none") or "none"),
            "source_requirement": record.source_requirement,
            "executor_body": self._extract_executor_body_from_temp_code(code),
            "trigger_keywords": list(record.trigger_keywords),
            "default_target": record.default_target,
            "input_adapters": list(register_payload.get("input_adapters", []) or []),
            "self_test_plan": dict(record.self_test_plan or {}),
        }

    def _runtime_repair_temp_capability(
        self,
        record: TempCapabilityRecord,
        *,
        user_input: str,
        error_text: str,
        runtime_snapshot: dict[str, Any],
    ) -> tuple[bool, str, list[str]]:
        file_path = Path(record.file_path)
        spec = self._runtime_spec_from_record(record)
        trace: list[str] = []
        for attempt in range(1, 4):
            spec = self._repair_temp_capability_spec(
                spec,
                f"runtime invocation failed: {error_text}; user_input={user_input}; failure={json.dumps(runtime_snapshot.get('failure', {}), ensure_ascii=False)}",
                runtime_snapshot,
            )
            code = self._render_temp_capability_code(spec)
            try:
                file_path.write_text(code, encoding="utf-8")
            except OSError as exc:
                return False, str(exc), trace
            validation = self._temp_registry.validate_candidate(file_path)
            if not bool(validation.get("ok", False)):
                err = str(validation.get("error", "unknown validation error"))
                trace.append(f"runtime_repair_attempt={attempt}: {err}")
                runtime_snapshot = dict(runtime_snapshot)
                runtime_snapshot["validation_snapshot"] = validation.get("snapshot", {})
                continue
            ok, detail = self.load_temp_capability(
                str(file_path),
                source_requirement=str(spec.get("source_requirement", "")),
                metadata=self._build_temp_metadata(spec),
            )
            if ok:
                trace.append(f"runtime_repair_attempt={attempt}: reload_ok")
                return True, detail, trace
            trace.append(f"runtime_repair_attempt={attempt}: reload_failed: {detail}")
        return False, "runtime auto-repair exhausted", trace

    def _temp_capability_preview(self, spec: dict[str, Any], file_path: Path) -> str:
        return (
            f"名称：{spec['name']}\n"
            f"描述：{spec['description']}\n"
            f"挂载路由：{spec['route']}\n"
            f"能力图：{spec['input_type']} -> {spec['output_type']}\n"
            f"副作用级别：{spec['side_effect_level']}\n"
            f"自测计划：{self._normalize_self_test_plan(spec.get('self_test_plan', {}))}\n"
            f"临时文件：{file_path}\n"
            f"原始需求：{spec['source_requirement']}\n\n"
            "回复：确认创建 / 修改为 ... / 取消"
        )

    def _maybe_stage_temp_capability(
        self,
        user_input: str,
        *,
        iteration_mode: str = "fix",
        capability_delta: list[str] | None = None,
    ) -> str | None:
        decision = self._classify_temp_capability_intent(user_input)
        if str(decision.get("intent", "normal_task")) != "create_temp_capability":
            return None
        mode = str(iteration_mode or "fix").strip().lower()
        if mode not in {"fix", "evolve"}:
            mode = "fix"
        delta_rows = [str(x).strip() for x in (capability_delta or []) if str(x).strip()]
        fmt = self._format_language_assistant(
            user_input,
            iteration_mode=mode,
            capability_delta=delta_rows,
        )
        spec = self._draft_temp_capability(
            user_input,
            iteration_mode=mode,
            capability_delta=delta_rows,
        )
        if not bool(spec.get("should_create", False)):
            if self._should_force_second_generation(user_input, spec, decision):
                forced = self._force_draft_temp_capability(
                    user_input,
                    spec,
                    decision,
                    iteration_mode=mode,
                    capability_delta=delta_rows,
                )
                if bool(forced.get("should_create", False)):
                    spec = forced
            if bool(spec.get("should_create", False)):
                spec, pre_qc_trace = self._pre_qc_rebuild_spec(
                    user_input=user_input,
                    spec=spec,
                    decision=decision,
                    iteration_mode=mode,
                    capability_delta=delta_rows,
                )
                quality_trace: list[str] = []
                gate_ok = True
                gate_reason = ""
                spec, quality_trace, gate_ok, gate_reason = self._run_temp_capability_quality_team(
                    user_input,
                    spec,
                    iteration_mode=mode,
                    capability_delta=delta_rows,
                )
                if not gate_ok:
                    return self._format_evidence_result(
                        summary="质控门禁未通过，暂不进入创建确认。",
                        route_key="coding",
                        evidence_lines=[f"intent_decision={decision}", f"temp_name={spec.get('name', '')}"] + pre_qc_trace + quality_trace,
                        detail=gate_reason or "验证专家未通过，请补充约束或修改需求后重试。",
                    )
                file_path = sandbox_path(".temp_tools", f"{spec['name']}.py")
                code = self._render_temp_capability_code(spec)
                self._pending_temp_capability = {
                    "state": "confirm",
                    "spec": spec,
                    "file_path": str(file_path),
                    "code": code,
                    "original_user_input": user_input,
                    "intent_decision": decision,
                    "forced_generation": True,
                }
                return self._format_evidence_result(
                    summary="已强制生成临时工具草案，请确认后创建并自动加载。",
                    route_key="coding",
                    evidence_lines=[
                        f"intent_decision={decision}",
                        f"temp_iteration_mode={mode}",
                        f"format_assistant.expert_count={fmt.get('recommended_coding_expert_count', 1)}",
                        f"format_assistant.inputs={len(fmt.get('inferred_inputs', []))}",
                        f"format_assistant.capability_delta={fmt.get('capability_delta', [])}",
                        f"temp_name={spec['name']}",
                        f"temp_action=temp_{spec['name']}",
                        f"temp_file={file_path}",
                        "forced_generation=1",
                    ] + quality_trace,
                    detail=self._temp_capability_preview(spec, file_path),
                )
                if pre_qc_trace:
                    return self._format_evidence_result(
                        summary="已强制生成并通过本地协议预检后进入创建确认。",
                        route_key="coding",
                        evidence_lines=[
                            f"intent_decision={decision}",
                            f"temp_iteration_mode={mode}",
                            f"format_assistant.expert_count={fmt.get('recommended_coding_expert_count', 1)}",
                            f"format_assistant.inputs={len(fmt.get('inferred_inputs', []))}",
                            f"format_assistant.capability_delta={fmt.get('capability_delta', [])}",
                            f"temp_name={spec['name']}",
                            f"temp_action=temp_{spec['name']}",
                            f"temp_file={file_path}",
                            "forced_generation=1",
                        ] + pre_qc_trace + quality_trace,
                        detail=self._temp_capability_preview(spec, file_path),
                    )
                return self._format_evidence_result(
                    summary="已强制生成临时工具草案，请确认后创建并自动加载。",
                    route_key="coding",
                    evidence_lines=[
                        f"intent_decision={decision}",
                        f"temp_iteration_mode={mode}",
                        f"format_assistant.expert_count={fmt.get('recommended_coding_expert_count', 1)}",
                        f"format_assistant.inputs={len(fmt.get('inferred_inputs', []))}",
                        f"format_assistant.capability_delta={fmt.get('capability_delta', [])}",
                        f"temp_name={spec['name']}",
                        f"temp_action=temp_{spec['name']}",
                        f"temp_file={file_path}",
                        "forced_generation=1",
                    ] + quality_trace,
                    detail=self._temp_capability_preview(spec, file_path),
                )

            question = str(spec.get("clarify_question", "")).strip() or "请再具体一点描述你想让临时工具做什么。"
            self._pending_temp_capability = {
                "state": "clarify",
                "original_user_input": user_input,
                "clarify_question": question,
                "intent_decision": decision,
            }
            return self._format_evidence_result(
                summary="我判断你想创建临时工具，但需求还需要补一处关键信息。",
                route_key="coding",
                evidence_lines=[f"intent_decision={decision}", f"temp_draft={spec}"],
                detail=question,
            )
        file_path = sandbox_path(".temp_tools", f"{spec['name']}.py")
        spec, pre_qc_trace = self._pre_qc_rebuild_spec(
            user_input=user_input,
            spec=spec,
            decision=decision,
            iteration_mode=mode,
            capability_delta=delta_rows,
        )
        quality_trace = []
        gate_ok = True
        gate_reason = ""
        spec, quality_trace, gate_ok, gate_reason = self._run_temp_capability_quality_team(
            user_input,
            spec,
            iteration_mode=mode,
            capability_delta=delta_rows,
        )
        if not gate_ok:
            return self._format_evidence_result(
                summary="质控门禁未通过，暂不进入创建确认。",
                route_key="coding",
                evidence_lines=[f"intent_decision={decision}", f"temp_name={spec.get('name', '')}"] + pre_qc_trace + quality_trace,
                detail=gate_reason or "验证专家未通过，请补充约束或修改需求后重试。",
            )
        file_path = sandbox_path(".temp_tools", f"{spec['name']}.py")
        code = self._render_temp_capability_code(spec)
        self._pending_temp_capability = {
            "state": "confirm",
            "spec": spec,
            "file_path": str(file_path),
            "code": code,
            "original_user_input": user_input,
            "intent_decision": decision,
        }
        return self._format_evidence_result(
            summary="已生成临时工具草案，请确认后创建并自动加载。",
            route_key="coding",
            evidence_lines=[
                f"intent_decision={decision}",
                f"temp_iteration_mode={mode}",
                f"format_assistant.expert_count={fmt.get('recommended_coding_expert_count', 1)}",
                f"format_assistant.inputs={len(fmt.get('inferred_inputs', []))}",
                f"format_assistant.capability_delta={fmt.get('capability_delta', [])}",
                f"temp_name={spec['name']}",
                f"temp_action=temp_{spec['name']}",
                f"temp_file={file_path}",
            ] + pre_qc_trace + quality_trace,
            detail=self._temp_capability_preview(spec, file_path),
        )

    def _handle_pending_temp_capability(self, user_input: str) -> str | None:
        pending = self._pending_temp_capability
        if pending is None:
            return None
        text = (user_input or "").strip()
        if not text:
            return None
        if self._is_cancel_text(text):
            self._pending_temp_capability = None
            return self._format_evidence_result(
                summary="已取消临时工具创建草案。",
                route_key="coding",
                evidence_lines=["pending_temp_capability=cleared"],
                detail="未写入任何临时文件，也未注册新能力。",
            )
        if pending.get("state") == "clarify":
            merged = f"{pending.get('original_user_input', '')}\n补充说明：{text}"
            self._pending_temp_capability = None
            return self._maybe_stage_temp_capability(merged)
        mod = self._extract_modify_instruction(text)
        if mod:
            merged = f"{pending.get('original_user_input', '')}\n修改要求：{mod}"
            self._pending_temp_capability = None
            return self._maybe_stage_temp_capability(merged)
        if not self._is_confirm_text(text):
            # Natural follow-up UX: plain pasted lines like "1. ...\n2. ..."
            # are treated as additional constraints and trigger a rebuilt draft.
            merged = f"{pending.get('original_user_input', '')}\n补充要求：{text}"
            self._pending_temp_capability = None
            return self._maybe_stage_temp_capability(merged)
        spec = dict(pending.get("spec", {}) or {})
        file_path = Path(str(pending.get("file_path", "")).strip())
        if not spec or not file_path:
            self._pending_temp_capability = None
            return "Error: invalid pending temp capability."
        validation_trace: list[str] = []
        ok = False
        detail = ""
        for attempt in range(1, 4):
            code = self._render_temp_capability_code(spec)
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(code, encoding="utf-8")
            except OSError as exc:
                self._pending_temp_capability = None
                return self._format_evidence_result(
                    summary="临时工具文件创建失败。",
                    route_key="coding",
                    evidence_lines=[f"temp_file={file_path}"],
                    detail=str(exc),
                )
            validation = self._temp_registry.validate_candidate(file_path)
            if bool(validation.get("ok", False)):
                ok, detail = self.load_temp_capability(
                    str(file_path),
                    source_requirement=str(spec.get("source_requirement", "")),
                    metadata=self._build_temp_metadata(spec),
                )
                if ok:
                    break
                validation_trace.append(f"attempt={attempt}: load failed: {detail}")
            else:
                error_text = str(validation.get("error", "unknown validation error"))
                validation_trace.append(f"attempt={attempt}: {error_text}")
                spec = self._repair_temp_capability_spec(
                    spec,
                    error_text,
                    validation.get("snapshot", {}),
                )
        self._pending_temp_capability = None
        if not ok:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                file_path.with_suffix(".meta.json").unlink(missing_ok=True)
            except OSError:
                pass
            self._set_last_temp_failure(
                {
                    "stage": "create_validate_load",
                    "user_input": str(pending.get("original_user_input", "")),
                    "record_name": str(spec.get("name", "")),
                    "action_name": f"temp_{spec.get('name', '')}",
                    "error": detail or "validate/load failed",
                    "validation_trace": list(validation_trace),
                    "file_path": str(file_path),
                }
            )
            return self._format_evidence_result(
                summary="临时工具经过自动修复后仍未能通过校验并加载。",
                route_key="coding",
                evidence_lines=[f"temp_file={file_path}"] + validation_trace,
                detail=detail or "请缩小需求范围后重试。",
            )
        return self._format_evidence_result(
            summary="临时工具已创建并自动加载到当前会话。",
            route_key="coding",
            evidence_lines=[
                f"temp_file={file_path}",
                f"loaded={detail}",
            ] + validation_trace,
            detail=(
                f"你现在可以直接使用这个临时能力。\n"
                f"查看：/temp-tools\n"
                f"调试：/debug\n"
                f"保留：/temp-tool keep {spec['name']}\n"
                f"丢弃：/temp-tool discard {spec['name']}"
            ),
        )

    def _maybe_run_temp_capability_query(
        self,
        user_input: str,
        preferred_record_name: str = "",
        *,
        step_args_override: dict[str, Any] | None = None,
        skip_dialog_trigger: bool = False,
    ) -> str | None:
        if not hasattr(self, "_temp_registry"):
            return None
        temp_context = self._is_temp_chain_context_input(user_input)
        record = None
        pref = str(preferred_record_name or "").strip()
        if pref:
            record = self._temp_registry.get_record(pref)
        if record is None:
            record = self._temp_registry.match_query(user_input)
        if record is None and temp_context:
            items = self._temp_registry.list_records()
            if items:
                # Fallback to the most recently created temp capability in temp-context input.
                record = items[-1]
        if record is None:
            return None
        executor = self._get_step_executor(record.action_name)
        if executor is None:
            return None
        action_spec = self._get_action_spec(record.action_name)
        dialog_trigger = (not skip_dialog_trigger) and self._is_temp_capability_dialog_trigger(user_input)

        if step_args_override is not None:
            step_args = dict(step_args_override)
            step_args, injected_trace = self._inject_temp_runtime_args(user_input, action_spec, step_args)
        else:
            step_args = self._build_temp_step_args(user_input, action_spec)
            if step_args is None:
                if dialog_trigger:
                    step_args = {"user_input": user_input}
                    injected_trace = []
                elif temp_context:
                    example = self._temp_input_format_example(record, action_spec)
                    self._set_last_temp_failure(
                        {
                            "stage": "runtime_query_failed",
                            "user_input": user_input,
                            "base_user_input": user_input.split("\n重试定制要求：", 1)[0].strip(),
                            "customization": user_input.split("\n重试定制要求：", 1)[1].strip()
                            if "\n重试定制要求：" in user_input
                            else "",
                            "record_name": record.name,
                            "action_name": record.action_name,
                            "error": "step_args_build_failed: missing required runtime args",
                            "failure": {
                                "failure_type": "input_extraction",
                                "reason": "step_args build failed in temp context",
                                "classifier": "rule_based",
                                "confidence": 0.9,
                            },
                        }
                    )
                    return self._format_evidence_result(
                        summary="已进入临时工具链，但参数提取失败。",
                        route_key=record.route,
                        evidence_lines=[
                            f"temp_match={record.name}",
                            "temp_chain_isolated=1",
                            "step_args_build=failed",
                        ],
                        detail="请补充该临时工具所需参数（例如 save_path/file_path）。\n\n" + example,
                    )
                else:
                    return None
            step_args, injected_trace = self._inject_temp_runtime_args(user_input, action_spec, step_args)

        evidence: list[str] = [f"temp_match={record.name}", f"temp_keywords={record.trigger_keywords}"]
        if injected_trace:
            evidence.extend(injected_trace)

        # For B) UX: open/execute always enters a runtime dialog first.
        if dialog_trigger:
            required_args = self._required_args_for_action_spec(action_spec)
            missing_args = [a for a in required_args if not str(step_args.get(a, "")).strip()]
            self._pending_temp_capability_dialog = {
                "record_name": record.name,
                "route": record.route,
                "action_name": record.action_name,
                "action_spec": dict(action_spec),
                "step_args": dict(step_args),
                "required_args": required_args,
                "missing_args": missing_args,
            }
            return self._format_temp_capability_dialog_prompt(
                record=record,
                action_spec=action_spec,
                step_args=step_args,
                required_args=required_args,
                missing_args=missing_args,
            )

        intermediate: dict[str, str] = {}
        err = executor(
            {"route": record.route, "action": record.action_name, "args": step_args},
            intermediate,
            evidence,
        )
        if err is not None:
            runtime_snapshot = {
                "user_input": user_input,
                "action_name": record.action_name,
                "record_name": record.name,
                "step_args": dict(step_args),
                "evidence": list(evidence),
                "intermediate": dict(intermediate),
                "error": str(err),
            }
            failure = self._classify_runtime_failure(
                record=record,
                action_spec=action_spec,
                step_args=step_args,
                error_text=str(err),
                runtime_snapshot=runtime_snapshot,
            )
            evidence.append(
                f"runtime_failure={failure.get('failure_type')} classifier={failure.get('classifier')} confidence={failure.get('confidence')}"
            )
            repaired_args, input_repair_trace = self._repair_runtime_inputs(
                failure=failure,
                user_input=user_input,
                step_args=step_args,
            )
            if repaired_args != step_args:
                retry_evidence: list[str] = evidence + input_repair_trace + ["runtime_retry=input_repair"]
                retry_intermediate: dict[str, str] = {}
                retry_err = executor(
                    {"route": record.route, "action": record.action_name, "args": repaired_args},
                    retry_intermediate,
                    retry_evidence,
                )
                if retry_err is None:
                    retry_detail = (
                        retry_intermediate.get("working_text")
                        or retry_intermediate.get("variant_json")
                        or retry_intermediate.get("variant_text")
                        or "Done."
                    )
                    self._last_temp_capability_failure = None
                    return self._format_evidence_result(
                        summary="临时能力首次执行失败，但已通过参数适配修复并重试成功。",
                        route_key=record.route,
                        evidence_lines=retry_evidence,
                        detail=retry_detail,
                    )
                evidence = retry_evidence
                err = str(retry_err)
                runtime_snapshot = {
                    "user_input": user_input,
                    "action_name": record.action_name,
                    "record_name": record.name,
                    "step_args": dict(repaired_args),
                    "evidence": list(retry_evidence),
                    "intermediate": dict(retry_intermediate),
                    "error": err,
                    "failure": failure,
                }
                step_args = repaired_args
                failure = self._classify_runtime_failure(
                    record=record,
                    action_spec=action_spec,
                    step_args=step_args,
                    error_text=str(err),
                    runtime_snapshot=runtime_snapshot,
                )
            if str(failure.get("failure_type", "")) in {"external_env", "external_http"}:
                self._set_last_temp_failure({
                    "user_input": user_input,
                    "base_user_input": user_input.split("\n重试定制要求：", 1)[0].strip(),
                    "customization": user_input.split("\n重试定制要求：", 1)[1].strip() if "\n重试定制要求：" in user_input else "",
                    "record_name": record.name,
                    "action_name": record.action_name,
                    "error": str(err),
                    "failure": dict(failure),
                })
                return self._format_evidence_result(
                    summary="临时能力已命中，但失败看起来来自外部环境或目标服务，已停止盲目自修。",
                    route_key=record.route,
                    evidence_lines=evidence,
                    detail=f"{err}\n分类原因：{failure.get('reason', '')}",
                )
            repaired, detail, repair_trace = self._runtime_repair_temp_capability(
                record,
                user_input=user_input,
                error_text=str(err),
                runtime_snapshot={**runtime_snapshot, "failure": failure},
            )
            if repaired:
                reloaded_record = self._temp_registry.get_record(record.name) or record
                reloaded_executor = self._get_step_executor(reloaded_record.action_name)
                if reloaded_executor is not None:
                    retry_evidence: list[str] = evidence + repair_trace + ["runtime_retry=1"]
                    retry_intermediate: dict[str, str] = {}
                    retry_err = reloaded_executor(
                        {"route": reloaded_record.route, "action": reloaded_record.action_name, "args": dict(step_args)},
                        retry_intermediate,
                        retry_evidence,
                    )
                    if retry_err is None:
                        retry_detail = (
                            retry_intermediate.get("working_text")
                            or retry_intermediate.get("variant_json")
                            or retry_intermediate.get("variant_text")
                            or "Done."
                        )
                        self._last_temp_capability_failure = None
                        return self._format_evidence_result(
                            summary="临时能力首次执行失败，但已自动自修、热重载并重试成功。",
                            route_key=reloaded_record.route,
                            evidence_lines=retry_evidence,
                            detail=retry_detail,
                        )
                    evidence = retry_evidence
                    err = f"{err}; retry_error={retry_err}"
            self._set_last_temp_failure({
                "user_input": user_input,
                "base_user_input": user_input.split("\n重试定制要求：", 1)[0].strip(),
                "customization": user_input.split("\n重试定制要求：", 1)[1].strip() if "\n重试定制要求：" in user_input else "",
                "record_name": record.name,
                "action_name": record.action_name,
                "error": str(err),
                "failure": dict(failure) if isinstance(failure, dict) else {},
                "step_args": dict(step_args),
            })
            return self._format_evidence_result(
                summary="临时能力已命中，但执行失败。",
                route_key=record.route,
                evidence_lines=evidence + (repair_trace if 'repair_trace' in locals() else []),
                detail=(
                    str(err)
                    + (f"\n自动自修结果：{detail}" if 'detail' in locals() else "")
                    + (
                        "\n\n"
                        + self._temp_input_format_example(record, action_spec)
                        if isinstance(failure, dict)
                        and str(failure.get("failure_type", "")).strip().lower() == "input_extraction"
                        else ""
                    )
                ),
            )
        detail = (
            intermediate.get("working_text")
            or intermediate.get("variant_json")
            or intermediate.get("variant_text")
            or "Done."
        )
        self._last_temp_capability_failure = None
        return self._format_evidence_result(
            summary="已命中临时能力并执行完成。",
            route_key=record.route,
            evidence_lines=evidence,
            detail=detail,
        )

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
        if hasattr(self, "temp_capabilities_debug_preview"):
            lines.append("")
            lines.append(self.temp_capabilities_debug_preview())
        return "\n".join(lines)
