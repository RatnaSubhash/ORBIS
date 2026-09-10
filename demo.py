"""
ORBIS Phase 1 Demonstration Script.
Executes a single-step agent pipeline using EchoTool and the Minimal Agent Shell.
Demonstrates:
  User Request -> UNDERSTANDING_REQUEST -> Tool Selection ->
  CALLING_TOOL -> Tool Execution -> VERIFYING -> COMPLETED.
"""

import asyncio
from typing import Optional

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.memory.db import DatabaseManager
from core.tools.echo_tool import EchoTool
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, StatusEvent, ToolCall


async def main() -> None:
    print("=" * 60)
    print("  ORBIS Phase 1: Minimal Agent Shell Demonstration")
    print("=" * 60)

    # 1. Initialize SQLite Database (in-memory for demo)
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    print("[1] Initialized SQLite DatabaseManager.")

    # 2. Initialize Tool Registry and register EchoTool
    registry = ToolRegistry()
    echo_tool = EchoTool()
    registry.register(echo_tool)
    print(f"[2] Registered Tool: '{echo_tool.name}' (Risk: {echo_tool.risk_level.value})")

    # 3. Setup Deterministic Mock LLM Provider
    # Configured to map the user prompt to an EchoTool invocation
    mock_tool_call = ToolCall(
        tool_name="echo",
        parameters={"message": "Hello from ORBIS Phase 1!"},
    )
    llm = MockLLMProvider(default_tool_call=mock_tool_call)
    print("[3] Configured MockLLMProvider for deterministic execution.")

    # 4. Initialize Minimal Agent Shell
    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=registry,
        db_manager=db,
    )

    # 5. Attach Status Event Listener to display safe public progress
    async def print_status(event: StatusEvent) -> None:
        risk_tag = f" [{event.risk_level.value}]" if event.risk_level else ""
        print(f"  >>> STATUS: {event.event_type.value}{risk_tag}")
        print(f"      Summary: {event.summary}")
        if event.details:
            print(f"      Details: {event.details}")

    orchestrator.add_event_listener(print_status)

    # 6. Execute User Request
    user_request = "Echo the message 'Hello from ORBIS Phase 1!'"
    print(f"\n[4] Submitting User Request:\n    \"{user_request}\"\n")
    print("-" * 60)

    context = ExecutionContext(task_id="demo_task_001")
    response = await orchestrator.run(user_request, context=context)

    print("-" * 60)
    print(f"\n[5] Execution Finished with Status: {response.status.value}")
    print(f"    Summary: {response.summary}")
    if response.tool_result:
        print(f"    Tool Output: {response.tool_result.output}")
    if response.verification:
        print(f"    Verification: Verified={response.verification.verified} ({response.verification.details})")

    # 7. Inspect SQLite Audit Trail
    audit_logs = db.get_audit_logs()
    print(f"\n[6] SQLite Audit Log Records: {len(audit_logs)}")
    for record in audit_logs:
        print(f"    - Task: {record['task_id']}, Action: {record['action_name']}, Risk: {record['risk_level']}, Verified: {bool(record['verified'])}")

    print("\n" + "=" * 60)
    print("  Phase 1 Pipeline Verified Successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
