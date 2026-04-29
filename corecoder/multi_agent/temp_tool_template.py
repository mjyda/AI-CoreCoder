from __future__ import annotations

TEMP_TOOL_TEMPLATE = """from __future__ import annotations

from typing import Any

ACTION_SPEC = {{
    "input_schema": {input_schema},
    "output_schema": {output_schema!r},
    "side_effect_level": {side_effect_level!r},
    "route": {route!r},
    "input_type": {input_type!r},
    "output_type": {output_type!r},
}}


def execute(owner: Any, step: dict[str, Any], intermediate: dict[str, str], evidence: list[str]) -> str | None:
{executor_body}


def register() -> dict[str, Any]:
    return {{
        "name": {name!r},
        "action_name": {action_name!r},
        "description": {description!r},
        "action_spec": ACTION_SPEC,
        "input_adapters": {input_adapters},
        "executor": execute,
    }}
"""
