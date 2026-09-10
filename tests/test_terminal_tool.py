"""
Unit and Security Tests for TerminalTool (ORBIS Phase 7).
Validates command allowlists, structured shell=False execution, path containment,
dynamic risk classification, secret masking, timeouts, and independent verification.
"""

from pathlib import Path
import pytest
from pydantic import ValidationError

from core.tools.terminal_tool import (
    CommandPolicy,
    TerminalActionParameters,
    TerminalTool,
)
from core.types import ExecutionContext, RiskLevel, ToolResult


# ------------------------------------------------------------------------------
# 1. Command Policy Allowlist & Rejection Tests
# ------------------------------------------------------------------------------

def test_command_policy_allowed_low_risk(tmp_path: Path) -> None:
    """Verifies that safe environment inspection commands evaluate to LOW risk without error."""
    # git --version / status
    risk, err = CommandPolicy.evaluate("git", ["--version"], tmp_path, tmp_path)
    assert risk == RiskLevel.LOW
    assert err is None

    risk, err = CommandPolicy.evaluate("git", ["status", "--short"], tmp_path, tmp_path)
    assert risk == RiskLevel.LOW
    assert err is None

    # python --version
    risk, err = CommandPolicy.evaluate("python", ["--version"], tmp_path, tmp_path)
    assert risk == RiskLevel.LOW
    assert err is None

    # pytest --version
    risk, err = CommandPolicy.evaluate("pytest", ["--version"], tmp_path, tmp_path)
    assert risk == RiskLevel.LOW
    assert err is None


def test_command_policy_medium_and_high_risk(tmp_path: Path) -> None:
    """Verifies that non-destructive runs evaluate to MEDIUM and mutating operations evaluate to HIGH."""
    # pytest test suite execution -> MEDIUM
    risk, err = CommandPolicy.evaluate("pytest", ["-v", "tests/test_tools.py"], tmp_path, tmp_path)
    assert risk == RiskLevel.MEDIUM
    assert err is None

    # git diff -> MEDIUM
    risk, err = CommandPolicy.evaluate("git", ["diff"], tmp_path, tmp_path)
    assert risk == RiskLevel.MEDIUM
    assert err is None

    # git clean / commit / reset -> HIGH
    for high_cmd in ("clean", "commit", "reset", "checkout", "switch"):
        risk, err = CommandPolicy.evaluate("git", [high_cmd], tmp_path, tmp_path)
        assert risk == RiskLevel.HIGH
        assert err is None

    # pip install -> HIGH
    risk, err = CommandPolicy.evaluate("python", ["-m", "pip", "install", "pkg"], tmp_path, tmp_path)
    assert risk == RiskLevel.HIGH
    assert err is None


def test_command_policy_blocked_shell_and_utilities(tmp_path: Path) -> None:
    """Verifies that raw shell binaries and administrative utilities are blocked."""
    for blocked_shell in ("cmd", "cmd.exe", "powershell", "powershell.exe", "bash", "sh"):
        risk, err = CommandPolicy.evaluate(blocked_shell, ["/c", "dir"], tmp_path, tmp_path)
        assert err is not None
        assert "strictly forbidden" in err

    for blocked_tool in ("curl", "wget", "del", "reg", "netsh", "format"):
        risk, err = CommandPolicy.evaluate(blocked_tool, ["arg"], tmp_path, tmp_path)
        assert err is not None
        assert "blocked by policy" in err or "not in the approved" in err


def test_command_policy_metacharacter_injection_rejection(tmp_path: Path) -> None:
    """Verifies that shell injection metacharacters are rejected in arguments."""
    injection_args = [
        ["status", ";", "del", "*"],
        ["status", "&&", "whoami"],
        ["status", "|", "grep"],
        ["status", "`id`"],
        ["status", "$()"],
        ["status", ">", "out.txt"],
    ]
    for args in injection_args:
        risk, err = CommandPolicy.evaluate("git", args, tmp_path, tmp_path)
        assert err is not None
        assert "metacharacters" in err


