"""
Execution Context for ORBIS Phase 6 Multi-Step Task Execution.
Manages step outputs, structured data passing, and dynamic parameter bindings.
"""

from typing import Any, Dict, Optional

from core.agent.models import TaskStep


class TaskExecutionContext:
    """
    Maintains intermediate step outputs and resolves parameter bindings
    between dependent steps without exposing arbitrary global state.
    """

    def __init__(self) -> None:
        self._outputs: Dict[str, Any] = {}
        self._named_aliases: Dict[str, Any] = {}

    def store_output(
        self, step_id: str, output: Any, output_key: Optional[str] = None
    ) -> None:
        """Records output of a successfully completed and verified step."""
        self._outputs[step_id] = output
        if output_key:
            self._named_aliases[output_key] = output

    def get_output(self, reference: str) -> Optional[Any]:
        """Retrieves raw output by step_id or output_key."""
        if reference in self._outputs:
            return self._outputs[reference]
        if reference in self._named_aliases:
            return self._named_aliases[reference]
        return None

    def resolve_bindings(self, step: TaskStep) -> Dict[str, Any]:
        """
        Constructs effective parameter dictionary by merging static parameters
        with dynamic bindings evaluated against previous step outputs.
        """
        params = dict(step.parameters)

        for param_key, binding_ref in step.input_bindings.items():
            if "." in binding_ref:
                source_ref, field_name = binding_ref.split(".", 1)
            else:
                source_ref, field_name = binding_ref, None

            source_output = self.get_output(source_ref)
            if source_output is None:
                continue

            if field_name is None:
                params[param_key] = source_output
            elif isinstance(source_output, dict):
                if field_name in source_output:
                    params[param_key] = source_output[field_name]
                elif "." in field_name:
                    curr = source_output
                    for part in field_name.split("."):
                        if isinstance(curr, dict):
                            curr = curr.get(part)
                        else:
                            curr = None
                            break
                    params[param_key] = curr
                else:
                    params[param_key] = source_output.get(field_name)
            elif hasattr(source_output, field_name):
                params[param_key] = getattr(source_output, field_name)
            else:
                # Fallback: if string output and requested field is content/message/path
                if isinstance(source_output, str) and field_name in ("content", "message", "path"):
                    params[param_key] = source_output

        return params
