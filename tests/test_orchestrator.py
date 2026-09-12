"""
Integration tests for Minimal Agent Shell / AgentOrchestrator.
Validates the complete execution pipeline, status event sequences, and failure recovery.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.memory.db import DatabaseManager
from core.tools.base import BaseTool
from core.tools.echo_tool import EchoParameters, EchoTool
from core.tools.registry import ToolRegistry
from core.types import (
    ExecutionContext,
    RiskLevel,
    StatusEvent,
    StatusEventType,
    ToolCall,
    ToolResult,
    VerificationReport,
)


@pytest.fixture
def test_setup():
    """Sets up an in-memory DB, registry with EchoTool, and event collector."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    registry = ToolRegistry()
    registry.register(EchoTool())

    events: List[StatusEvent] = []

    async def listener(event: StatusEvent) -> None:
        events.append(event)

    return db, registry, events, listener


@pytest.mark.asyncio
async def test_orchestrator_happy_path(test_setup) -> None:
    """Verifies complete end-to-end pipeline: Request -> Execute -> Verify -> Completed."""
    db, registry, events, listener = test_setup

    mock_call = ToolCall(tool_name="echo", parameters={"message": "hello agent"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    context = ExecutionContext(task_id="task_happy")
    response = await orchestrator.run("Echo message 'hello agent'", context=context)

    # 1. Assert final response
    assert response.status == StatusEventType.COMPLETED
    assert response.tool_result is not None
    assert response.tool_result.output == "hello agent"
    assert response.verification is not None
    assert response.verification.verified is True

    # 2. Assert status event sequence
    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.CALLING_TOOL,
        StatusEventType.VERIFYING,
        StatusEventType.COMPLETED,
    ]

    # 3. Assert database audit log
    logs = db.get_audit_logs(task_id="task_happy")
    assert len(logs) == 1
    assert logs[0]["action_name"] == "echo"
    assert logs[0]["verified"] == 1


@pytest.mark.asyncio
async def test_orchestrator_no_tool_required(test_setup) -> None:
    """Verifies that requests not requiring a tool complete gracefully."""
    db, registry, events, listener = test_setup

    llm = MockLLMProvider(default_tool_call=None)
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("What is 2 + 2?")
    assert response.status == StatusEventType.COMPLETED
    assert "no tool required" in response.summary

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_orchestrator_unregistered_tool(test_setup) -> None:
    """Verifies handling when the LLM selects a non-existent tool."""
    db, registry, events, listener = test_setup

    mock_call = ToolCall(tool_name="missing_tool", parameters={})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Do something with missing_tool")
    assert response.status == StatusEventType.FAILED
    assert "not registered" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.FAILED,
    ]


@pytest.mark.asyncio
async def test_orchestrator_parameter_validation_failure(test_setup) -> None:
    """Verifies handling when the tool parameters fail Pydantic validation."""
    db, registry, events, listener = test_setup

    # EchoTool requires 'message' string, passing empty dict
    mock_call = ToolCall(tool_name="echo", parameters={})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Echo nothing")
    assert response.status == StatusEventType.FAILED
    assert "Parameter validation failed" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.FAILED,
    ]


class FailingExecutionTool(BaseTool):
    """Test tool that fails during execute()."""
    name = "failing_exec"
    description = "A tool that always fails execution."
    risk_level = RiskLevel.LOW
    parameters_schema = EchoParameters

    async def execute(self, params, context):
        return ToolResult(success=False, error="Simulated execution crash")

    async def verify(self, params, result, context):
        return VerificationReport(verified=False, details="Should not reach here")


