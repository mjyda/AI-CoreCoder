"""Backward-compatible re-export for runtime skills."""

from .runtime.skills import Skill, load_skills, skills_prompt_block

__all__ = ["Skill", "load_skills", "skills_prompt_block"]
