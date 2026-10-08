"""
Unit tests for ORBIS Extended System Tools:
- AppLaunchTool (Allowlisted apps, safe PDF, YouTube, multi-app sequencing)
- NetworkControlTool (Wi-Fi, Bluetooth)
- MessagingTool (WhatsApp, Email, SMS with RiskLevel.HIGH)
- HardwareControlTool (RGB keyboard lighting, Game launcher)
- SystemControlTool extensions (Brightness, Battery Saver, Windows Settings)
- HierarchicalPlanner classification and routing for these capabilities
"""

import pytest
from pathlib import Path

from core.types import ExecutionContext, RiskLevel
from core.tools.app_launch_tool import AppLaunchTool, AppLaunchParameters, MockAppLauncher
from core.tools.network_control_tool import NetworkControlTool, NetworkControlParameters, MockNetworkBackend
from core.tools.messaging_tool import MessagingTool, MessagingParameters, MockMessageDispatcher
from core.tools.hardware_control_tool import HardwareControlTool, HardwareControlParameters, MockRGBController
from core.tools.system_control_tool import SystemControlTool, MockBrightnessController
from core.agent.models import AgentRequest
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.agent.planner.classifier import CapabilityClass, classify_capability
from core.tools.registry import create_default_tool_registry


@pytest.fixture
def mock_ctx():
    return ExecutionContext(request_id="test_req", step_id="test_step")


@pytest.mark.asyncio
async def test_app_launch_tool_allowlisted(mock_ctx, tmp_path):
    mock_launcher = MockAppLauncher()
    tool = AppLaunchTool(launcher=mock_launcher, allowed_workspace_root=str(tmp_path))
    params = AppLaunchParameters(action="launch_app", app_name="notepad")

    res = await tool.execute(params, mock_ctx)
    assert res.success is True
    assert res.output["app_name"] == "notepad"
    assert len(mock_launcher.launched_processes) == 1

    v_rep = await tool.verify(params, res, mock_ctx)
    assert v_rep.verified is True


@pytest.mark.asyncio
async def test_app_launch_tool_rejects_disallowed(mock_ctx, tmp_path):
    tool = AppLaunchTool(allowed_workspace_root=str(tmp_path))
    params = AppLaunchParameters(action="launch_app", app_name="malware.exe")
    res = await tool.execute(params, mock_ctx)
    assert res.success is False
    assert "not in the authorized allowlist" in res.error


@pytest.mark.asyncio
async def test_app_launch_tool_pdf(mock_ctx, tmp_path):
    test_pdf = tmp_path / "report.pdf"
    test_pdf.write_bytes(b"%PDF-1.4 test")

    mock_launcher = MockAppLauncher()
    tool = AppLaunchTool(
        launcher=mock_launcher,
        allowed_workspace_root=str(tmp_path),
    )

    params = AppLaunchParameters(action="open_pdf", pdf_path=str(test_pdf))
    res = await tool.execute(params, mock_ctx)
    assert res.success is True
    assert len(mock_launcher.launched_processes) == 1


@pytest.mark.asyncio
async def test_network_control_tool(mock_ctx):
    mock_backend = MockNetworkBackend()
    tool = NetworkControlTool(backend=mock_backend)

    # 1. WiFi Status
    p1 = NetworkControlParameters(action="get_wifi_status")
    r1 = await tool.execute(p1, mock_ctx)
    assert r1.success is True
    assert r1.output["connected"] is True
    v1 = await tool.verify(p1, r1, mock_ctx)
    assert v1.verified is True

    # 2. Scan WiFi
    p2 = NetworkControlParameters(action="scan_wifi")
    r2 = await tool.execute(p2, mock_ctx)
    assert r2.success is True
    assert len(r2.output["networks"]) > 0

    # 3. Bluetooth Status
    p3 = NetworkControlParameters(action="get_bluetooth_status")
    r3 = await tool.execute(p3, mock_ctx)
    assert r3.success is True
    assert r3.output["enabled"] is True


@pytest.mark.asyncio
async def test_messaging_tool_risk_and_execution(mock_ctx):
    mock_dispatcher = MockMessageDispatcher()
    tool = MessagingTool(dispatcher=mock_dispatcher)

    # Risk level must strictly be HIGH
    assert tool.risk_level == RiskLevel.HIGH
    params = MessagingParameters(
        channel="whatsapp",
        recipient="Alice",
        message="Hello from ORBIS automated test",
    )

    res = await tool.execute(params, mock_ctx)
    assert res.success is True
    assert res.output["recipient"] == "Alice"
    assert len(mock_dispatcher.sent_messages) == 1

    v_rep = await tool.verify(params, res, mock_ctx)
    assert v_rep.verified is True