@pytest.mark.asyncio
async def test_orchestrator_execution_failure(test_setup) -> None:
    """Verifies handling when tool execution returns success=False."""
    db, registry, events, listener = test_setup
    registry.register(FailingExecutionTool())

    mock_call = ToolCall(tool_name="failing_exec", parameters={"message": "fail"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Run failing tool")
    assert response.status == StatusEventType.FAILED
    assert "Simulated execution crash" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.CALLING_TOOL,
        StatusEventType.FAILED,
    ]


class FailingVerificationTool(BaseTool):
    """Test tool that executes successfully but fails verification."""
    name = "failing_verify"
    description = "A tool that fails verification."
    risk_level = RiskLevel.LOW
    parameters_schema = EchoParameters

    async def execute(self, params, context):
        return ToolResult(success=True, output="fake output")

    async def verify(self, params, result, context):
        return VerificationReport(
            verified=False,
            details="Discrepancy detected in test verification.",
            discrepancies=["State mismatch detected"],
        )


@pytest.mark.asyncio
async def test_orchestrator_verification_failure(test_setup) -> None:
    """Verifies handling when tool verification fails."""
    db, registry, events, listener = test_setup
    registry.register(FailingVerificationTool())

    mock_call = ToolCall(tool_name="failing_verify", parameters={"message": "verify_fail"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Run unverified tool")
    assert response.status == StatusEventType.FAILED
    assert "Verification failed" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.CALLING_TOOL,
        StatusEventType.VERIFYING,
        StatusEventType.FAILED,
    ]


@pytest.mark.asyncio
async def test_orchestrator_llm_exception_handling(test_setup) -> None:
    """Verifies handling when LLM provider raises an unexpected exception."""
    db, registry, events, listener = test_setup

    llm = MockLLMProvider(should_fail=True, failure_error_message="LLM unreachable")
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Trigger LLM error")
    assert response.status == StatusEventType.FAILED
    assert "LLM unreachable" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.FAILED,
    ]


# ==============================================================================
# Deterministic Regression Tests for Answer Mode & Response Quality
# ==============================================================================

def test_classify_answer_mode_comparison() -> None:
    """Verifies that comparison queries are classified as EXPLANATION_COMPARISON."""
    from core.agent.orchestrator import AnswerMode, classify_answer_mode

    assert classify_answer_mode("What is the difference between TCP and UDP?") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("Compare TCP and UDP") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("TCP vs UDP") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("How does TCP differ from UDP?") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("What are the differences between REST and GraphQL?") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("Pros and cons of microservices") == AnswerMode.EXPLANATION_COMPARISON


def test_classify_answer_mode_conceptual_explanation() -> None:
    """Verifies that conceptual explanations are classified as EXPLANATION_COMPARISON."""
    from core.agent.orchestrator import AnswerMode, classify_answer_mode

    assert classify_answer_mode("What is Python?") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("Explain how DNS works") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("Why is HTTPS more secure than HTTP?") == AnswerMode.EXPLANATION_COMPARISON
    assert classify_answer_mode("How do neural networks work?") == AnswerMode.EXPLANATION_COMPARISON


def test_classify_answer_mode_factual() -> None:
    """Verifies that direct factual queries are classified as FACTUAL (concise 1-2 sentences)."""
    from core.agent.orchestrator import AnswerMode, classify_answer_mode

    assert classify_answer_mode("What is the latest Python version?") == AnswerMode.FACTUAL
    assert classify_answer_mode("What is the latest version of Node.js?") == AnswerMode.FACTUAL
    assert classify_answer_mode("Who is the current Chief Minister of Tamil Nadu?") == AnswerMode.FACTUAL
    assert classify_answer_mode("Who is the Prime Minister of India?") == AnswerMode.FACTUAL
    assert classify_answer_mode("What is the price of Apple's 2TB iCloud storage plan?") == AnswerMode.FACTUAL
    assert classify_answer_mode("When was Python 3.12 released?") == AnswerMode.FACTUAL


def test_classify_answer_mode_research_summary() -> None:
    """Verifies that research summary queries are classified as RESEARCH_SUMMARY."""
    from core.agent.orchestrator import AnswerMode, classify_answer_mode

    assert classify_answer_mode("Research computer networks and create a short summary.") == AnswerMode.RESEARCH_SUMMARY
    assert classify_answer_mode("Summarize the history of the internet") == AnswerMode.RESEARCH_SUMMARY
    assert classify_answer_mode("Give me a brief overview of quantum computing") == AnswerMode.RESEARCH_SUMMARY
    assert classify_answer_mode("Research findings roundup on climate change") == AnswerMode.RESEARCH_SUMMARY


@pytest.mark.asyncio
async def test_tcp_vs_udp_comparison_synthesis_is_complete() -> None:
    """
    Regression Test:
    Verifies that 'What is the difference between TCP and UDP?' uses AnswerMode.EXPLANATION_COMPARISON,
    instructs the LLM not to stop after TCP, and produces a complete comparison covering connection model,
    reliability, ordering, speed/overhead, and use cases.
    """
    from core.agent.orchestrator import AnswerMode, AgentOrchestrator, classify_answer_mode, get_synthesis_system_prompt

    user_query = "What is the difference between TCP and UDP?"
    mode = classify_answer_mode(user_query)
    assert mode == AnswerMode.EXPLANATION_COMPARISON

    sys_prompt = get_synthesis_system_prompt(mode)
    assert "ANSWER MODE: EXPLANATION / COMPARISON (COMPLETE YET CONCISE)" in sys_prompt
    assert "CRITICAL: Do NOT stop after explaining only one concept or side." in sys_prompt
    assert "Connection model" in sys_prompt
    assert "Reliability & packet ordering" in sys_prompt
    assert "Speed & overhead" in sys_prompt
    assert "Typical use cases" in sys_prompt

    complete_comparison = (
        "TCP (Transmission Control Protocol) is a connection-oriented protocol that guarantees reliable, "
        "ordered delivery of packets using handshakes and retransmissions, making it ideal for web browsing and file transfers. "
        "In contrast, UDP (User Datagram Protocol) is a connectionless, lightweight protocol that provides best-effort "
        "delivery without sequencing guarantees, offering lower latency and overhead for real-time applications like streaming, gaming, and VoIP."
    )

    mock_llm = MockLLMProvider(default_text_response=complete_comparison)
    orchestrator = AgentOrchestrator(llm_provider=mock_llm, tool_registry=ToolRegistry())

    research_output = {
        "action": "research",
        "query": "difference between TCP and UDP",
        "evidence": "TCP is connection-oriented and reliable. UDP is connectionless and fast.",
        "search_results": [
            {
                "title": "TCP vs UDP",
                "source_domain": "cloudflare.com",
                "url": "https://cloudflare.com/tcp-vs-udp",
                "snippet": "TCP provides reliable, ordered data stream. UDP prioritizes low latency over reliability.",
            }
        ],
    }

    answer = await orchestrator.synthesize_research_answer(user_query, research_output)

    # Verify call history received the EXPLANATION_COMPARISON prompt
    assert len(mock_llm.call_history) == 1
    call = mock_llm.call_history[0]
    assert "CRITICAL: Do NOT stop after explaining only one concept or side." in call["system_prompt"]
    assert "User Question: What is the difference between TCP and UDP?" in call["prompt"]

    # Verify that the complete comparison covers both protocols
    assert "TCP" in answer
    assert "UDP" in answer
    assert "connection-oriented" in answer
    assert "connectionless" in answer
    assert "reliable" in answer
    assert "streaming" in answer or "latency" in answer


@pytest.mark.asyncio
async def test_conceptual_explanation_remains_complete() -> None:
    """
    Regression Test:
    Verifies that 'What is Python?' is classified as EXPLANATION_COMPARISON and receives instructions
    to provide a complete conceptual overview without cutting off prematurely.
    """
    from core.agent.orchestrator import AnswerMode, AgentOrchestrator, classify_answer_mode, get_synthesis_system_prompt

    query = "What is Python?"
    mode = classify_answer_mode(query)
    assert mode == AnswerMode.EXPLANATION_COMPARISON

    sys_prompt = get_synthesis_system_prompt(mode)
    assert "Clearly describe what the concept is, how it functions, and its core purpose without cutting off prematurely." in sys_prompt

    python_explanation = (
        "Python is a high-level, interpreted, general-purpose programming language known for its clear, "
        "readable syntax and dynamic typing. It emphasizes code readability and supports multiple programming "
        "paradigms, including procedural, object-oriented, and functional styles. Python is widely utilized in "
        "web development, data analysis, artificial intelligence, scientific computing, and task automation."
    )
    mock_llm = MockLLMProvider(default_text_response=python_explanation)
    orchestrator = AgentOrchestrator(llm_provider=mock_llm, tool_registry=ToolRegistry())

    research_output = {
        "action": "research",
        "query": "Python programming language",
        "evidence": "Python is a high-level general-purpose programming language.",
        "search_results": [],
    }

    answer = await orchestrator.synthesize_research_answer(query, research_output)
    assert "high-level, interpreted" in answer
    assert "artificial intelligence" in answer


@pytest.mark.asyncio
async def test_factual_synthesis_remains_concise() -> None:
    """
    Regression Test:
    Verifies that direct factual queries use AnswerMode.FACTUAL and enforce a 1-2 sentence constraint.
    """
    from core.agent.orchestrator import AnswerMode, AgentOrchestrator, classify_answer_mode, get_synthesis_system_prompt

    query = "What is the price of Apple's 2TB iCloud plus storage plan?"
    mode = classify_answer_mode(query)
    assert mode == AnswerMode.FACTUAL

    sys_prompt = get_synthesis_system_prompt(mode)
    assert "ANSWER MODE: FACTUAL (CONCISE 1-2 SENTENCES)" in sys_prompt
    assert "Keep simple factual answers concise (1 to 2 clear sentences)" in sys_prompt
    assert "For prices: state the exact plan/tier requested. Do NOT substitute generic or baseline free tiers" in sys_prompt

    mock_llm = MockLLMProvider(default_text_response="According to apple.com, the 2TB iCloud+ storage plan costs $9.99 per month in the United States.")
    orchestrator = AgentOrchestrator(llm_provider=mock_llm, tool_registry=ToolRegistry())

    research_output = {
        "action": "research",
        "query": "Apple 2TB iCloud plus price",
        "evidence": "2TB iCloud+ plan is $9.99 per month.",
        "search_results": [],
    }

    answer = await orchestrator.synthesize_research_answer(query, research_output)
    assert "$9.99 per month" in answer


@pytest.mark.asyncio
async def test_current_office_holder_remains_concise() -> None:
    """
    Regression Test:
    Verifies that office holder queries use AnswerMode.FACTUAL and forbid biographical or predecessor filler.
    """
    from core.agent.orchestrator import AnswerMode, AgentOrchestrator, classify_answer_mode, get_synthesis_system_prompt

    query = "Who is the current Chief Minister of Tamil Nadu?"
    mode = classify_answer_mode(query)
    assert mode == AnswerMode.FACTUAL

    sys_prompt = get_synthesis_system_prompt(mode)
    assert "For office holders: name the current active incumbent. Do NOT include biographical etymology or predecessor history." in sys_prompt

    mock_llm = MockLLMProvider(default_text_response="According to tn.gov.in, the current Chief Minister of Tamil Nadu is M. K. Stalin.")
    orchestrator = AgentOrchestrator(llm_provider=mock_llm, tool_registry=ToolRegistry())

    research_output = {
        "action": "research",
        "query": "current Chief Minister of Tamil Nadu",
        "evidence": "M. K. Stalin is the current Chief Minister of Tamil Nadu.",
        "search_results": [],
    }

    answer = await orchestrator.synthesize_research_answer(query, research_output)
    assert "M. K. Stalin" in answer


@pytest.mark.asyncio
async def test_research_summary_mode_remains_grounded() -> None:
    """
    Regression Test:
    Verifies that 'Research computer networks and create a short summary.' uses AnswerMode.RESEARCH_SUMMARY,
    instructs the LLM to synthesize search evidence into key findings with attribution, and produces a grounded summary.
    """
    from core.agent.orchestrator import AnswerMode, AgentOrchestrator, classify_answer_mode, get_synthesis_system_prompt

    query = "Research computer networks and create a short summary."
    mode = classify_answer_mode(query)
    assert mode == AnswerMode.RESEARCH_SUMMARY

    sys_prompt = get_synthesis_system_prompt(mode)
    assert "ANSWER MODE: RESEARCH SUMMARY (GROUNDED SYNTHESIS)" in sys_prompt
    assert "Synthesize the supplied search evidence into a coherent, informative summary of the requested topic." in sys_prompt
    assert "Directly reflect the key findings from the search evidence rather than generic boilerplate." in sys_prompt
    assert "Attribute key facts to their source domains." in sys_prompt

    grounded_summary = (
        "According to computer.org, a computer network is a set of interconnected computing devices that exchange data "
        "and share resources using established communication protocols. Modern computer networks encompass Local Area Networks "
        "(LANs) and Wide Area Networks (WANs) governed by the TCP/IP stack. Key infrastructure elements include routers, switches, "
        "and physical or wireless links enabling secure global data transmission."
    )
    mock_llm = MockLLMProvider(default_text_response=grounded_summary)
    orchestrator = AgentOrchestrator(llm_provider=mock_llm, tool_registry=ToolRegistry())

    research_output = {
        "action": "research",
        "query": "computer networks",
        "evidence": "Computer networks link computing devices to share resources and data via protocols like TCP/IP.",
        "search_results": [
            {
                "title": "Computer Networking Overview",
                "source_domain": "computer.org",
                "url": "https://computer.org/networking",
                "snippet": "Interconnected computing devices share resources via communication protocols.",
            }
        ],
    }

    answer = await orchestrator.synthesize_research_answer(query, research_output)
    assert "computer.org" in answer
    assert "interconnected computing devices" in answer


def test_ambiguity_handling_remains_intact() -> None:
    """
    Regression Test:
    Verifies that ambiguous or malformed queries continue to be caught by ambiguity detection
    and do not get treated as valid search or comparison queries.
    """
    from core.agent.planner.hierarchical import is_ambiguous_or_malformed_query

    # Malformed queries with musical adjectives or dangling phrases
    is_amb, msg = is_ambiguous_or_malformed_query("who is who is the current chief minister of the melodic")
    assert is_amb is True
    assert msg is not None
    assert "clarify" in msg.lower() or "identify" in msg.lower()

    is_amb, msg = is_ambiguous_or_malformed_query("who is who")
    assert is_amb is True
    assert msg is not None

    is_amb, msg = is_ambiguous_or_malformed_query("what is the difference between")
    assert is_amb is True
    assert msg is not None

    # Valid queries must NOT be flagged as ambiguous
    assert is_ambiguous_or_malformed_query("What is the difference between TCP and UDP?")[0] is False
    assert is_ambiguous_or_malformed_query("Who is the current Chief Minister of Tamil Nadu?")[0] is False
    assert is_ambiguous_or_malformed_query("What is the latest Python version?")[0] is False
    assert is_ambiguous_or_malformed_query("Research computer networks and create a short summary.")[0] is False


@pytest.mark.asyncio
async def test_end_to_end_research_and_summary_query_cleaning() -> None:
    """
    Regression Test:
    Verifies that 'Research computer networks and create a short summary.' produces a search step
    with the query cleaned to 'computer networks' (stripping 'and create a short summary'),
    and synthesizes a grounded research summary upon completion.
    """
    from core.agent.models import AgentRequest, TaskState
    from core.agent.planner.hierarchical import HierarchicalPlanner
    from core.tools.search_provider import MockSearchProvider
    from core.tools.web_research_tool import WebResearchTool, SearchResult

    mock_search = MockSearchProvider()
    mock_search.set_mock_results(
        "computer networks",
        [
            SearchResult(
                title="Computer Networking Basics",
                url="https://networking.example.org/basics",
                snippet="Computer networks connect autonomous computers to share data using standardized protocols.",
            )
        ],
    )

    def mock_fetcher(url: str):
        return 200, "Networking Basics", "<html><body><p>Computer networks connect autonomous computers to share data using standardized protocols.</p></body></html>"

    web_tool = WebResearchTool(search_provider=mock_search, mock_fetcher=mock_fetcher)
    echo_tool = EchoTool()

    registry = ToolRegistry()
    registry.register(web_tool)
    registry.register(echo_tool)

    planner = HierarchicalPlanner()
    request = AgentRequest(
        request_id="req_summary_test",
        user_prompt="Research computer networks and create a short summary.",
    )
    plan = await planner.plan(request, registry)

    # Verify query was cleaned of 'and create a short summary'
    search_step = plan.steps["step_web_research"]
    assert search_step.parameters["query"] == "computer networks"

    grounded_summary = "According to networking.example.org, computer networks connect computers to exchange data reliably."
    mock_llm = MockLLMProvider(default_text_response=grounded_summary)
    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=registry,
        planner=planner,
    )

    res = await orchestrator.run_task(
        user_request="Research computer networks and create a short summary.",
    )

    assert res.status == TaskState.COMPLETED
    assert res.summary == grounded_summary
    assert "networking.example.org" in res.summary


