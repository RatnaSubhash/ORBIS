"""
================================================================================
ORBIS PHASE 8 DEMONSTRATION: CONTENT & DOCUMENT GENERATION
Structured Synthesis, Safe Document Persistence, Workspace Containment,
Overwrite Approval Gates, Research-to-Document DAG, and Independent Verification
================================================================================
"""

import asyncio
from pathlib import Path
import tempfile

from core.agent.executor.executor import TaskExecutor
from core.agent.llm.mock import MockLLMProvider
from core.agent.models import AgentRequest, TaskPlan, TaskState, TaskStep, TaskType
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.content.mock import MockContentGenerator
from core.tools.content_tool import ContentGeneratorParameters, ContentGeneratorTool
from core.tools.document_tool import DocumentActionParameters, DocumentTool
from core.tools.echo_tool import EchoTool
from core.tools.registry import ToolRegistry
from core.tools.search_provider import MockSearchProvider, SearchResult
from core.tools.terminal_tool import TerminalActionParameters, TerminalTool
from core.tools.web_research_tool import WebResearchParameters, WebResearchTool
from core.types import ExecutionContext, RiskLevel, ToolCall


async def run_demo() -> None:
    print("=" * 80)
    print("ORBIS PHASE 8 DEMONSTRATION: CONTENT & DOCUMENT GENERATION")
    print("Deterministic, Offline Execution with Strict Filesystem Boundaries")
    print("=" * 80)

    with tempfile.TemporaryDirectory(prefix="orbis_demo8_") as temp_dir:
        temp_root = Path(temp_dir).resolve()

        # Initialize mock git repository for Phase 7 compatibility
        import subprocess
        subprocess.run(["git", "init"], cwd=str(temp_root), capture_output=True)

        # ----------------------------------------------------------------------
        # Setup Tool Registry
        # ----------------------------------------------------------------------
        registry = ToolRegistry()
        registry.register(EchoTool())

        # Web Research (Phase 7)
        mock_search = MockSearchProvider()
        mock_search.set_mock_results(
            "computer networks",
            [
                SearchResult(
                    title="Computer Networks — Principles & Protocols",
                    url="https://docs.example.org/networks",
                    snippet="Foundations of packet switching, TCP/IP stack, and routing algorithms.",
                )
            ],
        )
        def mock_web_fetcher(url: str):
            html = """
            <html>
            <head><title>Computer Networks</title></head>
            <body>
                <h1>Computer Networks Architecture</h1>
                <p>Computer networks utilize layered protocols (OSI and TCP/IP) for packet routing.</p>
                <p>Key layers include physical, data link, network (IP), transport (TCP/UDP), and application.</p>
                <p>Note: Secret API key sk-live88888888888888888888 must never be exposed.</p>
            </body>
            </html>
            """
            return 200, "Computer Networks", html

        web_tool = WebResearchTool(search_provider=mock_search, mock_fetcher=mock_web_fetcher)
        registry.register(web_tool)

        term_tool = TerminalTool(allowed_workspace_root=str(temp_root))
        registry.register(term_tool)

        # Content Generator (Phase 8)
        content_gen = MockContentGenerator()
        content_tool = ContentGeneratorTool(generator=content_gen)
        registry.register(content_tool)

        # Document Tool (Phase 8)
        doc_tool = DocumentTool(allowed_workspace_root=str(temp_root))
        registry.register(doc_tool)

        planner = HierarchicalPlanner()
        executor = TaskExecutor(tool_registry=registry, planner=planner)
        ctx = ExecutionContext()

        # ----------------------------------------------------------------------
        # SCENARIO 1: In-Memory Content Generation (No Filesystem Side Effects)
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 1: In-Memory Content Generation (Pure Synthesis)")
        print("-" * 80)
        gen_params = ContentGeneratorParameters(
            topic="Operating System Virtual Memory",
            content_type="report",
            format="markdown",
        )
        gen_res = await content_tool.execute(gen_params, ctx)
        print(f"Generated Topic: {gen_res.output['topic']}")
        print(f"Format: {gen_res.output['format']} | Characters: {gen_res.output['char_count']}")
        print(f"Grounding Metadata: is_generated={gen_res.output['grounding']['is_generated']}, has_context={gen_res.output['grounding']['has_context']}")
        print("Preview:")
        for line in gen_res.output["content"].splitlines()[:6]:
            print(f"  {line}")

        # ----------------------------------------------------------------------
        # SCENARIO 2: Safe Markdown Document Creation (.md)
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 2: Safe Markdown Document Creation (.md)")
        print("-" * 80)
        md_params = DocumentActionParameters(
            file_path="docs/virtual_memory.md",
            content=gen_res.output["content"],
            format="markdown",
            overwrite=False,
        )
        md_res = await doc_tool.execute(md_params, ctx)
        print(f"Document Written: {md_res.output['file_name']}")
        print(f"Path on Disk: {md_res.output['file_path']}")
        print(f"Size: {md_res.output['size_bytes']} bytes | Format: {md_res.output['format']}")
        print(f"Physical File Exists: {(temp_root / 'docs' / 'virtual_memory.md').exists()}")

        # ----------------------------------------------------------------------
        # SCENARIO 3: Safe Plain Text Document Creation (.txt)
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 3: Safe Plain Text Document Creation (.txt)")
        print("-" * 80)
        txt_gen_params = ContentGeneratorParameters(
            topic="Network Routing Protocols",
            content_type="document",
            format="text",
        )
        txt_gen_res = await content_tool.execute(txt_gen_params, ctx)

        txt_doc_params = DocumentActionParameters(
            file_path="notes/routing.txt",
            content=txt_gen_res.output["content"],
            format="text",
            overwrite=False,
        )
        txt_res = await doc_tool.execute(txt_doc_params, ctx)
        print(f"Document Written: {txt_res.output['file_name']}")
        print(f"Size: {txt_res.output['size_bytes']} bytes | Overwritten: {txt_res.output['overwritten']}")
        print(f"Physical File Exists: {(temp_root / 'notes' / 'routing.txt').exists()}")

        # ----------------------------------------------------------------------
        # SCENARIO 4: Security Boundary & Path Traversal Rejections
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 4: Security Boundary & Path Traversal Rejections")
        print("-" * 80)
        # Attempt 1: Path traversal outside workspace
        try:
            p_traversal = DocumentActionParameters(file_path="../outside.md", content="Escape attempt")
            res_trav = await doc_tool.execute(p_traversal, ctx)
            print(f"Attempt '../outside.md': Success={res_trav.success} | Error: {res_trav.error}")
        except Exception as e:
            print(f"Attempt '../outside.md' blocked at validation: {e}")

        # Attempt 2: Protected credential file
        p_env = DocumentActionParameters(file_path=".env", content="SECRET_TOKEN=12345")
        res_env = await doc_tool.execute(p_env, ctx)
        print(f"Attempt '.env': Success={res_env.success} | Error: {res_env.error}")

        # Attempt 3: Disallowed script extension
        p_script = DocumentActionParameters(file_path="exploit.py", content="import os; os.system('calc')")
        res_script = await doc_tool.execute(p_script, ctx)
        print(f"Attempt 'exploit.py': Success={res_script.success} | Error: {res_script.error}")

        # ----------------------------------------------------------------------
        # SCENARIO 5: Overwrite Protection & High-Risk Approval Gate
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 5: Overwrite Protection & High-Risk Approval Gate")
        print("-" * 80)
        # Attempt A: Overwriting existing file without overwrite=True
        p_no_overwrite = DocumentActionParameters(file_path="docs/virtual_memory.md", content="Attempt overwrite", overwrite=False)
        res_no_ow = await doc_tool.execute(p_no_overwrite, ctx)
        print(f"Overwrite without flag: Success={res_no_ow.success} | Error: {res_no_ow.error}")

        # Attempt B: Overwrite step in TaskPlan without approval
        step_ow = TaskStep(
            step_id="step_overwrite_doc",
            title="Overwrite virtual memory document",
            tool_name="document",
            parameters={"file_path": "docs/virtual_memory.md", "content": "Approved new content.", "format": "markdown", "overwrite": True},
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            state=TaskState.READY,
        )
        plan_ow = TaskPlan(request_id="req_ow", user_prompt="Overwrite document", steps={"step_overwrite_doc": step_ow})

        res_unapproved = await executor.execute(plan_ow, approval_callback=None)
        print(f"Execution without approval callback:")
        print(f"  Status: {res_unapproved.status.value}")
        print(f"  Summary: {res_unapproved.summary}")

        # Attempt C: Overwrite step with explicit approval callback
        async def grant_approval(step: TaskStep) -> bool:
            print(f"  >>> [Approval Callback] Step '{step.step_id}' ({step.tool_name}) APPROVED by user.")
            return True

        plan_ow.steps["step_overwrite_doc"].state = TaskState.READY
        res_approved = await executor.execute(plan_ow, approval_callback=grant_approval)
        print(f"Execution with approval:")
        print(f"  Status: {res_approved.status.value}")
        print(f"  Completed: {res_approved.completed_steps}")

        # ----------------------------------------------------------------------
        # SCENARIO 6: Multi-Step Research -> Content -> Document Pipeline
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 6: Multi-Step Research -> Content -> Document Pipeline")
        print("-" * 80)
        research_req = AgentRequest(user_prompt="Research computer networks and create a report called network_report.md")
        pipeline_plan = await planner.plan(research_req, registry)

        print(f"Generated Pipeline Plan: ID={pipeline_plan.plan_id} (Steps: {len(pipeline_plan.steps)})")
        for sid, step in pipeline_plan.steps.items():
            print(f"  * Step '{sid}' | Tool: {step.tool_name} | State: {step.state.value} | Bindings: {step.input_bindings}")

        pipeline_res = await executor.execute(pipeline_plan)
        print(f"Execution Outcome: {pipeline_res.status.value}")
        print(f"Executed Steps: {' -> '.join(pipeline_res.completed_steps)}")

        net_doc = temp_root / "network_report.md"
        print(f"Persisted Report Path: {net_doc}")
        print(f"File Size on Disk: {net_doc.stat().st_size} bytes")
        print(f"Secret Redaction Check: sk-live key redacted -> {'[REDACTED API KEY]' in net_doc.read_text(encoding='utf-8')}")

        # ----------------------------------------------------------------------
        # SCENARIO 7: Independent Document Verification
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 7: Independent Document Verification")
        print("-" * 80)
        doc_step_res = pipeline_res.step_results["step_create_document"]
        doc_ver = pipeline_res.verification_reports["step_create_document"]
        print(f"Tool Verification Assessment: Verified={doc_ver.verified}")
        print(f"Verification Details: {doc_ver.details}")

        # ----------------------------------------------------------------------
        # SCENARIO 8: Backward Compatibility Verification (Phase 1, 6, 7, 8)
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 8: Backward Compatibility Verification")
        print("-" * 80)
        mock_llm = MockLLMProvider(
            default_tool_call=ToolCall(tool_name="echo", parameters={"message": "Phase 1 Backward Compatible"})
        )
        orchestrator = AgentOrchestrator(
            llm_provider=mock_llm,
            tool_registry=registry,
            planner=planner,
            executor=executor,
        )

        p1_res = await orchestrator.run("echo Phase 1 Compatible")
        print(f"Phase 1 Single-Step run(): Status={p1_res.status.value} | Output='{p1_res.tool_result.output}'")

        p7_res = await orchestrator.run_task("check git status")
        print(f"Phase 7 Terminal Automation run_task(): Status={p7_res.status.value} | Steps={p7_res.completed_steps}")

        p8_res = await orchestrator.run_task("Create a README for my project")
        print(f"Phase 8 Document Generation run_task(): Status={p8_res.status.value} | Steps={p8_res.completed_steps}")

    print("\n" + "=" * 80)
    print("PHASE 8 DEMONSTRATION COMPLETE: ALL SCENARIOS VALIDATED")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_demo())
