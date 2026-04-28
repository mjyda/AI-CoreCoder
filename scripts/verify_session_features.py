#!/usr/bin/env python3
"""无 API 的自检：会话快照 + 路由上下文 + 路径解析。运行: python scripts/verify_session_features.py"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from corecoder.multi_agent import (
    AssistantSessionContext,
    SuperAssistant,
    _extract_file_path_from_tool_result,
    _route_intent,
)
from corecoder.tools.sandbox import DEFAULT_SESSION_SNAPSHOT_NAME, SANDBOX_ROOT

DEMO_JSON_PATH = str(SANDBOX_ROOT / "demo.json")
DEMO_JSON_PATH_ESCAPED = DEMO_JSON_PATH.replace("\\", "\\\\")


def test_path_extract() -> None:
    raw = f"- tool=write_file args={{'file_path': '{DEMO_JSON_PATH_ESCAPED}', 'format': 'json'}}"
    p = _extract_file_path_from_tool_result(raw)
    assert p and p.lower().endswith("demo.json"), p


def test_snapshot_roundtrip(tmp: Path) -> None:
    c = AssistantSessionContext(max_turns=5)
    c.record_turn("保存前十条到 test.json", "browser", raw)
    c.write_snapshot(tmp)
    c2 = AssistantSessionContext.read_snapshot(tmp)
    assert c2 is not None
    assert c2.last_route == "browser"
    assert c2.last_file_path and "corecodertest" in c2.last_file_path.lower()
    assert len(c2.turns) == 1
    assert c2.turns[0]["route"] == "browser"


def test_route_with_context() -> None:
    ctx = AssistantSessionContext()
    ctx.last_route = "browser"
    ctx.last_file_path = str(SANDBOX_ROOT / "test1.json")
    r = _route_intent("刚才保存的那个文件内容改成合法 JSON", ctx)
    assert r == "file", r


def test_resolve_snapshot_path() -> None:
    p = SuperAssistant._resolve_snapshot_path(None)
    assert p.name == DEFAULT_SESSION_SNAPSHOT_NAME
    assert SANDBOX_ROOT.resolve() in p.parents or p.parent == SANDBOX_ROOT.resolve()


raw = (
    "[浏览器专家] ok\n\n证据输出：\n"
    f"- tool=write_file args={{'file_path': '{DEMO_JSON_PATH_ESCAPED}', 'format': 'json'}}\n"
)


def main() -> None:
    test_path_extract()
    test_route_with_context()
    test_resolve_snapshot_path()
    with tempfile.TemporaryDirectory() as td:
        test_snapshot_roundtrip(Path(td) / "snap.json")
    print("verify_session_features: ALL OK")
    print("  - path extract from write_file evidence")
    print("  - snapshot JSON round-trip")
    print("  - routing with ctx -> file (follow-up)")
    print("  - default snapshot path under sandbox")


if __name__ == "__main__":
    main()
