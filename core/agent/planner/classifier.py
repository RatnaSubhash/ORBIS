"""
Intent and Capability Classifier for ORBIS.
Classifies natural language requests into abstract request types / capability classes.
Does NOT use topic or entity whitelists.
Supports conversational wrappers, politeness markers, contractions, Indian-English phrasing,
and transcription variations.
"""

from enum import Enum
import re
from typing import Optional, Tuple

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
    if has_question_starter and trailing_dangling:
        return True, "Your query appears incomplete. Could you please specify the full topic or question?"

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

    # 4. Intent & Workspace Continuity
    if ContinuityService.is_intent_query(prompt):
        return CapabilityClass.INTENT_CONTINUITY
    if ContinuityService.is_continuity_query(prompt):
        return CapabilityClass.WORKSPACE_CONTINUITY

    # 5. Workspace Restoration
    if (
        ("restore" in norm or "reopen" in norm)
        and any(w in norm for w in ("workspace", "windows", "layout", "session", "apps"))
    ):
        return CapabilityClass.WORKSPACE_RESTORATION

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
            "file", "study notes", "notes", "recommendations"
        )
    )
    has_research_signals = any(
        r in norm
        for r in ("search", "research", "find online", "lookup online", "lookup", "explore", "investigate")
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
            "on my screen", "on the screen"
        )
    ) or bool(re.search(r"\bwhat\s+(?:window|application|app|is\s+currently\s+on\s+my\s+screen)\b", norm)) or bool(re.search(r"\bwhat\s+(?:window|application|app)\s+is\s+(?:currently\s+)?(?:open|active|running|focused)\b", norm))
    if is_desktop_query:
        return CapabilityClass.DESKTOP_INSPECTION

    # 12. Fresh Information & Explicit Web Search
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
            r"\b(?:latest\s+news|recent\s+news|today'?s\s+news|latest\s+version|current\s+version|"
            r"latest\s+release|current\s+release|recently\s+released|latest\s+update|"
            r"recent\s+(?:articles?|news|updates?|developments?|papers?|breakthroughs?))\b",
            norm,
        )
        or re.search(
            r"\bwho\s+is\s+(?:the\s+)?(?:current\s+)?(?:chief\s+minister|prime\s+minister|president|ceo|cto|cfo|governor|mayor)\b",
            norm,
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
        or re.search(r"\bhow\s+does\s+.+\s+differ\s+from\b", norm)
    )
    is_explanation = bool(
        re.search(r"\bexplain\b", norm)
        or re.search(r"\bhow\s+(?:does|do|works?|is)\b", norm)
        or re.search(r"\bwhy\s+(?:does|do|is|are|did)\b", norm)
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
        re.search(r"\b(?:what|who|where|when|why|how|tell|about|info|information|history|define|meaning)\b", norm)
        or norm.endswith("?")
    )
    if has_info_signal:
        return CapabilityClass.GENERAL_INFORMATION

    return CapabilityClass.UNKNOWN_AMBIGUOUS
