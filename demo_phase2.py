"""
ORBIS Phase 2 Demonstration Script.
Demonstrates:
  1. Starting the observation service.
  2. Detecting active application/window state.
  3. Producing structured workspace events.
  4. Storing events in SQLite database.
  5. Retrieving events from SQLite.
  6. Demonstrating duplicate event suppression.
  7. Demonstrating privacy exclusion (redaction/dropping).
  8. Demonstrating safe FileTool metadata inspection within allowed workspace root.
"""

import asyncio
from pathlib import Path

from core.config import Settings
from core.memory.db import DatabaseManager
from core.observation.mock import MockWorkspaceObserver
from core.observation.service import WorkspaceObservationService
from core.tools.desktop_tool import DesktopActionParameters, DesktopTool
from core.tools.file_tool import FileActionParameters, FileTool
from core.types import ExecutionContext, WorkspaceEvent


async def run_phase2_demo() -> None:
    print("=" * 68)
    print("  ORBIS Phase 2: Passive Workspace Observation & Desktop Tools Demo")
    print("=" * 68)

    # 1. Initialize SQLite Database
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    print("\n[1] Initialized SQLite DatabaseManager with Phase 2 migrations.")

    # 2. Configure Observer and Observation Service
    mock_observer = MockWorkspaceObserver()
    mock_observer.set_active_window(
        title="orchestrator.py - ORBIS - Visual Studio Code",
        process_name="Code.exe",
        process_id=4120,
        executable_path="C:\\Users\\User\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
    )

    observation_service = WorkspaceObservationService(
        observer=mock_observer,
        db_manager=db,
        polling_interval=1.0,
    )

    # Attach event listener
    observed_events = []

    async def on_event(event: WorkspaceEvent) -> None:
        observed_events.append(event)
        print(f"    >>> EVENT DETECTED: {event.event_type.value}")
        print(f"        Process: {event.process_name} (PID: {event.process_id})")
        print(f"        Title:   {event.window_title}")
        print(f"        Time:    {event.timestamp.isoformat()}")

    observation_service.add_event_listener(on_event)

    # 3. Detect Active Application and Window
    print("\n[2] Sampling active workspace state (VS Code)...")
    event1 = await observation_service.poll_step()
    assert event1 is not None

    # 4. Demonstrate Duplicate Event Suppression
    print("\n[3] Testing duplicate event suppression on identical state...")
    event2 = await observation_service.poll_step()
    if event2 is None:
        print("    --> Success: Duplicate sample detected and suppressed (no event emitted or recorded).")

    # 5. Transition to another application (Browser)
    print("\n[4] User switches to Chrome browser...")
    mock_observer.set_active_window(
        title="FastAPI Documentation - Google Chrome",
        process_name="chrome.exe",
        process_id=5812,
    )
    event3 = await observation_service.poll_step()
    assert event3 is not None

    # 6. Demonstrate Privacy Exclusion
    print("\n[5] User switches to a sensitive password manager (1Password)...")
    mock_observer.set_active_window(
        title="1Password - Personal Vault (Master Password Required)",
        process_name="1password.exe",
        process_id=9876,
        is_excluded=True,
    )
    event4 = await observation_service.poll_step()
    if event4 is None:
        print("    --> Success: Privacy exclusion enforced! Event dropped, no sensitive metadata recorded.")

    # 7. Retrieve Events from SQLite Database
    print("\n[6] Retrieving recorded workspace events from SQLite database...")
    db_events = db.get_workspace_events()
    print(f"    Total Events Stored: {len(db_events)}")
    for i, e in enumerate(db_events, 1):
        print(f"    {i}. [{e.event_type.value}] {e.process_name} - \"{e.window_title}\"")

    # 8. Demonstrate DesktopTool Read-Only Inspection
    print("\n[7] Demonstrating DesktopTool safe inspection...")
    desktop_tool = DesktopTool(observer=mock_observer)
    context = ExecutionContext(task_id="demo_dt")
    dt_result = await desktop_tool.execute(DesktopActionParameters(action="capture_snapshot"), context)
    print(f"    DesktopTool Output: {dt_result.output}")
    dt_verify = await desktop_tool.verify(DesktopActionParameters(action="capture_snapshot"), dt_result, context)
    print(f"    DesktopTool Verification: {dt_verify.verified} ({dt_verify.details})")

    # 9. Demonstrate Safe FileTool Metadata Inspection
    print("\n[8] Demonstrating FileTool safe metadata inspection within workspace root...")
    project_root = Path(__file__).parent.resolve()
    file_tool = FileTool(allowed_workspace_root=str(project_root))

    # Safe inspection of README.md
    ft_result = await file_tool.execute(
        FileActionParameters(action="inspect_path", path="README.md"),
        context,
    )
    print(f"    Inspected 'README.md':")
    print(f"      Size: {ft_result.output['size_bytes']} bytes")
    print(f"      Modified: {ft_result.output['modified_at']}")
    ft_verify = await file_tool.verify(
        FileActionParameters(action="inspect_path", path="README.md"),
        ft_result,
        context,
    )
    print(f"      Verification: {ft_verify.verified} ({ft_verify.details})")

    # Safe rejection of path traversal outside root
    print("\n    Testing path traversal rejection ('../outside.txt'):")
    traversal_result = await file_tool.execute(
        FileActionParameters(action="inspect_path", path="../outside.txt"),
        context,
    )
    print(f"      Success: {traversal_result.success}")
    print(f"      Error caught: {traversal_result.error}")

    print("\n" + "=" * 68)
    print("  Phase 2 Demonstration Completed Successfully!")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(run_phase2_demo())
