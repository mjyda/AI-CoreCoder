from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re

SESSION_SNAPSHOT_VERSION = 1


def _extract_file_path_from_tool_result(text: str) -> str | None:
    """Best-effort: recover a sandbox file path from expert evidence / tool lines."""
    patterns = (
        r"file_path['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]",
        r"'file_path'\s*:\s*'([^']+)'",
        r'"file_path"\s*:\s*"([^"]+)"',
    )
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            cand = m.group(1).strip()
            if cand:
                return cand
    m2 = re.search(r"(D:\\corecodertest\\[^\s\n'\"|<>]+)", text, re.I)
    if m2:
        return m2.group(1).rstrip(".,;)")
    return None


def _normalize_captured_file_path(path: str) -> str:
    """Normalize paths parsed from evidence (avoids odd escaping in display / reuse)."""
    s = path.strip().strip("\"'")
    try:
        return str(Path(s).resolve())
    except (OSError, ValueError, RuntimeError):
        return str(Path(s))


@dataclass
class AssistantSessionContext:
    """In-memory session context for one REPL / one SuperAssistant instance."""

    max_turns: int = 12
    turns: list[dict[str, str]] = field(default_factory=list)
    last_route: str = ""
    last_file_path: str | None = None
    last_user_message: str = ""
    semantic_preferences: dict[str, str] = field(default_factory=dict)
    semantic_success_patterns: list[dict[str, str]] = field(default_factory=list)
    semantic_corrections: list[dict[str, str]] = field(default_factory=list)

    def clear(self) -> None:
        self.turns.clear()
        self.last_route = ""
        self.last_file_path = None
        self.last_user_message = ""
        self.semantic_preferences.clear()
        self.semantic_success_patterns.clear()
        self.semantic_corrections.clear()

    def record_turn(self, user_input: str, route: str, result: str) -> None:
        self.last_user_message = user_input.strip()[:500]
        summary = re.sub(r"\s+", " ", result).strip()[:400]
        self.turns.append(
            {
                "user": user_input[:240],
                "route": route,
                "summary": summary,
            }
        )
        while len(self.turns) > self.max_turns:
            self.turns.pop(0)
        self.last_route = route
        path = _extract_file_path_from_tool_result(result)
        if path:
            self.last_file_path = _normalize_captured_file_path(path)

    def format_for_prompt(self) -> str:
        lines: list[str] = []
        if self.last_file_path:
            lines.append(f"- 最近写入/操作的沙箱文件：`{self.last_file_path}`")
        if self.last_route:
            lines.append(f"- 上一轮分派专家：`{self.last_route}`")
        if self.turns:
            recent = self.turns[-min(4, len(self.turns)) :]
            lines.append("- 最近对话摘要：")
            for t in recent:
                u = t.get("user", "")
                tail = "…" if len(u) > 80 else ""
                lines.append(f"  • [{t.get('route', '')}] {u[:80]}{tail}")
        if self.semantic_preferences:
            pref = ", ".join(f"{k}={v}" for k, v in sorted(self.semantic_preferences.items()))
            lines.append(f"- 语义偏好：{pref}")
        if not lines:
            return ""
        return "【会话上下文（指代「刚才」「这个文件」「继续」时请优先参考）】\n" + "\n".join(lines)

    def format_semantic_hint(self) -> str:
        parts: list[str] = []
        if self.semantic_preferences:
            pref = "; ".join(f"{k}={v}" for k, v in sorted(self.semantic_preferences.items()))
            parts.append(f"用户偏好：{pref}")
        if self.semantic_success_patterns:
            recent = self.semantic_success_patterns[-2:]
            rows = [f"{r.get('utterance','')} -> {r.get('intent','')}" for r in recent]
            parts.append("近期成功模式：" + " | ".join(rows))
        if self.semantic_corrections:
            recent = self.semantic_corrections[-2:]
            rows = [f"{r.get('utterance','')} => {r.get('corrected','')}" for r in recent]
            parts.append("近期纠偏：" + " | ".join(rows))
        return "\n".join(parts)

    def record_semantic_success(self, utterance: str, intent: str) -> None:
        self.semantic_success_patterns.append(
            {"utterance": utterance[:120], "intent": intent[:200]}
        )
        self.semantic_success_patterns = self.semantic_success_patterns[-8:]

    def record_semantic_correction(self, utterance: str, corrected: str) -> None:
        self.semantic_corrections.append(
            {"utterance": utterance[:120], "corrected": corrected[:200]}
        )
        self.semantic_corrections = self.semantic_corrections[-8:]

    def snapshot_dict(self) -> dict[str, object]:
        return {
            "version": SESSION_SNAPSHOT_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "max_turns": self.max_turns,
            "turns": list(self.turns),
            "last_route": self.last_route,
            "last_file_path": self.last_file_path,
            "last_user_message": self.last_user_message,
            "semantic_preferences": dict(self.semantic_preferences),
            "semantic_success_patterns": list(self.semantic_success_patterns),
            "semantic_corrections": list(self.semantic_corrections),
        }

    def write_snapshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.snapshot_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def read_snapshot(path: Path) -> AssistantSessionContext | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(data, dict):
            return None
        if int(data.get("version", 1)) != SESSION_SNAPSHOT_VERSION:
            return None
        max_turns = max(1, min(int(data.get("max_turns", 12)), 100))
        ctx = AssistantSessionContext(max_turns=max_turns)
        turns = data.get("turns")
        if isinstance(turns, list):
            clean: list[dict[str, str]] = []
            for item in turns:
                if not isinstance(item, dict):
                    continue
                clean.append(
                    {
                        "user": str(item.get("user", ""))[:240],
                        "route": str(item.get("route", ""))[:64],
                        "summary": str(item.get("summary", ""))[:2000],
                    }
                )
            ctx.turns = clean
        ctx.last_route = str(data.get("last_route", ""))[:64]
        lf = data.get("last_file_path")
        ctx.last_file_path = str(lf)[:2048] if lf is not None else None
        ctx.last_user_message = str(data.get("last_user_message", ""))[:500]
        prefs = data.get("semantic_preferences", {})
        if isinstance(prefs, dict):
            ctx.semantic_preferences = {str(k)[:60]: str(v)[:120] for k, v in prefs.items()}
        s_patterns = data.get("semantic_success_patterns", [])
        if isinstance(s_patterns, list):
            ctx.semantic_success_patterns = [
                {
                    "utterance": str(x.get("utterance", ""))[:120],
                    "intent": str(x.get("intent", ""))[:200],
                }
                for x in s_patterns
                if isinstance(x, dict)
            ][-8:]
        s_corr = data.get("semantic_corrections", [])
        if isinstance(s_corr, list):
            ctx.semantic_corrections = [
                {
                    "utterance": str(x.get("utterance", ""))[:120],
                    "corrected": str(x.get("corrected", ""))[:200],
                }
                for x in s_corr
                if isinstance(x, dict)
            ][-8:]
        return ctx
