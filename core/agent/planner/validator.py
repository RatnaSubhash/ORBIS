"""
Plan Validator for ORBIS Phase 6 Hierarchical Task Planner.
Verifies structural DAG integrity, dependency consistency, cycle absence,
step bounds, tool availability, and parameter binding references.
"""

from typing import Dict, List, Optional, Set

from core.agent.models import TaskPlan, TaskStep
from core.tools.registry import ToolRegistry
from core.types import RiskLevel


class PlanValidator:
    """
    Validates hierarchical task execution plans prior to execution.
    Fails safely with descriptive errors if the plan is malformed.
    """

    @classmethod
    def validate_plan(
        cls, plan: TaskPlan, tool_registry: Optional[ToolRegistry] = None
    ) -> List[str]:
        """
        Validates the structure, dependencies, limits, and tool bindings of a TaskPlan.
        Returns a list of error strings. An empty list signifies a valid plan.
        """
        errors: List[str] = []

        if not plan.steps:
            errors.append("Task plan contains no steps.")
            return errors

        if len(plan.steps) > plan.max_steps:
            errors.append(
                f"Task plan step count ({len(plan.steps)}) exceeds maximum allowed steps ({plan.max_steps})."
            )

        step_ids = list(plan.steps.keys())
        step_id_set = set(step_ids)

        # 1. Dependency Existence and Self-Dependency Checks
        for step_id, step in plan.steps.items():
            if step.step_id != step_id:
                errors.append(f"Step key '{step_id}' does not match step.step_id '{step.step_id}'.")

            for dep_id in step.dependencies:
                if dep_id == step_id:
                    errors.append(f"Step '{step_id}' cannot depend on itself.")
                elif dep_id not in step_id_set:
                    errors.append(
                        f"Step '{step_id}' depends on non-existent step '{dep_id}'."
                    )

            # Parent-child hierarchy check
            if step.parent_step_id is not None:
                if step.parent_step_id == step_id:
                    errors.append(f"Step '{step_id}' cannot be its own parent.")
                elif step.parent_step_id not in step_id_set:
                    errors.append(
                        f"Step '{step_id}' references non-existent parent step '{step.parent_step_id}'."
                    )

            # Tool existence and authoritative risk level check
            if tool_registry is not None:
                tool = tool_registry.get(step.tool_name)
                if tool is None:
                    errors.append(
                        f"Step '{step_id}' references unregistered tool '{step.tool_name}'."
                    )
                elif tool.risk_level == RiskLevel.HIGH and step.risk_level != RiskLevel.HIGH:
                    # Enforce that plan cannot downgrade authoritative tool risk level
                    step.risk_level = tool.risk_level

            # Input bindings validation
            for param_key, binding_ref in step.input_bindings.items():
                parts = binding_ref.split(".", 1)
                source_step_id = parts[0]
                if source_step_id not in step_id_set:
                    errors.append(
                        f"Step '{step_id}' input binding '{param_key}' references non-existent step '{source_step_id}'."
                    )
                elif source_step_id not in step.dependencies:
                    errors.append(
                        f"Step '{step_id}' consumes output from '{source_step_id}' without declaring it as a dependency."
                    )

        # 2. Circular Dependency Detection (DFS)
        visited: Dict[str, int] = {sid: 0 for sid in step_ids}  # 0=unvisited, 1=visiting, 2=visited

        def _dfs_cycle(u: str, path: List[str]) -> bool:
            visited[u] = 1
            path.append(u)
            step = plan.steps.get(u)
            if step:
                for v in step.dependencies:
                    if v not in step_ids:
                        continue
                    if visited[v] == 1:
                        cycle_path = " -> ".join(path[path.index(v):] + [v])
                        errors.append(f"Circular dependency detected: {cycle_path}")
                        return True
                    elif visited[v] == 0:
                        if _dfs_cycle(v, path):
                            return True
            path.pop()
            visited[u] = 2
            return False

        for sid in step_ids:
            if visited[sid] == 0:
                _dfs_cycle(sid, [])

        return errors

    @classmethod
    def validate_plan_or_raise(
        cls, plan: TaskPlan, tool_registry: Optional[ToolRegistry] = None
    ) -> None:
        """
        Validates the plan and raises ValueError if any validation rule is violated.
        """
        errors = cls.validate_plan(plan, tool_registry=tool_registry)
        if errors:
            raise ValueError(f"Plan validation failed with {len(errors)} error(s):\n- " + "\n- ".join(errors))