# ==============================================================================
# Deterministic Regression Tests for Multi-Turn Approval & Task Continuation
# ==============================================================================

@pytest.fixture
def doc_orchestrator_fixture(tmp_path: Path):
    """Sets up an AgentOrchestrator equipped with DocumentTool rooted in tmp_path."""
    from core.tools.document_tool import DocumentTool
    from core.content.base import BaseContentGenerator, GeneratedContent
    from core.agent.planner.hierarchical import HierarchicalPlanner

    # Minimal mock content generator tool
    class MockContentTool(BaseTool):
        name: str = "content_generator"
        description: str = "Mock content generator"
        risk_level: RiskLevel = RiskLevel.LOW
        call_count: int = 0

        def validate_parameters(self, params):
            return params

        async def execute(self, params, context):
            self.call_count += 1
            return ToolResult(success=True, output={"content": "# Mock Content for " + params.get("topic", "Topic")})

        async def verify(self, params, result, context):
            return VerificationReport(verified=True, details="Content generated.")

    doc_tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    content_tool = MockContentTool()
    echo_tool = EchoTool()

    reg = ToolRegistry()
    reg.register(doc_tool)
    reg.register(content_tool)
    reg.register(echo_tool)

    planner = HierarchicalPlanner()
    orchestrator = AgentOrchestrator(
        llm_provider=MockLLMProvider(),
        tool_registry=reg,
        planner=planner,
    )

    return orchestrator, doc_tool, content_tool, tmp_path


