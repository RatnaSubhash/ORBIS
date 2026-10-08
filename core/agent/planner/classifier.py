"""
Intent and Capability Classifier for ORBIS.
Classifies natural language requests into abstract request types / capability classes.
Does NOT use topic or entity whitelists.
Supports conversational wrappers, politeness markers, contractions, Indian-English phrasing,
and transcription variations.
"""

from enum import Enum
import re
from typing import Any, Dict, Optional, Tuple

from core.continuity.service import ContinuityService


class CapabilityClass(str, Enum):
    """Abstract capability classes representing distinct request types."""
    ABOUT_ORBIS_CAPABILITIES = "ABOUT_ORBIS_CAPABILITIES"
    REAL_TIME_UTILITY = "REAL_TIME_UTILITY"
    FRESH_INFORMATION = "FRESH_INFORMATION"
    GENERAL_INFORMATION = "GENERAL_INFORMATION"
    TECHNICAL_EXPLANATION = "TECHNICAL_EXPLANATION"
    WORKSPACE_INSPECTION = "WORKSPACE_INSPECTION"
    FILE_READING = "FILE_READING"
    FILE_CREATION = "FILE_CREATION"
    DOCUMENT_GENERATION = "DOCUMENT_GENERATION"
    TERMINAL_COMMANDS = "TERMINAL_COMMANDS"
    DESKTOP_INSPECTION = "DESKTOP_INSPECTION"
    SYSTEM_CONTROL = "SYSTEM_CONTROL"
    APP_LAUNCHING = "APP_LAUNCHING"
    NETWORK_CONTROL = "NETWORK_CONTROL"
    HARDWARE_CONTROL = "HARDWARE_CONTROL"
    MESSAGING = "MESSAGING"
    WORKSPACE_CONTINUITY = "WORKSPACE_CONTINUITY"
    INTENT_CONTINUITY = "INTENT_CONTINUITY"
    WORKSPACE_RESTORATION = "WORKSPACE_RESTORATION"
    PROJECT_ASSISTANCE = "PROJECT_ASSISTANCE"
    UNKNOWN_AMBIGUOUS = "UNKNOWN_AMBIGUOUS"


# Common conversational wrappers and politeness markers to strip for classification
LEADING_WRAPPERS = re.compile(
    r"^(?:please\s+|can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|would\s+you\s+(?:please\s+)?|"
    r"i\s+(?:want|would\s+like)\s+to\s+(?:know|understand)\s+(?:about\s+)?|"
    r"tell\s+me\s+|do\s+you\s+know\s+|help\s+me\s+(?:to\s+)?|i\s+need\s+to\s+|"
    r"kindly\s+|hey\s+orbis,?\s*|orbis,?\s*)+",
    re.IGNORECASE,
)

TRAILING_QUALIFIERS = re.compile(
    r"\s+\b(?:simply|in\s+simple\s+(?:words|terms)|in\s+plain\s+english|briefly|in\s+short|"
    r"for\s+me|please|thanks|thank\s+you)\b\s*$",
    re.IGNORECASE,
)


def normalize_prompt_for_classification(prompt: str) -> str:
    """Normalizes contractions and common speech variations."""
    if not prompt:
        return ""
    text = prompt.strip().lower()
    # Contractions
    text = re.sub(r"\bwhat's\b", "what is", text)
    text = re.sub(r"\bwho's\b", "who is", text)
    text = re.sub(r"\bwhere's\b", "where is", text)
    text = re.sub(r"\bhow's\b", "how is", text)
    text = re.sub(r"\bit's\b", "it is", text)
    text = re.sub(r"\bcan't\b", "cannot", text)
    text = re.sub(r"\bdon't\b", "do not", text)
    text = re.sub(r"\blet's\b", "let us", text)
    # Informal pronouns
    text = re.sub(r"\bu\b", "you", text)
    text = re.sub(r"\bur\b", "your", text)
    return text


