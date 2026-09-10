"""
Abstract Base Tool Contract for ORBIS.
All tools implement this contract to ensure consistent execution,
validation, risk assessment, and independent verification.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Type

from pydantic import BaseModel, ValidationError

from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class BaseTool(ABC):
    """Abstract base class for all ORBIS tools."""

    name: str
    description: str
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel]

    def validate_parameters(self, raw_params: Dict[str, Any]) -> BaseModel:
        """
        Validates raw input dictionary against the tool's parameter schema.
        Raises ValidationError if validation fails.
        """
        return self.parameters_schema.model_validate(raw_params)

    def get_schema_json(self) -> Dict[str, Any]:
        """Returns JSON schema of the parameter model for prompting and discovery."""
        return self.parameters_schema.model_json_schema()

    @abstractmethod
    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """
        Executes the tool logic with typed parameters.
        Must return a ToolResult with success status and outputs or errors.
        """
        pass

    @abstractmethod
    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """
        Independently verifies that the real-world state matches expectations.
        Must return a VerificationReport.
        """
        pass