@pytest.mark.asyncio
async def test_existing_file_enters_blocked_pending_approval_before_execution(doc_orchestrator_fixture) -> None:
    """
    Test A & B:
    Verifies that requesting document creation for an already-existing file:
    - Proactively enters TaskState.BLOCKED_PENDING_APPROVAL before execution.
    - Generates a specific approval prompt naming the file.
    - Does NOT modify the file on disk while waiting for approval.
    """
    from core.agent.models import TaskState

    orchestrator, doc_tool, content_tool, tmp_path = doc_orchestrator_fixture

    target_file = tmp_path / "README.md"
    original_text = "# Original README"
    target_file.write_text(original_text, encoding="utf-8")

    res = await orchestrator.run_task("Create a README for my project.")

    # 1. Must halt at BLOCKED_PENDING_APPROVAL
    assert res.status == TaskState.BLOCKED_PENDING_APPROVAL

    # 2. Specific approval prompt naming the target file
    assert "README.md already exists in your workspace. Do you want me to overwrite it?" in res.summary

    # 3. No modification occurred on disk while waiting for approval
    assert target_file.read_text(encoding="utf-8") == original_text

    # 4. Orchestrator holds active PendingApproval record
    assert orchestrator._pending_approval is not None
    assert orchestrator._pending_approval.blocked_step_id == "step_create_document"
    assert orchestrator._pending_approval.tool_name == "document"