def test_command_policy_path_traversal_and_secrets(tmp_path: Path) -> None:
    """Verifies that path traversal ('..') and credential file arguments are blocked."""
    # Path traversal
    risk, err = CommandPolicy.evaluate("pytest", ["../outside_tests.py"], tmp_path, tmp_path)
    assert err is not None
    assert "Path traversal" in err or "outside workspace root" in err

    # Secret credential references
    risk, err = CommandPolicy.evaluate("git", ["diff", ".env"], tmp_path, tmp_path)
    assert err is not None
    assert "credential file blocked" in err


# ------------------------------------------------------------------------------
# 2. TerminalTool Execution Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_terminal_tool_allowed_low_risk_execution(tmp_path: Path) -> None:
    """Verifies executing an allowed safe command via live execution."""
    tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    params = TerminalActionParameters(command="python", args=["--version"])
    result = await tool.execute(params, ctx)

    assert result.success is True
    assert "Python" in result.output["stdout"]
    assert result.output["exit_code"] == 0
    assert result.output["risk_level"] == RiskLevel.LOW.value


@pytest.mark.asyncio
async def test_terminal_tool_blocked_command_rejection(tmp_path: Path) -> None:
    """Verifies that blocked or unknown commands return failure with an explicit policy reason."""
    tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    params = TerminalActionParameters(command="powershell", args=["Get-Process"])
    result = await tool.execute(params, ctx)

    assert result.success is False
    assert "strictly forbidden" in result.error

    params_unknown = TerminalActionParameters(command="unknown_hacker_tool", args=["--run"])
    result_unknown = await tool.execute(params_unknown, ctx)
    assert result_unknown.success is False
    assert "not in the approved" in result_unknown.error


@pytest.mark.asyncio
async def test_terminal_tool_workspace_boundary_enforcement(tmp_path: Path) -> None:
    """Verifies that attempts to specify a working directory outside the workspace root are rejected."""
    tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    outside_dir = tmp_path.parent / "completely_outside_dir"
    params = TerminalActionParameters(command="git", args=["status"], working_dir=str(outside_dir))
    result = await tool.execute(params, ctx)

    assert result.success is False
    assert "Working directory safety violation" in result.error


@pytest.mark.asyncio
async def test_terminal_tool_mock_runner_and_secret_redaction(tmp_path: Path) -> None:
    """Verifies output capture and secret redaction from command stdout and stderr."""
    def fake_runner(cmd: str, args: list, wdir: Path):
        return 0, "Connected with token: sk-live12345678901234567890\nDone.", ""

    tool = TerminalTool(allowed_workspace_root=str(tmp_path), mock_runner=fake_runner)
    ctx = ExecutionContext()

    params = TerminalActionParameters(command="git", args=["status"])
    result = await tool.execute(params, ctx)

    assert result.success is True
    stdout = result.output["stdout"]
    assert "sk-live12345678901234567890" not in stdout
    assert "[REDACTED API KEY]" in stdout


@pytest.mark.asyncio
async def test_terminal_tool_timeout_handling(tmp_path: Path) -> None:
    """Verifies that process timeout is caught cleanly and returns failure."""
    tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    # Sleep command via python script for 5 seconds with 1 second timeout
    sleep_script = tmp_path / "sleep.py"
    sleep_script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    params = TerminalActionParameters(
        command="python",
        args=["sleep.py"],
        timeout_seconds=1.0,
    )
    result = await tool.execute(params, ctx)

    assert result.success is False
    assert "timed out" in result.error.lower()
    assert result.output["timed_out"] is True


@pytest.mark.asyncio
async def test_terminal_tool_non_zero_exit_code_handling(tmp_path: Path) -> None:
    """Verifies that commands exiting with non-zero code return success=False and record exit code."""
    def failing_runner(cmd: str, args: list, wdir: Path):
        return 1, "", "Fatal: repository not found."

    tool = TerminalTool(allowed_workspace_root=str(tmp_path), mock_runner=failing_runner)
    ctx = ExecutionContext()

    params = TerminalActionParameters(command="git", args=["status"])
    result = await tool.execute(params, ctx)

    assert result.success is False
    assert result.output["exit_code"] == 1
    assert "Fatal: repository not found" in result.error