@pytest.mark.asyncio
async def test_hardware_control_tool(mock_ctx):
    mock_rgb = MockRGBController()
    tool = HardwareControlTool(rgb_controller=mock_rgb)

    # 1. RGB
    p1 = HardwareControlParameters(
        action="set_rgb_color",
        color="cyan",
        mode="breathing",
        brightness=80,
    )
    r1 = await tool.execute(p1, mock_ctx)
    assert r1.success is True
    assert mock_rgb.color == "cyan"
    v1 = await tool.verify(p1, r1, mock_ctx)
    assert v1.verified is True

    # 2. Game
    p2 = HardwareControlParameters(action="launch_game", game_name="Cyberpunk")
    r2 = await tool.execute(p2, mock_ctx)
    assert r2.success is True


@pytest.mark.asyncio
async def test_system_control_brightness_and_settings(mock_ctx):
    mock_bright = MockBrightnessController(initial_level=50)
    tool = SystemControlTool(brightness_controller=mock_bright)

    # Get brightness
    from core.tools.system_control_tool import SystemControlParameters
    p1 = SystemControlParameters(action="get_brightness")
    r1 = await tool.execute(p1, mock_ctx)
    assert r1.success is True
    assert r1.output["level"] == 50

    # Set brightness
    p2 = SystemControlParameters(action="set_brightness", level=85)
    r2 = await tool.execute(p2, mock_ctx)
    assert r2.success is True
    assert mock_bright.get_brightness() == 85
    v2 = await tool.verify(p2, r2, mock_ctx)
    assert v2.verified is True

    # Power status
    p3 = SystemControlParameters(action="get_power_status")
    r3 = await tool.execute(p3, mock_ctx)
    assert r3.success is True
    assert "battery_percent" in r3.output

    # Windows settings
    p4 = SystemControlParameters(action="get_windows_settings")
    r4 = await tool.execute(p4, mock_ctx)
    assert r4.success is True
    assert "os" in r4.output


@pytest.mark.asyncio
async def test_planner_routing_for_extended_capabilities():
    registry = create_default_tool_registry()
    planner = HierarchicalPlanner()

    # 1. App Launching
    req1 = AgentRequest(user_prompt="open notepad")
    assert classify_capability("open notepad") == CapabilityClass.APP_LAUNCHING
    plan1 = await planner.plan(req1, registry)
    assert len(plan1.steps) == 1
    step1 = list(plan1.steps.values())[0]
    assert step1.tool_name == "app_launch"
    assert step1.parameters["app_name"] == "notepad"

    # 2. Network Control
    req2 = AgentRequest(user_prompt="check wifi status")
    assert classify_capability("check wifi status") == CapabilityClass.NETWORK_CONTROL
    plan2 = await planner.plan(req2, registry)
    step2 = list(plan2.steps.values())[0]
    assert step2.tool_name == "network_control"
    assert step2.parameters["action"] == "get_wifi_status"

    # 3. Hardware Control
    req3 = AgentRequest(user_prompt="set keyboard rgb to cyan")
    assert classify_capability("set keyboard rgb to cyan") == CapabilityClass.HARDWARE_CONTROL
    plan3 = await planner.plan(req3, registry)
    step3 = list(plan3.steps.values())[0]
    assert step3.tool_name == "hardware_control"
    assert step3.parameters["color"] == "cyan"

    # 4. Messaging (Strictly HIGH Risk)
    req4 = AgentRequest(user_prompt="send whatsapp message to Alice saying Hello from test")
    assert classify_capability("send whatsapp message to Alice saying Hello from test") == CapabilityClass.MESSAGING
    plan4 = await planner.plan(req4, registry)
    step4 = list(plan4.steps.values())[0]
    assert step4.tool_name == "messaging"
    assert step4.risk_level == RiskLevel.HIGH
    assert step4.requires_approval is True

    # 5. Brightness in System Control
    req5 = AgentRequest(user_prompt="set brightness to 75%")
    assert classify_capability("set brightness to 75%") == CapabilityClass.SYSTEM_CONTROL
    plan5 = await planner.plan(req5, registry)
    step5 = list(plan5.steps.values())[0]
    assert step5.tool_name == "system_control"
    assert step5.parameters["action"] == "set_brightness"
    assert step5.parameters["level"] == 75