@pytest.mark.asyncio
async def test_affirmative_approval_resumes_same_task(doc_orchestrator_fixture) -> None:
    """
    Test C & E:
    Verifies that speaking/entering 'Yes, overwrite it':
    - Resumes the exact pending task.
    - Modifies parameters to overwrite=True.
    - Successfully overwrites the file and returns COMPLETED.
    - Clears the pending approval record.
    """
    from core.agent.models import TaskState

    orchestrator, doc_tool, content_tool, tmp_path = doc_orchestrator_fixture

    target_file = tmp_path / "README.md"
    target_file.write_text("# Old Content", encoding="utf-8")

    # Turn 1: Blocked
    res1 = await orchestrator.run_task("Create a README for my project.")
    assert res1.status == TaskState.BLOCKED_PENDING_APPROVAL
    assert target_file.read_text(encoding="utf-8") == "# Old Content"

    # Turn 2: Affirmative approval
    res2 = await orchestrator.run_task("Yes, overwrite it.")
    assert res2.status == TaskState.COMPLETED
    assert "successfully" in res2.summary.lower()

    # Verify file was now overwritten
    new_content = target_file.read_text(encoding="utf-8")
    assert new_content != "# Old Content"
    assert "Mock Content" in new_content

    # Pending approval cleared
    assert orchestrator._pending_approval is None


