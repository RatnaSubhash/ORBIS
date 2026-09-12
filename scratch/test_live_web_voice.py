import asyncio
import sys
import os
sys.path.insert(0, os.path.abspath("."))

from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.memory.db import DatabaseManager
from core.tools.registry import create_default_tool_registry
from core.voice.service import extract_spoken_response

async def main():
    print("Testing live web research query routing and execution...")
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    
    tools = create_default_tool_registry(db=db)
    
    planner = HierarchicalPlanner()
    orchestrator = AgentOrchestrator(
        llm_provider=None,
        tool_registry=tools,
        planner=planner,
    )
    
    query = "What is the latest Python version?"
    print(f"\nUser query: '{query}'")
    
    res = await orchestrator.run_task(user_request=query)
    print(f"\nTask Status: {res.status}")
    print(f"Task ID: {res.task_id}")
    print(f"Completed steps: {res.completed_steps}")
    print(f"Final output keys: {list(res.final_output.keys()) if isinstance(res.final_output, dict) else type(res.final_output)}")
    
    spoken = extract_spoken_response(res)
    print(f"\nExtracted Spoken Response for VoiceService:\n'{spoken}'")
    
    assert res.status.name == "COMPLETED"
    assert "step_web_research" in res.completed_steps
    assert "python" in spoken.lower()
    print("\nSUCCESS: Non-destructive live integration test passed!")

if __name__ == "__main__":
    asyncio.run(main())
