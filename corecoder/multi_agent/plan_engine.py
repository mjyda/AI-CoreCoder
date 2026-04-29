from __future__ import annotations

from .mods.plan_core import PlanCoreMixin
from .mods.plan_paths import PlanPathMixin
from .mods.plan_steps import PlanStepMixin


class ExecutionPlanMixin(PlanCoreMixin, PlanPathMixin, PlanStepMixin):
    pass