@pytest.mark.asyncio
async def test_completed_steps_not_repeated_on_resume(doc_orchestrator_fixture) -> None:
    """
    Test D:
    Verifies that steps already completed before the approval block (e.g. content_generator)
    are NOT re-executed when resuming.
    """
    from core.agent.models import TaskState

    orchestrator, doc_tool, content_tool, tmp_path = doc_orchestrator_fixture

    target_file = tmp_path / "README.md"
    target_file.write_text("# Initial", encoding="utf-8")

    # Turn 1: Step 1 (content_generator) runs once, Step 2 (document) blocks
    res1 = await orchestrator.run_task("Create a README for my project.")
    assert res1.status == TaskState.BLOCKED_PENDING_APPROVAL
    assert content_tool.call_count == 1

    # Turn 2: Affirmative approval
    res2 = await orchestrator.run_task("Yes, overwrite it.")
    assert res2.status == TaskState.COMPLETED

    # Step 1 must NOT have run a second time
    assert content_tool.call_count == 1


@pytest.mark.asyncio
async def test_negative_approval_cancels_without_modification(doc_orchestrator_fixture) -> None:
    """
    Test F:
    Verifies that speaking/entering 'No, don't overwrite it':
    - Clears the pending approval.
    - Does NOT modify the target file.
    - Returns a clean cancellation result.
    """
    from core.agent.models import TaskState

    orchestrator, doc_tool, content_tool, tmp_path = doc_orchestrator_fixture

    target_file = tmp_path / "README.md"
    target_file.write_text("# Untouched Original", encoding="utf-8")

    # Turn 1: Blocked
    res1 = await orchestrator.run_task("Create a README for my project.")
    assert res1.status == TaskState.BLOCKED_PENDING_APPROVAL

    # Turn 2: Negative cancellation
    res2 = await orchestrator.run_task("No, don't overwrite it.")
    assert res2.status == TaskState.CANCELLED
    assert "Action cancelled" in res2.summary
    assert "README.md" in res2.summary

    # File remains completely untouched
    assert target_file.read_text(encoding="utf-8") == "# Untouched Original"
    assert orchestrator._pending_approval is None


