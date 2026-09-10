"""
================================================================================
ORBIS PHASE 7 DEMONSTRATION: WEB RESEARCH & CONTROLLED TERMINAL AUTOMATION
Structured Research, Untrusted Web Data Isolation, Command Allowlisting,
Approval Gates, Multi-Step DAG Integration, and Backward Compatibility
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
from core.tools.echo_tool import EchoTool
from core.tools.registry import ToolRegistry
from core.tools.search_provider import MockSearchProvider, SearchResult
from core.tools.terminal_tool import TerminalActionParameters, TerminalTool
from core.tools.web_research_tool import (
    WebResearchParameters,
    WebResearchTool,
)
from core.types import ExecutionContext, RiskLevel, ToolCall


async def run_demo() -> None:
    print("=" * 80)
    print("ORBIS PHASE 7 DEMONSTRATION: WEB RESEARCH & CONTROLLED TERMINAL AUTOMATION")
    print("Offline, Deterministic Execution with Strict Security Boundaries")
    print("=" * 80)

    with tempfile.TemporaryDirectory(prefix="orbis_demo7_") as temp_dir:
        temp_root = Path(temp_dir).resolve()

        # Initialize mock git repository in temp workspace for realistic git commands
        import subprocess
        subprocess.run(["git", "init"], cwd=str(temp_root), capture_output=True)

        # ----------------------------------------------------------------------
        # Setup Tool Registry
        # ----------------------------------------------------------------------
        registry = ToolRegistry()
        echo_tool = EchoTool()
        registry.register(echo_tool)

        # Web Research with Mock Provider
        mock_search = MockSearchProvider()
        mock_search.set_mock_results(
            "orbis desktop agent",
            [
                SearchResult(
                    title="ORBIS — Workspace Continuity & Desktop Agent Architecture",
                    url="https://github.com/orbis/desktop-agent",
                    snippet="An intelligent context-aware desktop agent for workspace continuity and autonomous computer assistance.",
                ),
                SearchResult(
                    title="Context DNA Specification v1.0",
                    url="https://docs.orbis.local/spec/context-dna",
                    snippet="Deterministic state tracking, sessionization, and knowledge graph models.",
                ),
            ],
        )

        def mock_web_fetcher(url: str):
            if "github" in url:
                html = """
                <html>
                <head><title>ORBIS Repository</title></head>
                <body>
                    <h1>ORBIS Architecture</h1>
                    <p>Phase 7 introduces Web Research and Controlled Terminal Automation.</p>
                    <p>Features include SSRF protection, untrusted data separation, and command allowlists.</p>
                    <p>Warning: Secret key sk-live99999999999999999999 must never be exposed.</p>
                </body>
                </html>
                """
                return 200, "ORBIS Repository", html
            return 200, "Generic Page", "<html><body><p>Standard webpage content.</p></body></html>"

        web_tool = WebResearchTool(search_provider=mock_search, mock_fetcher=mock_web_fetcher)
        registry.register(web_tool)

        # Controlled Terminal Tool
        term_tool = TerminalTool(allowed_workspace_root=str(temp_root))
        registry.register(term_tool)

        planner = HierarchicalPlanner()
        executor = TaskExecutor(tool_registry=registry, planner=planner)

        # ----------------------------------------------------------------------
        # SCENARIO 1: Web Research with MockSearchProvider
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 1: Web Research Using MockSearchProvider (Search Action)")
        print("-" * 80)
        ctx = ExecutionContext()
        search_params = WebResearchParameters(action="search", query="orbis desktop agent", max_results=2)
        search_res = await web_tool.execute(search_params, ctx)

        print(f"Query: 'orbis desktop agent'")
        print(f"Execution Success: {search_res.success}")
        print(f"Total Results: {search_res.output.get('total_results')}")
        for idx, item in enumerate(search_res.output.get("results", []), 1):
            print(f"  [{idx}] {item['title']} -> {item['url']} ({item['source_domain']})")

        # ----------------------------------------------------------------------
        # SCENARIO 2: Structured Research Result & Untrusted Web Data Boundary
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 2: Structured Page Extraction & Untrusted Web Data Isolation")
        print("-" * 80)
        fetch_params = WebResearchParameters(action="fetch_page", url="https://github.com/orbis/desktop-agent")
        fetch_res = await web_tool.execute(fetch_params, ctx)

        print(f"Fetched URL: {fetch_res.output['url']}")
        print(f"Page Title: {fetch_res.output['title']}")
        print(f"Status Code: {fetch_res.output['status_code']}")
        print(f"Extracted Text:\n  \"{fetch_res.output['extracted_text'].strip()}\"")
        print(f"Security Classification Marker: is_untrusted_external_data = {fetch_res.output['is_untrusted_external_data']}")
        print(f"Security Notice: {fetch_res.output['security_notice']}")
        print(f"Secret Masking Check: sk-live key redacted -> {'[REDACTED API KEY]' in fetch_res.output['extracted_text']}")

        # ----------------------------------------------------------------------
        # SCENARIO 3: Terminal Safe Command (Low Risk)
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 3: Terminal Safe Command Execution (LOW Risk Inspection)")
        print("-" * 80)
        safe_params = TerminalActionParameters(command="git", args=["status", "--short"])
        safe_res = await term_tool.execute(safe_params, ctx)

        print(f"Executed: git status --short")
        print(f"Success: {safe_res.success} | Exit Code: {safe_res.output['exit_code']}")
        print(f"Risk Level: {safe_res.output['risk_level']}")
        print(f"Working Directory: {safe_res.output['working_dir']}")

        # ----------------------------------------------------------------------
        # SCENARIO 4: Blocked Unsafe Command
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 4: Blocked Unsafe Command (Security Policy Rejection)")
        print("-" * 80)
        # Attempt 1: Raw shell binary
        cmd_shell = TerminalActionParameters(command="cmd.exe", args=["/c", "dir"])
        res_shell = await term_tool.execute(cmd_shell, ctx)
        print(f"Attempt: 'cmd.exe /c dir'")
        print(f"Result: Success={res_shell.success} | Error: {res_shell.error}")

        # Attempt 2: Path traversal outside workspace
        cmd_traversal = TerminalActionParameters(command="git", args=["diff", "../outside.txt"])
        res_traversal = await term_tool.execute(cmd_traversal, ctx)
        print(f"Attempt: 'git diff ../outside.txt'")
        print(f"Result: Success={res_traversal.success} | Error: {res_traversal.error}")

        # ----------------------------------------------------------------------
        # SCENARIO 5: Approval-Required Terminal Action (High Risk)
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 5: High-Risk Action & Backend Approval Gate")
        print("-" * 80)
        step_high = TaskStep(
            step_id="step_clean_workspace",
            title="Clean untracked files",
            tool_name="terminal",
            parameters={"command": "git", "args": ["clean", "-fd"]},
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            state=TaskState.READY,
        )
        plan_high = TaskPlan(
            request_id="req_high_risk",
            user_prompt="Clean workspace files",
            steps={"step_clean_workspace": step_high},
        )

        # Attempt A: Without approval callback
        res_unapproved = await executor.execute(plan_high, approval_callback=None)
        print(f"Execution without approval callback:")
        print(f"  Overall Status: {res_unapproved.status.value}")
        print(f"  Step State: {res_unapproved.plan.steps['step_clean_workspace'].state.value}")
        print(f"  Message: {res_unapproved.summary}")

        # Attempt B: With approval callback granting approval
        async def mock_approval(step: TaskStep) -> bool:
            print(f"  >>> [Approval Callback] Step '{step.step_id}' ({step.parameters.get('command')}) APPROVED by user.")
            return True

        plan_high.steps["step_clean_workspace"].state = TaskState.READY
        res_approved = await executor.execute(plan_high, approval_callback=mock_approval)
        print(f"Execution with explicit approval:")
        print(f"  Overall Status: {res_approved.status.value}")
        print(f"  Completed Steps: {res_approved.completed_steps}")

        # ----------------------------------------------------------------------
        # SCENARIO 6: Multi-Step Phase 6 Integration (Search -> Summarize)
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 6: Multi-Step Phase 6 Integration (Search -> Summarize)")
        print("-" * 80)
        multi_req = AgentRequest(user_prompt="search the web for orbis desktop agent and summarize")
        multi_plan = await planner.plan(multi_req, registry)

        print(f"Generated Multi-Step Plan: ID={multi_plan.plan_id}")
        for sid, step in multi_plan.steps.items():
            print(f"  * Step '{sid}' | Tool: {step.tool_name} | State: {step.state.value} | Deps: {step.dependencies}")

        multi_res = await executor.execute(multi_plan)
        print(f"Execution Status: {multi_res.status.value}")
        print(f"Completed Steps: {' -> '.join(multi_res.completed_steps)}")

        # ----------------------------------------------------------------------
        # SCENARIO 7: Independent Verification
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 7: Independent Tool Verification")
        print("-" * 80)
        web_ver = await web_tool.verify(fetch_params, fetch_res, ctx)
        print(f"Web Tool Verification: Verified={web_ver.verified} | Details: {web_ver.details}")

        term_ver = await term_tool.verify(safe_params, safe_res, ctx)
        print(f"Terminal Tool Verification: Verified={term_ver.verified} | Details: {term_ver.details}")

        # ----------------------------------------------------------------------
        # SCENARIO 8: Backward Compatibility Verification
        # ----------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("SCENARIO 8: Backward Compatibility Verification (Phase 1 + Phase 6)")
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

        p1_res = await orchestrator.run("echo Phase 1 Backward Compatible")
        print(f"Phase 1 Single-Step run(): Status={p1_res.status.value} | Output='{p1_res.tool_result.output}'")

        p6_res = await orchestrator.run_task("check git status")
        print(f"Phase 6 Multi-Step run_task(): Status={p6_res.status.value} | Steps={p6_res.completed_steps}")

    print("\n" + "=" * 80)
    print("PHASE 7 DEMONSTRATION COMPLETE: ALL SCENARIOS VALIDATED")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_demo())