@pytest.mark.asyncio
async def test_terminal_tool_verification(tmp_path: Path) -> None:
    """Verifies that verify() confirms clean exit code 0 and catches failed exit codes."""
    tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()
    params = TerminalActionParameters(command="git", args=["--version"])

    # Clean exit code 0
    ok_result = ToolResult(success=True, output={"exit_code": 0, "timed_out": False, "stdout": "git version 2.40"})
    report_ok = await tool.verify(params, ok_result, ctx)
    assert report_ok.verified is True

    # Non-zero exit code
    fail_result = ToolResult(success=False, error="Exited with code 2", output={"exit_code": 2, "timed_out": False})
    report_fail = await tool.verify(params, fail_result, ctx)
    assert report_fail.verified is False


# ------------------------------------------------------------------------------
# 3. Security Regression Tests (SEC-02 and SEC-04)
# ------------------------------------------------------------------------------

def test_command_policy_rejects_path_qualified_executables(tmp_path: Path) -> None:
    """
    SEC-02 REGRESSION TEST:
    CommandPolicy must strictly reject path-qualified, directory-bearing, relative,
    or UNC command values. Only bare executable names from the allowlist are permitted.
    """
    forbidden_commands = [
        r"C:\evil\git.exe",
        "C:/evil/git.exe",
        r".\git.exe",
        "./git.exe",
        r"..\git.exe",
        "../git.exe",
        "/tmp/git",
        r"\\server\share\git.exe",
        "//server/share/git.exe",
        r"\git.exe",
        "subdir/git",
    ]

    for forbidden in forbidden_commands:
        risk, err = CommandPolicy.evaluate(forbidden, ["status"], tmp_path, tmp_path)
        assert err is not None, f"Command '{forbidden}' should have been rejected by policy"
        assert "strictly forbidden" in err or "Path-qualified" in err
        assert risk == RiskLevel.HIGH


@pytest.mark.asyncio
async def test_terminal_tool_rejects_path_qualified_command_execution(tmp_path: Path) -> None:
    """
    SEC-02 REGRESSION TEST:
    TerminalTool.execute must fail when invoked with any path-qualified executable.
    """
    tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    params = TerminalActionParameters(command=r"C:\evil\git.exe", args=["status"])
    result = await tool.execute(params, ctx)
    assert result.success is False
    assert "strictly forbidden" in result.error or "Path-qualified" in result.error


def test_command_policy_python_script_risk_is_high(tmp_path: Path) -> None:
    """
    SEC-04 REGRESSION TEST:
    Arbitrary Python script execution within the workspace must evaluate to RiskLevel.HIGH
    so that it is authoritatively gated behind the approval requirement.
    Safe inspection commands must remain RiskLevel.LOW.
    """
    # 1. Arbitrary script execution is HIGH risk
    risk, err = CommandPolicy.evaluate("python", ["run_task.py"], tmp_path, tmp_path)
    assert risk == RiskLevel.HIGH
    assert err is None

    risk, err = CommandPolicy.evaluate("python", ["scripts/worker.py", "--fast"], tmp_path, tmp_path)
    assert risk == RiskLevel.HIGH
    assert err is None

    # 2. Tool dynamic risk derivation confirms HIGH risk
    tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    assert tool.get_effective_risk({"command": "python", "args": ["script.py"]}) == RiskLevel.HIGH

    # 3. Legitimate bare commands inspection is LOW risk
    for bare_cmd in ("git", "python", "pytest", "node", "npm"):
        risk_ver, err_ver = CommandPolicy.evaluate(bare_cmd, ["--version"], tmp_path, tmp_path)
        assert risk_ver == RiskLevel.LOW
        assert err_ver is None

