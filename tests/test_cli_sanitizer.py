from corecoder.cli import (
    _has_required_headings,
    _sanitize_final_output,
    _should_enforce_structured_output,
)


def test_sanitize_removes_protocol_tags():
    raw = """
<tool_call>
<function=bash>
<parameter=command>dir</parameter>
</function>
</tool_call>
已实现功能
"""
    cleaned = _sanitize_final_output(raw)
    assert "<tool_call>" not in cleaned
    assert "<function=bash>" not in cleaned
    assert "已实现功能" in cleaned


def test_sanitize_deduplicates_blocks():
    raw = "A\n\nA\n\nB"
    cleaned = _sanitize_final_output(raw)
    assert cleaned == "A\n\nB"


def test_heading_validator_requires_all_sections():
    missing = "已实现功能\n项目结构摘要"
    assert _has_required_headings(missing) is False

    full = "\n".join(
        [
            "已实现功能",
            "项目结构摘要",
            "实际执行命令",
            "验证结果",
            "已知限制",
            "下一步建议",
            "协作编排执行记录",
        ]
    )
    assert _has_required_headings(full) is True


def test_structured_output_enforced_only_for_task_intent():
    assert _should_enforce_structured_output("请对 demo/todo_app 做总结") is True
    assert _should_enforce_structured_output("帮我做交付报告") is True
    assert _should_enforce_structured_output("你哈") is False
    assert _should_enforce_structured_output("/help") is False