def is_ambiguous_or_malformed(prompt: str) -> Tuple[bool, Optional[str]]:
    """Detects incomplete, stuttered, or malformed queries that require clarification."""
    if not prompt or not prompt.strip():
        return True, "I didn't hear a query. Could you please repeat your request?"

    cleaned = prompt.strip()
    lower = cleaned.lower()

    # Pure punctuation or no alphanumeric characters (e.g., "???", "...")
    if not re.search(r"[a-zA-Z0-9]", cleaned):
        return True, "I didn't hear a clear question. Could you please repeat your request?"

    # Repeated question prefixes / stuttering at query start
    repeated_prefix = re.search(
        r"^(who\s+is\s+who\s+is|what\s+is\s+what\s+is|where\s+is\s+where\s+is|"
        r"tell\s+me\s+tell\s+me|search\s+for\s+search\s+for|how\s+much\s+is\s+how\s+much\s+is)\b",
        lower,
    )
    if repeated_prefix:
        return True, "I heard a repeated phrase in your request. Could you please clarify what you would like to know?"

    # Incomplete queries ending abruptly with a trailing preposition or article
    stripped_punct = lower.rstrip(".?! \t\r\n")
    trailing_dangling = re.search(r"\b(?:of|for|about|in|at|to|by|from|between|with|the|a|an)\s*$", stripped_punct)
    has_question_starter = bool(
        re.search(r"^(who|what|where|when|which|how|tell\s+me|search|find|current|latest)\b", stripped_punct)
    )
    is_valid_inverted = bool(
        re.search(r"\b(?:was|were|am|is|are|did|have)\s+(?:i|you|we|he|she|they)\s+[a-zA-Z\s]+?\s+(?:with|for|on|in|at|from|about)\s*$", stripped_punct)
    )
    if has_question_starter and trailing_dangling and not is_valid_inverted:
        return True, "Your query appears incomplete. Could you please specify the full topic or question?"

    # 3. Malformed office targets with musical/adjective terms (e.g., "chief minister of the melodic")
    office_adjective_pattern = re.search(
        r"\b(?:chief\s+minister|prime\s+minister|president|governor|mayor)\s+of\s+(?:the\s+)?(melodic|rhythmic|harmonic|musical|lyrical|symphonic|loud|quiet|tasty|fragrant|blue|green|yellow)\b",
        lower,
    )
    if office_adjective_pattern:
        return True, "I could not identify the state, region, or organization in your question. Could you please clarify who you are asking about?"

    # 4. Bare tautological queries e.g. "who is who", "what is what"
    if re.search(r"^(?:who\s+is\s+who|what\s+is\s+what)\s*[.?!]?$", lower):
        return True, "Could you please specify the exact person or topic you would like to know about?"

    return False, None


