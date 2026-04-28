"""Skill discovery and prompt injection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    content: str
    path: str


def _extract_frontmatter_field(text: str, field: str) -> str:
    """Extract one YAML frontmatter field with a lightweight parser."""
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return ""

    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)#按冒号分割，最多分割 1 次
        if key.strip() == field:# field 就是要查找的字段名
            return value.strip().strip("'\"")
    return ""


def _discover_skill_dirs() -> list[Path]:
    """Return skill directories in priority order."""
    project_root = Path.cwd()
    dirs = [
        project_root / ".cursor" / "skills",
        Path.home() / ".cursor" / "skills",
    ]
    return [d for d in dirs if d.exists() and d.is_dir()]


def load_skills() -> list[Skill]:
    """Load all discovered SKILL.md files."""
    found: list[Skill] = []
    seen_names: set[str] = set()

    for root in _discover_skill_dirs():
        for skill_md in sorted(root.glob("*/SKILL.md")):
            try:
                content = skill_md.read_text(encoding="utf-8")
            except Exception:
                continue

            if not content.strip():
                continue

            name = _extract_frontmatter_field(content, "name") or skill_md.parent.name
            description = _extract_frontmatter_field(content, "description")
            if name in seen_names:
                continue
#如果存在同名技能，优先加载第一个发现的技能，后续同名技能将被忽略，没有就进行加入里面
            seen_names.add(name)
            found.append(
                Skill(
                    name=name,
                    description=description,
                    content=content.strip(),
                    path=str(skill_md),
                )
            )

    return found

#将加载的技能列表格式化成适合嵌入系统提示的文本块
def skills_prompt_block(skills: list[Skill]) -> str:
    """Build a compact prompt block with loaded skills."""
    if not skills:
        return ""

    blocks = []
    for s in skills:
        blocks.append(
            f"## Skill: {s.name}\n"
            f"Source: {s.path}\n"
            f"{s.content}"
        )
    return "\n\n".join(blocks)
