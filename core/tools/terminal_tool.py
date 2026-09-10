"""
Controlled Terminal Automation Tool for ORBIS Phase 7.
Provides strictly bounded, structured process execution without shell pass-through.
Enforces a deterministic command policy, workspace path containment,
dynamic risk classification (LOW / MEDIUM / HIGH), secret redaction, and approval gates.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import shlex
from typing import Any, Callable, Dict, List, Optional, Tuple, Type
from pydantic import BaseModel, Field

from core.config import get_settings
from core.tools.base import BaseTool
from core.tools.secret_redaction import redact_secrets, sanitize_data_structure
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.terminal")

# Shell injection metacharacters strictly disallowed in any argument
DANGEROUS_SHELL_CHARS = re.compile(r"[;&|<>\$`\n\r]")

# Disallowed shell wrapper binaries (must not be invoked as general shells)
DISALLOWED_SHELL_BINARIES = {
    "cmd", "cmd.exe",
    "powershell", "powershell.exe",
    "pwsh", "pwsh.exe",
    "bash", "bash.exe",
    "sh", "zsh",
    "wscript", "wscript.exe",
    "cscript", "cscript.exe",
}

# Blocked high-danger system utilities
DISALLOWED_SYSTEM_BINARIES = {
    "curl", "curl.exe",
    "wget", "wget.exe",
    "format", "format.com",
    "del", "erase", "rmdir", "rd",
    "reg", "reg.exe",
    "net", "net.exe",
    "netsh", "netsh.exe",
    "sc", "sc.exe",
    "taskkill", "taskkill.exe",
    "shutdown", "shutdown.exe",
    "ssh", "ssh.exe",
    "sftp", "sftp.exe",
}


class CommandPolicy:
    """
    Deterministic command policy defining allowed executables, argument rules,
    and risk levels (LOW, MEDIUM, HIGH, or BLOCKED).
    """

    @classmethod
    def evaluate(
        cls, command: str, args: List[str], allowed_root: Path, working_dir: Path
    ) -> Tuple[RiskLevel, Optional[str]]:
        """
        Evaluates a candidate command against the security policy.
        Returns (RiskLevel, error_message).
        If error_message is not None, the command is BLOCKED.
        """
        cmd_raw = command.strip()

        # SEC-02: Reject path-qualified, directory-bearing, relative, or UNC command values.
        # TerminalTool must only accept a bare executable name from the explicit allowlist.
        if (
            "\\" in cmd_raw
            or "/" in cmd_raw
            or ":" in cmd_raw
            or ".." in cmd_raw
            or cmd_raw.startswith(".")
            or cmd_raw.startswith("//")
            or cmd_raw.startswith("\\\\")
            or Path(cmd_raw).name.lower() != cmd_raw.lower()
        ):
            return (
                RiskLevel.HIGH,
                f"Path-qualified command '{command}' is strictly forbidden. Bare executable name required.",
            )

        cmd_norm = cmd_raw.lower()
        if cmd_norm.endswith(".exe"):
            cmd_base = cmd_norm[:-4]
        else:
            cmd_base = cmd_norm

        # 1. Shell wrapper rejection
        if cmd_norm in DISALLOWED_SHELL_BINARIES or cmd_base in DISALLOWED_SHELL_BINARIES:
            return RiskLevel.HIGH, f"Invocation of general shell binary '{command}' is strictly forbidden."

        # 2. Dangerous system tool rejection
        if cmd_norm in DISALLOWED_SYSTEM_BINARIES or cmd_base in DISALLOWED_SYSTEM_BINARIES:
            return RiskLevel.HIGH, f"Invocation of system administrative tool '{command}' is blocked by policy."

        # 3. Metacharacter injection checks
        for arg in [command] + args:
            if DANGEROUS_SHELL_CHARS.search(arg):
                return RiskLevel.HIGH, f"Argument contains disallowed shell metacharacters: '{arg}'."

        # 4. Path traversal / workspace boundary checks on arguments
        for arg in args:
            # Check if arg looks like a file path
            if "/" in arg or "\\" in arg or arg.startswith("."):
                # Prohibit navigating above workspace
                if ".." in arg:
                    return RiskLevel.HIGH, f"Path traversal ('..') is prohibited in argument: '{arg}'."
                try:
                    candidate = (working_dir / arg).resolve()
                    if candidate != allowed_root and allowed_root not in candidate.parents:
                        return RiskLevel.HIGH, f"Argument references path outside workspace root: '{arg}'."
                except Exception as e:
                    return RiskLevel.HIGH, f"Path validation failed for '{arg}': {e}"

            # Check for credential access attempts
            arg_lower = arg.lower()
            if any(sens in arg_lower for sens in (".env", "id_rsa", "credentials", "id_ed25519", "sam")):
                return RiskLevel.HIGH, f"Access to sensitive credential file blocked in argument: '{arg}'."

        # 5. Executable Allowlist and Risk Classification
        if cmd_base == "git":
            return cls._evaluate_git(args)
        elif cmd_base == "python":
            return cls._evaluate_python(args)
        elif cmd_base == "pytest":
            return cls._evaluate_pytest(args)
        elif cmd_base in ("node", "npm"):
            return cls._evaluate_node_npm(cmd_base, args)

        # Unrecognized executable
        return RiskLevel.HIGH, f"Command '{command}' is not in the approved command allowlist."

    @classmethod
    def _evaluate_git(cls, args: List[str]) -> Tuple[RiskLevel, Optional[str]]:
        if not args:
            return RiskLevel.LOW, None

        subcmd = args[0].lower()

        # LOW: Safe read-only inspection
        if subcmd in ("version", "--version"):
            return RiskLevel.LOW, None
        if subcmd in ("status", "branch", "rev-parse"):
            return RiskLevel.LOW, None
        if subcmd == "log":
            # Must be bounded
            return RiskLevel.LOW, None
        if subcmd == "diff":
            if any(arg in ("--stat", "--name-only", "--name-status") for arg in args):
                return RiskLevel.LOW, None
            return RiskLevel.MEDIUM, None

        # HIGH: Commands that mutate workspace or repository state -> requires approval
        if subcmd in ("commit", "checkout", "switch", "clean", "reset", "merge", "rebase", "stash"):
            return RiskLevel.HIGH, None

        # BLOCKED: Commands that alter remotes or push
        if subcmd in ("push", "remote", "clone", "fetch", "pull"):
            return RiskLevel.HIGH, f"Git network/remote operation '{subcmd}' is blocked in Phase 7."

        return RiskLevel.HIGH, f"Git subcommand '{subcmd}' is not allowed."

    @classmethod
    def _evaluate_python(cls, args: List[str]) -> Tuple[RiskLevel, Optional[str]]:
        if not args:
            return RiskLevel.LOW, None

        # LOW: Version or help inspection
        if args[0] in ("--version", "-V", "-v", "--help", "-h"):
            return RiskLevel.LOW, None

        # MEDIUM: Module inspection / unittest
        if args[0] == "-m":
            if len(args) > 1 and args[1] in ("unittest", "pytest", "py_compile"):
                return RiskLevel.MEDIUM, None
            if len(args) > 1 and args[1] in ("pip", "ensurepip"):
                return RiskLevel.HIGH, None

        # HIGH: Arbitrary script execution within workspace (requires approval)
        if any(arg.endswith(".py") for arg in args):
            return RiskLevel.HIGH, None

        return RiskLevel.HIGH, f"Python arguments {args} are not permitted under current policy."

    @classmethod
    def _evaluate_pytest(cls, args: List[str]) -> Tuple[RiskLevel, Optional[str]]:
        # Inspection / test execution within workspace
        if any(a in ("--version", "--help", "--collect-only") for a in args):
            return RiskLevel.LOW, None

        # Medium: Running tests
        return RiskLevel.MEDIUM, None

    @classmethod
    def _evaluate_node_npm(cls, cmd: str, args: List[str]) -> Tuple[RiskLevel, Optional[str]]:
        if not args or args[0] in ("--version", "-v", "version"):
            return RiskLevel.LOW, None

        if cmd == "npm" and args[0] in ("list", "ls"):
            return RiskLevel.LOW, None

        if cmd == "npm" and args[0] in ("install", "i", "add", "uninstall", "update"):
            return RiskLevel.HIGH, None

        return RiskLevel.HIGH, f"{cmd} arguments {args} are not permitted under current policy."


class TerminalActionParameters(BaseModel):
    """Parameter schema for controlled terminal execution."""
    command: str = Field(
        ...,
        min_length=1,
        description="The approved executable command (e.g. 'git', 'python', 'pytest').",
    )
    args: List[str] = Field(
        default_factory=list,
        description="Validated list of arguments to pass directly to the executable.",
    )
    working_dir: Optional[str] = Field(
        default=None,
        description="Target directory for execution. Must be within allowed workspace root.",
    )
    timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        description="Execution timeout in seconds.",
    )


class TerminalTool(BaseTool):
    """
    Controlled Terminal Automation Tool for ORBIS Phase 7.
    Enforces process isolation without shell execution (shell=False),
    path containment, dynamic risk evaluation, and secret masking.
    """

    name: str = "terminal"
    description: str = (
        "Controlled command execution without shell pass-through. "
        "Enforces command allowlists, workspace containment, and secret redaction."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.MEDIUM
    parameters_schema: Type[BaseModel] = TerminalActionParameters

    def __init__(
        self,
        allowed_workspace_root: Optional[str] = None,
        mock_runner: Optional[Callable[[str, List[str], Path], Tuple[int, str, str]]] = None,
    ) -> None:
        settings = get_settings()
        root_str = allowed_workspace_root or settings.allowed_workspace_root
        self.allowed_root = Path(root_str).resolve()
        self.mock_runner = mock_runner
        self.default_timeout = settings.terminal_timeout_seconds
        self.max_output_bytes = settings.terminal_max_output_bytes

    def resolve_working_directory(self, requested_dir: Optional[str]) -> Path:
        """Resolves and validates working directory within allowed workspace boundary."""
        if not requested_dir:
            return self.allowed_root

        candidate = Path(requested_dir)
        if not candidate.is_absolute():
            resolved = (self.allowed_root / candidate).resolve()
        else:
            resolved = candidate.resolve()

        if resolved != self.allowed_root and self.allowed_root not in resolved.parents:
            raise ValueError(
                f"Working directory safety violation: '{requested_dir}' resolves outside workspace root '{self.allowed_root}'."
            )

        if not resolved.is_dir():
            raise ValueError(f"Working directory does not exist or is not a directory: '{resolved}'.")

        return resolved

    def get_effective_risk(self, params: Any) -> RiskLevel:
        """
        Dynamically derives the authoritative RiskLevel from command policy.
        Ensures HIGH-risk commands cannot be spoofed as LOW by untrusted plans.
        """
        if isinstance(params, dict):
            cmd = params.get("command", "")
            args = params.get("args", [])
            wdir_str = params.get("working_dir")
        elif isinstance(params, TerminalActionParameters):
            cmd = params.command
            args = params.args
            wdir_str = params.working_dir
        else:
            return RiskLevel.HIGH

        try:
            wdir = self.resolve_working_directory(wdir_str)
        except Exception:
            return RiskLevel.HIGH

        risk, _ = CommandPolicy.evaluate(cmd, args, self.allowed_root, wdir)
        return risk

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes the approved command without a shell wrapper."""
        if not isinstance(params, TerminalActionParameters):
            params = self.validate_parameters(params.model_dump())

        # 1. Resolve and validate working directory
        try:
            working_dir = self.resolve_working_directory(params.working_dir)
        except ValueError as val_err:
            return ToolResult(success=False, error=str(val_err))

        # 2. Evaluate Command Policy
        risk_level, block_reason = CommandPolicy.evaluate(
            params.command, params.args, self.allowed_root, working_dir
        )
        if block_reason is not None:
            return ToolResult(
                success=False,
                error=f"Command blocked by policy: {block_reason}",
                metadata={"command": params.command, "risk_level": risk_level.value},
            )

        # 3. Execute via mock runner (if provided for isolated testing)
        if self.mock_runner:
            try:
                ret_code, stdout, stderr = self.mock_runner(params.command, params.args, working_dir)
                output = {
                    "command": params.command,
                    "args": params.args,
                    "working_dir": str(working_dir),
                    "exit_code": ret_code,
                    "stdout": redact_secrets(stdout[: self.max_output_bytes]),
                    "stderr": redact_secrets(stderr[: self.max_output_bytes]),
                    "timed_out": False,
                    "risk_level": risk_level.value,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }
                err_msg = None if ret_code == 0 else f"Process exited with non-zero code {ret_code}."
                if ret_code != 0 and stderr:
                    err_msg = f"{err_msg} Error: {stderr.strip()[:200]}"
                return ToolResult(
                    success=ret_code == 0,
                    output=sanitize_data_structure(output),
                    error=err_msg,
                    metadata={"exit_code": ret_code, "risk_level": risk_level.value},
                )
            except Exception as mock_err:
                return ToolResult(success=False, error=f"Mock execution failed: {mock_err}")

        # 4. Live execution via asyncio.create_subprocess_exec (strictly shell=False)
        try:
            # SEC-02: Ensure executable passed to create_subprocess_exec matches an explicitly approved bare identity
            cmd_raw = params.command.strip()
            cmd_lower = cmd_raw.lower()
            cmd_base = cmd_lower[:-4] if cmd_lower.endswith(".exe") else cmd_lower
            if cmd_base not in ("git", "python", "pytest", "node", "npm"):
                return ToolResult(
                    success=False,
                    error=f"Executable '{cmd_raw}' is not in the approved allowlist.",
                )
            executable = cmd_raw

            proc = await asyncio.create_subprocess_exec(
                executable,
                *params.args,
                cwd=str(working_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=params.timeout_seconds
                )
                timed_out = False
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                    await proc.wait()
                except Exception as kill_err:
                    logger.warning("Failed to kill timed out process: %s", kill_err)
                stdout_bytes, stderr_bytes = b"", b"Execution timed out."

            raw_stdout = stdout_bytes[: self.max_output_bytes].decode("utf-8", errors="replace")
            raw_stderr = stderr_bytes[: self.max_output_bytes].decode("utf-8", errors="replace")

            output = {
                "command": params.command,
                "args": params.args,
                "working_dir": str(working_dir),
                "exit_code": proc.returncode if not timed_out else -1,
                "stdout": redact_secrets(raw_stdout),
                "stderr": redact_secrets(raw_stderr),
                "timed_out": timed_out,
                "risk_level": risk_level.value,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }

            if timed_out:
                return ToolResult(
                    success=False,
                    output=sanitize_data_structure(output),
                    error=f"Command '{params.command}' timed out after {params.timeout_seconds} seconds.",
                    metadata={"timed_out": True},
                )

            success = proc.returncode == 0
            err_msg = None if success else f"Command exited with code {proc.returncode}."
            if not success and raw_stderr:
                err_msg = f"{err_msg} Error: {raw_stderr.strip()[:200]}"

            return ToolResult(
                success=success,
                output=sanitize_data_structure(output),
                error=err_msg,
                metadata={"exit_code": proc.returncode, "risk_level": risk_level.value},
            )

        except FileNotFoundError:
            return ToolResult(
                success=False,
                error=f"Executable '{params.command}' not found on system PATH.",
                metadata={"command": params.command},
            )
        except Exception as exec_err:
            logger.warning("Subprocess execution exception: %s", exec_err)
            return ToolResult(
                success=False,
                error=f"Subprocess execution error: {str(exec_err)}",
                metadata={"command": params.command},
            )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Independently verifies that the process completed cleanly."""
        if not isinstance(params, TerminalActionParameters):
            params = self.validate_parameters(params.model_dump())

        if not result.success:
            return VerificationReport(
                verified=False,
                details="Terminal command reported execution failure.",
                discrepancies=[result.error or "Process execution failed"],
            )

        if not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details="Invalid terminal output format.",
                discrepancies=["Output is not a dictionary"],
            )

        if result.output.get("timed_out"):
            return VerificationReport(
                verified=False,
                details="Process timed out during execution.",
                discrepancies=["timed_out is True"],
            )

        exit_code = result.output.get("exit_code")
        if exit_code != 0:
            return VerificationReport(
                verified=False,
                details=f"Process exited with non-zero code {exit_code}.",
                discrepancies=[f"Exit code is {exit_code}"],
            )

        return VerificationReport(
            verified=True,
            details="Process completed successfully with clean exit code 0.",
        )