@pytest.mark.asyncio
async def test_expired_approval_cannot_be_resumed(doc_orchestrator_fixture) -> None:
    """
    Test G:
    Verifies that when the approval TTL expires, saying 'Yes, overwrite it'
    does NOT resume the old task or modify the file.
    """
    from core.agent.models import TaskState

    orchestrator, doc_tool, content_tool, tmp_path = doc_orchestrator_fixture

    target_file = tmp_path / "README.md"
    target_file.write_text("# Stale Original", encoding="utf-8")

    # Turn 1: Blocked
    res1 = await orchestrator.run_task("Create a README for my project.")
    assert res1.status == TaskState.BLOCKED_PENDING_APPROVAL

    # Manually expire the TTL
    orchestrator._pending_approval.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    # Turn 2: Attempt approval after expiration
    res2 = await orchestrator.run_task("Yes, overwrite it.")

    # Must NOT have resumed the task or modified the file
    assert target_file.read_text(encoding="utf-8") == "# Stale Original"
    assert orchestrator._pending_approval is None


@pytest.mark.asyncio
async def test_unrelated_query_invalidates_pending_approval(doc_orchestrator_fixture) -> None:
    """
    Test H:
    Verifies that asking an unrelated query (e.g. 'Echo Hello') invalidates the pending approval
    and processes the new query normally without modifying the file.
    """
    from core.agent.models import TaskState

    orchestrator, doc_tool, content_tool, tmp_path = doc_orchestrator_fixture

    target_file = tmp_path / "README.md"
    target_file.write_text("# Safe Original", encoding="utf-8")

    # Turn 1: Blocked on README.md
    res1 = await orchestrator.run_task("Create a README for my project.")
    assert res1.status == TaskState.BLOCKED_PENDING_APPROVAL
    assert orchestrator._pending_approval is not None

    # Turn 2: Unrelated command
    res2 = await orchestrator.run_task("Echo Hello World")

    # Echo query should complete normally
    assert res2.status == TaskState.COMPLETED
    assert "Hello World" in str(res2.final_output)

    # Pending approval must be invalidated
    assert orchestrator._pending_approval is None
    assert target_file.read_text(encoding="utf-8") == "# Safe Original"