def extract_project_assistance_metadata(prompt: str) -> Tuple[str, str]:
    """
    Extracts (intent_action, project_subject) from a project assistance request.
    Strips conversational preambles ('can you help me...', 'could you assist me in...', 'I want to build...').
    Distinguishes:
      USER INTENT: build / design / create / plan / develop / architect / implement / assist
      PROJECT SUBJECT: compiler in Rust / distributed cache / operating systems / robotics / database
    Returns:
      (intent, subject)
    """
    if not prompt:
        return ("assist", "project")

    p = prompt.strip().rstrip(".?!")
    # Strip conversational wrapper preambles
    raw = re.sub(
        r"^(?:can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|would\s+you\s+(?:please\s+)?|"
        r"i\s+(?:want|would\s+like|need)\s+(?:you\s+to\s+|to\s+)?|please\s+|hey\s+orbis,?\s*|orbis,?\s*)+",
        "",
        p,
        flags=re.IGNORECASE,
    ).strip()

    # Match intent verb and subject remainder
    m = re.match(
        r"^(?:(?:help(?:\s+me)?|assist(?:\s+me)?|guide\s+me|work)\s+)?"
        r"(?:(?:to|in|with|on)\s+)?"
        r"(build(?:ing)?|design(?:ing)?|creat(?:e|ing)|plan(?:ning)?|develop(?:ing)?|architect(?:ing)?|implement(?:ing)?|cod(?:e|ing)|program(?:ming)?|setup|set\s+up|assist|help)?"
        r"(?:\s+(?:with|on|in|for))?"
        r"\s*(?:a\s+|an\s+|the\s+|my\s+)?"
        r"(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        raw_action = (m.group(1) or "assist").lower()
        verb_norm = {
            "building": "build",
            "designing": "design",
            "creating": "create",
            "planning": "plan",
            "developing": "develop",
            "architecting": "architect",
            "implementing": "implement",
            "coding": "code",
            "programming": "program",
        }
        action = verb_norm.get(raw_action, raw_action)
        remainder = m.group(2).strip()
        # Strip trailing 'project' if meaningful subject precedes it (e.g. 'robotics project' -> 'robotics')
        subj = re.sub(r"\s+project$", "", remainder, flags=re.IGNORECASE).strip()
        if not subj:
            subj = "project"
        return action, subj

    return "assist", raw


def extract_system_control_metadata(prompt: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """
    Extracts (action, level, clarification_message) from a system control prompt.
    Actions: 'set_volume', 'get_volume', 'mute', 'unmute',
             'set_brightness', 'get_brightness', 'battery_saver', 'power_status', 'windows_settings'.
    Level: integer in [0, 100], or None.
    Clarification: message to ask user if incomplete/ambiguous/invalid, or None.
    """
    if not prompt:
        return None, None, "Could you please specify what system control action you would like to perform?"

    norm = prompt.strip().lower()

    # 1. Brightness
    if "brightness" in norm or "dim" in norm or "brighten" in norm:
        nums = re.findall(r"(?:^|\s)(-?\d+)(?:%|percent|\b)", norm)
        if nums:
            val = int(nums[-1])
            if val < 0 or val > 100:
                return "set_brightness", None, "Brightness level must be between 0 and 100 percent."
            return "set_brightness", val, None
        if any(w in norm for w in ("get", "check", "what is", "what's", "show", "current", "how bright")):
            return "get_brightness", None, None
        if any(w in norm for w in ("set", "change", "adjust", "dim", "brighten", "turn up", "turn down", "increase", "decrease")):
            return "set_brightness", None, "What brightness level would you like me to set (0-100%)?"
        return "get_brightness", None, None

    # 2. Battery saver & Power status
    if "battery saver" in norm or "power saver" in norm:
        return "set_battery_saver", None, None
    if "battery" in norm or "power" in norm:
        return "get_power_status", None, None

    # 3. Windows settings
    if "setting" in norm or "dark mode" in norm:
        return "get_windows_settings", None, None

    # 4. Mute / Unmute
    if re.search(r"\bun-?mute\b", norm) or "turn sound back on" in norm or "turn on sound" in norm:
        return "unmute", None, None
    if re.search(r"\b(?:mute|silence)\b", norm) or "turn off sound" in norm:
        return "mute", None, None

    # 5. Get volume queries
    if (
        re.search(r"\b(?:what\s+is|what's|get|check|tell\s+me|show)\s+(?:the\s+)?(?:current\s+)?(?:master\s+)?(?:volume|sound|audio)\b", norm)
        or re.search(r"\b(?:current\s+)?(?:volume|audio)\s+level\b", norm)
        or re.search(r"\bhow\s+loud\s+is\s+it\b", norm)
        or norm in ("volume", "volume?", "check volume", "get volume", "current volume")
    ):
        return "get_volume", None, None

    # 6. Check for explicit numbers
    all_nums = re.findall(r"(?:^|\s)(-?\d+)(?:%|percent|\b)", norm)
    if all_nums:
        val = int(all_nums[-1])
        if val < 0 or val > 100:
            return "set_volume", None, "Volume level must be between 0 and 100 percent."
        return "set_volume", val, None

    # 7. Relative adjustments without explicit level:
    if re.search(r"\b(?:increase|turn\s+up|raise|higher|make\s+it\s+louder|louder)\b", norm):
        return "set_volume", None, "What volume level would you like me to set (0-100%)?"
    if re.search(r"\b(?:decrease|turn\s+down|lower|reduce|make\s+it\s+quieter|quieter)\b", norm):
        return "set_volume", None, "What volume level would you like me to set (0-100%)?"

    # 8. Generic "set volume" / "change volume" without level
    if re.search(r"\b(?:set|change|adjust)\s+(?:the\s+)?(?:master\s+)?volume\b", norm):
        return "set_volume", None, "What volume level would you like me to set (0-100%)?"

    return "get_volume", None, None


def extract_app_launch_metadata(prompt: str) -> Dict[str, Any]:
    """Extracts parameters for AppLaunchTool from a user prompt."""
    norm = prompt.strip().lower()
    pdf_match = re.search(r"\b(?:open|view)\s+(?:the\s+)?([a-zA-Z0-9_\-./\\]+\.pdf)\b", norm)
    if pdf_match:
        return {"action": "open_pdf", "target_path": pdf_match.group(1)}

    yt_match = re.search(r"\b(?:play|search\s+for|search)\s+(.+?)\s+on\s+youtube\b", norm)
    if yt_match:
        return {"action": "youtube_search", "search_query": yt_match.group(1).strip()}
    yt_match2 = re.search(r"\byoutube\s+(?:search\s+for|search|play)\s+(.+)\b", norm)
    if yt_match2:
        return {"action": "youtube_search", "search_query": yt_match2.group(1).strip()}

    app_mapping = {
        "notepad": "notepad",
        "chrome": "chrome",
        "google chrome": "chrome",
        "vs code": "code",
        "vscode": "code",
        "visual studio code": "code",
        "code": "code",
        "spotify": "spotify",
        "calculator": "calculator",
        "calc": "calculator",
        "terminal": "terminal",
        "powershell": "terminal",
        "cmd": "terminal",
        "whatsapp": "whatsapp",
        "edge": "edge",
        "microsoft edge": "edge",
        "msedge": "edge",
        "opera": "opera",
        "opera gx": "opera",
        "steam": "steam",
        "discord": "discord",
        "explorer": "explorer",
        "file explorer": "explorer",
        "files": "explorer",
        "settings": "settings",
        "windows settings": "settings",
        "word": "word",
        "excel": "excel",
        "powerpoint": "powerpoint",
    }
    for key, app in app_mapping.items():
        if re.search(rf"\b(?:open|launch|start|run)\s+(?:the\s+)?{re.escape(key)}\b", norm):
            return {"action": "launch_app", "app_name": app}

    return {"action": "launch_app", "app_name": "notepad"}


def extract_network_control_metadata(prompt: str) -> Dict[str, Any]:
    """Extracts parameters for NetworkControlTool from a user prompt."""
    norm = prompt.strip().lower()
    if "bluetooth" in norm:
        if "device" in norm or "paired" in norm:
            return {"action": "list_bluetooth_devices"}
        return {"action": "get_bluetooth_status"}
    if "scan" in norm or "list" in norm or "available" in norm:
        return {"action": "scan_wifi"}
    if "disconnect" in norm or "turn off" in norm or "disable" in norm:
        return {"action": "disconnect_wifi"}
    return {"action": "get_wifi_status"}


def extract_hardware_control_metadata(prompt: str) -> Dict[str, Any]:
    """Extracts parameters for HardwareControlTool from a user prompt."""
    norm = prompt.strip().lower()
    if "game" in norm:
        game_match = re.search(r"\b(?:launch|start|play)\s+(?:the\s+)?(?:game\s+)?([a-zA-Z0-9_\-]+)\b", norm)
        game_name = game_match.group(1) if game_match else "cyberwar"
        return {"action": "launch_game", "game_name": game_name}
    color = "cyan"
    for c in ("red", "green", "blue", "cyan", "magenta", "yellow", "white", "orange", "purple"):
        if c in norm:
            color = c
            break
    mode = "static"
    for m in ("breathing", "wave", "reactive", "cycle"):
        if m in norm:
            mode = m
            break
    return {"action": "set_rgb_color", "color": color, "mode": mode, "brightness": 100}


def extract_messaging_metadata(prompt: str) -> Dict[str, Any]:
    """Extracts parameters for MessagingTool from a user prompt."""
    norm = prompt.strip().lower()
    platform = "whatsapp"
    if "email" in norm:
        platform = "email"
    elif "sms" in norm or "text message" in norm:
        platform = "sms"

    to_match = re.search(r"\bto\s+([a-zA-Z0-9_@.\-]+)\b", prompt, flags=re.IGNORECASE)
    recipient = to_match.group(1) if to_match else "contact"

    msg_match = re.search(r"\b(?:saying|message|text)\s+['\"]?(.+?)['\"]?$", prompt, flags=re.IGNORECASE)
    message = msg_match.group(1) if msg_match else "Hello from ORBIS"
    return {"channel": platform, "recipient": recipient, "message": message}


def classify_capability(prompt: str) -> CapabilityClass:
    """
    Classifies a natural language query into an authoritative CapabilityClass.
    Operates strictly on linguistic, grammatical, and functional structures, NOT topic whitelists.
    """
    if not prompt or not prompt.strip():
        return CapabilityClass.UNKNOWN_AMBIGUOUS

    # 1. Ambiguity check
    ambiguous, _ = is_ambiguous_or_malformed(prompt)
    if ambiguous:
        return CapabilityClass.UNKNOWN_AMBIGUOUS

    raw_lower = prompt.strip().lower()
    norm = normalize_prompt_for_classification(prompt)

    # 2. ABOUT_ORBIS_CAPABILITIES
    # "what can you do", "what all can you do", "what do you do", "tell me what you can do",
    # "how can you help", "what are your capabilities", "who are you"
    if (
        re.search(
            r"\bwhat\s+(?:all\s+)?(?:are\s+the\s+things\s+)?(?:(?:can|do|could)\s+you|you\s+can)\s+(?:do|help|assist|handle|perform)\b",
            norm,
        )
        or re.search(r"\bwhat\s+(?:all\s+)?(?:can\s+you|you\s+can)\s+(?:help(?:\s+me)?\s+with|help\b)", norm)
        or re.search(r"\bhow\s+(?:can\s+you|you\s+can)\s+(?:help|assist)\b", norm)
        or re.search(r"\b(?:tell|show|explain)\s+(?:to\s+)?me\s+(?:what|how)\s+(?:you|orbis)\s+(?:can\s+do|do|can\s+help)\b", norm)
        or re.search(r"\bwhat\s+are\s+you\s+(?:able\s+to\s+do|capable\s+of)\b", norm)
        or re.search(r"\bwhat\s+does?\s+(?:you|orbis)\s+do\b", norm)
        or re.search(r"\bwho\s+are\s+you\b", norm)
        or re.search(r"\b(?:your\s+)?capabilities\b", norm)
    ):
        return CapabilityClass.ABOUT_ORBIS_CAPABILITIES

    # 3. REAL_TIME_UTILITY: System Time, Date, and Host Environment
    has_time_query = bool(
        re.search(r"\b(?:what\s+time\s+is\s+it|what\s+is\s+the\s+(?:current\s+)?time|tell\s+me\s+the\s+time|current\s+time)\b", norm)
        or re.search(r"\btime\s+(?:is\s+it\s+)?(?:now|in\s+[a-zA-Z\s]+)\b", norm)
    )
    has_date_query = bool(
        re.search(r"\b(?:what\s+(?:is\s+)?today'?s\s+date|what\s+is\s+the\s+date|what\s+date\s+is\s+it|today'?s\s+date|current\s+date)\b", norm)
        or re.search(r"\b(?:what|which)\s+day\s+(?:is\s+it|is\s+today|today\s+is)\b", norm)
    )
    has_system_info = bool(re.search(r"\b(?:system\s+info|host\s+info|environment\s+info|what\s+os\b)\b", norm))

    if has_time_query or has_date_query or has_system_info:
        return CapabilityClass.REAL_TIME_UTILITY

    # 4. Workspace Restoration
    is_restoration = (
        bool(re.search(r"\bcontinue\s+(?:from\s+)?where\s+(?:i|we)\s+left\s+off\b", norm))
        or (
            ("restore" in norm or "reopen" in norm or "resume" in norm)
            and any(w in norm for w in ("workspace", "windows", "layout", "session", "apps", "project", "work"))
        )
        or (
            "open" in norm
            and any(
                w in norm
                for w in (
                    "project i was working on",
                    "project i was working on earlier",
                    "what i was working on",
                    "what i was doing",
                    "workspace i was working on",
                    "workspace from yesterday",
                    "previous workspace",
                    "previous project",
                    "last project",
                    "project from earlier",
                    "where i left off",
                )
            )
        )
        or (
            norm.startswith("open project ")
            or norm.startswith("open the project ")
            or (re.search(r"^open\s+(?:the\s+)?project\s+[a-zA-Z0-9_\-]+[?.!]?$", norm) is not None)
        )
        or (
            ("preview" in norm or "dry run" in norm or "dry-run" in norm or "plan only" in norm)
            and any(w in norm for w in ("restore", "restoration", "workspace"))
        )
        or ("preview what you would restore" in norm)
    )
    if is_restoration:
        return CapabilityClass.WORKSPACE_RESTORATION

    # 5. Intent & Workspace Continuity
    if ContinuityService.is_intent_query(prompt) or (norm and ContinuityService.is_intent_query(norm)):
        return CapabilityClass.INTENT_CONTINUITY
    if ContinuityService.is_continuity_query(prompt) or (norm and ContinuityService.is_continuity_query(norm)):
        return CapabilityClass.WORKSPACE_CONTINUITY

    # 6. Project Assistance
    # Captures requests for technical project guidance, system architecture, or building software systems:
    is_project_assistance = bool(
        re.search(
            r"\b(?:(?:help|assist|guide)(?:\s+me)?\s+(?:to\s+|with\s+|in\s+|on\s+)?(?:build(?:ing)?|design(?:ing)?|develop(?:ing)?|architect(?:ing)?|implement(?:ing)?|cod(?:e|ing)|program(?:ming)?|plan(?:ning)?|creat(?:e|ing))|"
            r"(?:help|assist|guide)(?:\s+me)?\s+with\s+(?:a\s+|an\s+|my\s+)?(?:[a-zA-Z0-9_\-\s]+?\s+)?project|"
            r"i\s+(?:want|need)\s+to\s+(?:build|design|develop|architect|implement|code|program))\b",
            norm,
        )
        or re.search(r"\bproject\s+(?:assistance|advice|guidance|planning|help)\b", norm)
    )
    if is_project_assistance:
        return CapabilityClass.PROJECT_ASSISTANCE

    # 7. Document Generation & File Creation
    has_creation_verb = any(
        v in norm for v in ("create", "write", "generate", "save", "produce", "draft", "prepare")
    )
    has_doc_target = any(
        t in norm
        for t in (
            "report", "document", "readme", "markdown", ".md", ".txt", "text file",
            "file", "study notes", "notes", "recommendations",
            "excel", "spreadsheet", "sheet", ".xlsx", "presentation", "slides", "deck", ".pptx",
            "word", ".docx", "pdf", ".pdf", "table", "csv"
        )
    )
    has_research_signals = any(
        r in norm
        for r in ("search", "research", "find online", "lookup online", "lookup", "explore", "investigate", "find")
    )

    if has_creation_verb and has_doc_target:
        if has_research_signals:
            return CapabilityClass.DOCUMENT_GENERATION
        return CapabilityClass.FILE_CREATION

    # 8. Terminal Commands
    has_terminal_cmd = any(
        c in norm
        for c in (
            "terminal", "run command", "command line", "bash", "shell",
            "pytest", "run test", "run tests", "git status", "git log", "git diff", "git clean",
            "check python version", "installed python version", "local python version", "pip list"
        )
    ) and not any(w in norm for w in ("latest", "recent", "newest", "current release"))
    if has_terminal_cmd:
        return CapabilityClass.TERMINAL_COMMANDS

    # 9. Workspace Inspection (File Listing)
    is_file_listing = (
        ("file" in norm or "directory" in norm or "folder" in norm or "files" in norm)
        and any(w in norm for w in ("check", "list", "show", "see", "view", "what"))
        and any(w in norm for w in ("workspace", "project", "repo", "here", "root"))
    ) or norm in ("check my workspace", "check workspace", "list workspace files", "inspect workspace")
    if is_file_listing:
        return CapabilityClass.WORKSPACE_INSPECTION

    # 10. File Reading / Summarization
    is_file_reading = (
        any(w in norm for w in ("read", "summarize", "view", "inspect", "open", "show"))
        and any(w in norm for w in ("readme", "file", "contents", "content", ".md", ".txt", ".py", ".json", ".toml", ".yaml", ".yml", ".env"))
        and not has_creation_verb
    )
    if is_file_reading:
        return CapabilityClass.FILE_READING

    # 11. Desktop & Active Window Inspection
    is_desktop_query = any(
        w in norm
        for w in (
            "active window", "what application am i using", "what app am i using",
            "what am i using", "what window is open", "what window is active",
            "desktop inspection", "list processes", "running processes", "active app",
            "on my screen", "on the screen", "desktop window", "desktop windows"
        )
    ) or ("desktop" in norm and any(w in norm for w in ("window", "windows", "apps", "applications"))) or bool(re.search(r"\bwhat\s+(?:window|application|app|is\s+currently\s+on\s+my\s+screen)\b", norm)) or bool(re.search(r"\bwhat\s+(?:window|application|app)\s+is\s+(?:currently\s+)?(?:open|active|running|focused)\b", norm))
    if is_desktop_query:
        return CapabilityClass.DESKTOP_INSPECTION

    # Geometric / math volume query exclusion:
    # "volume of a sphere", "calculate volume of cylinder", "volume formula for cone"
    is_geometric_volume = bool(
        re.search(
            r"\bvolume\s+of\s+(?:a\s+|an\s+|the\s+)?(?:sphere|cylinder|cone|cube|box|prism|pyramid|hemisphere|shape|container|tank|room|gas|liquid)\b",
            norm,
        )
        or re.search(r"\b(?:calculate|formula\s+for|find\s+the)\s+volume\b", norm)
    )

    # 12. Messaging (WhatsApp, Email, SMS)
    is_messaging = bool(
        re.search(r"\b(?:send|compose)\s+(?:a\s+)?(?:whatsapp|email|sms|text\s+message)\b", norm)
        or re.search(r"\b(?:send\s+message|send\s+email|send\s+sms|send\s+whatsapp)\b", norm)
    )
    if is_messaging:
        return CapabilityClass.MESSAGING

    # 13. Network Control (Wi-Fi, Bluetooth)
    is_network_control = bool(
        re.search(r"\b(?:wi-?fi|wifi|wlan|bluetooth)\b", norm)
        and any(w in norm for w in ("turn on", "turn off", "connect", "disconnect", "status", "scan", "list", "check", "devices", "networks", "paired", "toggle", "enable", "disable"))
    )
    if is_network_control:
        return CapabilityClass.NETWORK_CONTROL

    # 14. Hardware Control (RGB Keyboard, Game Launcher)
    is_hardware_control = bool(
        re.search(r"\b(?:rgb|backlight|keyboard\s+lighting|keyboard\s+color|keyboard\s+lights?)\b", norm)
        or re.search(r"\b(?:launch|start|play)\s+(?:the\s+)?game\b", norm)
    )
    if is_hardware_control:
        return CapabilityClass.HARDWARE_CONTROL

    # 15. Application Launching (Allowlisted apps, PDFs, YouTube)
    is_app_launch = bool(
        re.search(r"\b(?:open|launch|start|run)\s+(?:app|application|program\s+)?(?:notepad|chrome|google\s+chrome|vs\s*code|visual\s+studio\s+code|code|spotify|calculator|calc|whatsapp|terminal|powershell|cmd|edge|msedge|opera|opera\s+gx|steam|discord|explorer|file\s+explorer|files|settings|windows\s+settings|word|excel|powerpoint)\b", norm)
        or re.search(r"\b(?:open|view)\s+[a-zA-Z0-9_\-./\\]+\.pdf\b", norm)
        or re.search(r"\b(?:play|search)\s+(?:on\s+)?youtube\b", norm)
        or re.search(r"\byoutube\s+(?:search|play)\b", norm)
    ) and not any(w in norm for w in ("workspace i was working on", "project i was working on", "where i left off", "previous project", "last project"))
    if is_app_launch:
        return CapabilityClass.APP_LAUNCHING

    # 16. System Control (Audio Volume, Mute, Brightness, Power/Battery, Windows Settings)
    if not is_geometric_volume:
        is_system_control = (
            bool(re.search(r"\b(?:un-?mute)\b", norm))
            or bool(re.search(r"\b(?:mute|silence)\s*(?:the\s+)?(?:computer|pc|system|audio|sound|speakers?)?\b", norm))
            or (re.search(r"\b(?:mute|silence)\b", norm) and not re.search(r"\bmute\s+(?:point|button|switch)\b", norm))
            or bool(re.search(r"\b(?:get|check|what\s+is|what's|show)\s+(?:the\s+)?(?:current\s+)?(?:master\s+)?(?:volume|sound|audio\s+level)\b", norm))
            or bool(re.search(r"\b(?:current\s+)?(?:volume|audio)\s+level\b", norm))
            or bool(re.search(r"\b(?:set|increase|decrease|raise|lower|change|adjust|turn\s+(?:up|down)|put|make)\s+(?:the\s+)?(?:master\s+)?(?:volume|sound|audio)\b", norm))
            or bool(re.search(r"\bvolume\s+(?:to\s+)?\d+\s*(?:%|percent)?\b", norm))
            or bool(re.search(r"\b(?:make\s+it|turn\s+it)\s+(?:louder|quieter)\b", norm))
            or bool(re.search(r"\b(?:brightness|screen\s+brightness|display\s+brightness)\b", norm))
            or bool(re.search(r"\b(?:dim|brighten)\s+(?:the\s+)?(?:screen|display)\b", norm))
            or bool(re.search(r"\b(?:battery\s+saver|power\s+saver|power\s+status|battery\s+status)\b", norm))
            or bool(re.search(r"\b(?:windows\s+settings|system\s+settings|dark\s+mode\s+setting)\b", norm))
        )
        if is_system_control:
            return CapabilityClass.SYSTEM_CONTROL

    # 13. Fresh Information & Explicit Web Search
    # Explicit web search verbs
    has_explicit_web_search = any(
        w in norm
        for w in (
            "search the web", "search web", "web search", "lookup online", "look up online",
            "find online", "browse web", "google", "search online"
        )
    ) or (
        norm.startswith("search for ")
        or norm.startswith("search ")
        or norm.startswith("research ")
    )
    # Temporal / fresh information signals (e.g. latest version, latest release, recent news)
    has_freshness_signal = bool(
        re.search(
            r"\b(?:latest\s+news|recent\s+news|today'?s\s+news|news\s+today|"
            r"(?:latest|current)\s+(?:[a-zA-Z0-9_\-]+\s+)?(?:version|release|update)|"
            r"recently\s+released|"
            r"recent\s+(?:articles?|news|updates?|developments?|papers?|breakthroughs?)|"
            r"happened\s+recently|recently\s+in\b)\b",
            norm,
        )
        or re.search(
            r"\b(?:current\s+(?:price|stock\s+price|market\s+cap|score|rate|exchange\s+rate|weather)|"
            r"live\s+(?:score|updates?|match|stream))\b",
            norm,
        )
        or re.search(
            r"\b(?:who\s+(?:won|is\s+winning)|winner\s+of|champions?\s+of|results?\s+of)\b.+\b202[4-9]\b",
            norm,
        )
        or re.search(
            r"\bwho\s+is\s+(?:the\s+)?(?:current\s+)?(?:chief\s+minister|prime\s+minister|president|ceo|cto|cfo|governor|mayor)\b",
            norm,
        )
        or (
            bool(re.search(r"\b(?:today|this\s+week|this\s+month)\b", norm))
            and any(w in norm for w in ("news", "events", "happened", "headline", "headlines", "weather", "updates"))
        )
    )
    if has_explicit_web_search or has_freshness_signal:
        return CapabilityClass.FRESH_INFORMATION

    # 13. Technical Explanations & Comparisons
    # "explain X", "can you explain X simply", "how does X work", "why does X use...", "compare X with Y"
    stripped = LEADING_WRAPPERS.sub("", norm).strip()
    stripped = TRAILING_QUALIFIERS.sub("", stripped).strip()

    is_comparison = bool(
        re.search(r"\b(?:compare|versus|vs\.?|difference\s+between|differences\s+between)\b", norm)
        or re.search(r"\bhow\s+(?:does|do)\s+.+\s+differ\s+(?:from|with)\b", norm)
        or re.search(r"\bhow\s+are\s+.+\s+different\b", norm)
        or re.search(r"\bside\s+by\s+side\b", norm)
        or re.search(r"\bwhich\s+(?:one\s+)?(?:is|are)\s+(?:faster|better|easier|more|less|preferred|recommended)\b", norm)
        or re.search(r"\bbetween\s+.+\s+and\s+.+\b", norm)
    )
    is_explanation = bool(
        re.search(r"\bexplain\b", norm)
        or re.search(r"\bhow\s+(?:does|do|works?|is)\b", norm)
        or re.search(r"\bwhy\s+(?:does|do|is|are|did)\b", norm)
        or re.search(r"\b(?:give|show)\s+(?:me\s+)?(?:a\s+)?(?:simple\s+)?(?:real-?world\s+)?example\b", norm)
        or re.search(r"\bexample\s+of\b", norm)
    )
    if is_comparison or is_explanation:
        return CapabilityClass.TECHNICAL_EXPLANATION

    # 14. General Information & Factual Questions
    # "tell me about X", "who is X", "what is X", "history of X", "define X"
    is_general_info = bool(
        re.search(r"\b(?:who\s+is|who\s+was)\b", norm)
        or re.search(r"\bwhat\s+(?:is|are|was|were)\b", norm)
        or re.search(r"\btell\s+me\s+about\b", norm)
        or re.search(r"\bhistory\s+of\b", norm)
        or re.search(r"\bdefine\b", norm)
        or re.search(r"\bmeaning\s+of\b", norm)
    )
    if is_general_info:
        return CapabilityClass.GENERAL_INFORMATION

    # 16. Informational queries with question signals or query markers
    has_info_signal = bool(
        re.search(r"\b(?:what|who|where|when|why|how|tell|about|info|information|history|define|meaning|calculate|compute|formula)\b", norm)
        or is_geometric_volume
        or norm.endswith("?")
    )
    if has_info_signal:
        return CapabilityClass.GENERAL_INFORMATION

    return CapabilityClass.UNKNOWN_AMBIGUOUS