@pytest.mark.asyncio
async def test_voice_two_turn_approval_integration(doc_orchestrator_fixture) -> None:
    """
    Test K:
    Verifies complete end-to-end two-turn voice interaction:
    Turn 1: Audio -> STT -> Orchestrator -> BLOCKED_PENDING_APPROVAL -> TTS speaks approval question.
    Turn 2: Audio -> STT -> Orchestrator -> Resumes task -> File overwritten -> TTS speaks completion.
    """
    from core.voice.models import AudioBuffer
    from core.voice.service import VoiceService
    from core.voice.stt.mock_stt import MockSTTProvider
    from core.voice.tts.mock_tts import MockTTSProvider
    from core.voice.capture.mock_capture import MockAudioCapture

    orchestrator, doc_tool, content_tool, tmp_path = doc_orchestrator_fixture

    target_file = tmp_path / "README.md"
    target_file.write_text("# Old Voice Content", encoding="utf-8")

    stt = MockSTTProvider()
    tts = MockTTSProvider()
    capture = MockAudioCapture()

    voice_svc = VoiceService(
        orchestrator=orchestrator,
        capture=capture,
        stt_provider=stt,
        tts_provider=tts,
    )
    orchestrator.voice_service = voice_svc

    # --- Turn 1: "Create a README file for all this" ---
    stt.queue_transcript("Create a README file for all this")
    audio1 = AudioBuffer(data=b"\x01\x02" * 8000, sample_rate=16000)
    v_res1 = await voice_svc.process_spoken_request(audio1, speak_response=True)

    assert v_res1.requires_approval is True
    assert "README.md already exists in your workspace. Do you want me to overwrite it?" in v_res1.agent_response_summary
    assert target_file.read_text(encoding="utf-8") == "# Old Voice Content"

    # --- Turn 2: "Yes, overwrite it" ---
    stt.queue_transcript("Yes, overwrite it")
    audio2 = AudioBuffer(data=b"\x03\x04" * 8000, sample_rate=16000)
    v_res2 = await voice_svc.process_spoken_request(audio2, speak_response=True)

    assert v_res2.requires_approval is False
    assert v_res2.success is True
    assert "successfully" in v_res2.agent_response_summary.lower()

    # Verify file was overwritten
    assert target_file.read_text(encoding="utf-8") != "# Old Voice Content"
    assert "Mock Content" in target_file.read_text(encoding="utf-8")
