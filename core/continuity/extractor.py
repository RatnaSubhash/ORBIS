"""
Workspace Resource Extractor for ORBIS.
Provides safe, deterministic, structure-based extraction of project names
and file evidence from active window titles across supported developer tools.

Strict safety invariants:
- Never exposes protected resources (.env, credentials, SSH keys, certificates).
- Never infers a project or file from process name alone.
- Never fabricates URLs from browser window titles.
- Distinguishes actual files from internal editor pages (e.g. Release Notes, Settings).
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import List, Literal, Optional, Set, Tuple


# Known common file extensions for development and text editing
COMMON_FILE_EXTENSIONS: Set[str] = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".toml", ".yaml", ".yml",
    ".html", ".css", ".scss", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java",
    ".kt", ".cs", ".rb", ".php", ".sh", ".bat", ".ps1", ".sql", ".txt", ".xml",
    ".csv", ".ini", ".cfg", ".conf", ".env", ".proto", ".lock",
}

# Standalone exact file names without extensions
STANDALONE_FILENAMES: Set[str] = {
    "dockerfile", "makefile", "license", "procfile", "gemfile", "vagrantfile",
    "cmakelists.txt", ".gitignore", ".dockerignore", ".eslintrc", ".prettierrc",
}

# Internal editor tab titles that must never be treated as user project files
INTERNAL_EDITOR_PAGES: Set[str] = {
    "welcome", "get started", "settings", "keyboard shortcuts", "extensions",
    "interactive playground", "process explorer", "launchpad", "open folder",
    "visual studio code", "untitled",
}

# Sensitive / protected file patterns that must never be exposed or persisted in resources
PROTECTED_RESOURCE_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"^\.git($|[\\/])", re.IGNORECASE),
    re.compile(r"id_rsa|id_ed25519|\.pem$|\.key$|private.*key", re.IGNORECASE),
    re.compile(r"credentials", re.IGNORECASE),
    re.compile(r"system32|sam$", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|api[_-]?secret|api[_-]?token|auth[_-]?token|secret[_-]?key|user_passwords?|passwords?\.(?:txt|csv|json|yaml|yml|tsv))", re.IGNORECASE),
]


def is_protected_resource(name: Optional[str]) -> bool:
    """
    Evaluates whether a filename or path represents a protected / sensitive resource.
    Matches .env, .env.*, credentials, private keys, certificates, etc.
    """
    if not name or not name.strip():
        return False
    clean = name.strip()
    # Check base filename and stem
    base_name = Path(clean).name.lower()
    for pat in PROTECTED_RESOURCE_PATTERNS:
        if pat.search(base_name) or pat.search(clean.lower()):
            return True
    return False


ORBIS_SHELL_TITLES: Set[str] = {
    "orbis agent shell",
    "orbis-frontend",
    "orbis agent",
    "orbis shell",
    "orbis",
}

ORBIS_SHELL_PATTERNS: List[re.Pattern] = [
    re.compile(r"^orbis\s*(?:[-–—:]\s*)?(?:agent\s+shell|frontend|shell|agent|assistant)\b", re.IGNORECASE),
    re.compile(r"\borbis-frontend\b", re.IGNORECASE),
]

ORBIS_PATH_PATTERNS: List[re.Pattern] = [
    re.compile(r"[\\/]orbis[\\/](?:frontend|dist|node_modules)[\\/]", re.IGNORECASE),
    re.compile(r"[\\/]projects[\\/]orbis[\\/]frontend", re.IGNORECASE),
]


def is_orbis_control_plane(
    process_name: Optional[str],
    window_title: Optional[str] = None,
    executable_path: Optional[str] = None,
) -> bool:
    """
    Identifies whether an observed window or process is the ORBIS assistant's
    own control-plane / Electron UI shell, rather than the user's workspace.

    Robust identity checks:
    - Process name matches electron or orbis executable.
    - AND (window title matches ORBIS UI shell ('ORBIS Agent Shell', 'orbis-frontend', etc.)
           OR executable path points to the ORBIS repository/distribution).

    Invariants:
    - Never classifies an unrelated Electron app (e.g. Slack, custom Electron apps) as ORBIS.
    - Never classifies IDEs (VS Code 'Code.exe', Antigravity IDE) as ORBIS control-plane,
      even if the user is editing the ORBIS project in their IDE.
    """
    if not process_name:
        return False

    proc = process_name.strip().lower()
    if proc not in ("electron.exe", "electron", "orbis.exe", "orbis"):
        return False

    # Check window title
    if window_title:
        clean_title = window_title.strip().lower()
        if clean_title in ORBIS_SHELL_TITLES:
            return True
        for pat in ORBIS_SHELL_PATTERNS:
            if pat.search(clean_title):
                return True

    # Check executable path if provided
    if executable_path:
        clean_path = executable_path.strip().lower()
        for pat in ORBIS_PATH_PATTERNS:
            if pat.search(clean_path):
                return True

    if window_title and window_title.strip().lower() == "orbis":
        return True

    return False


@dataclass(frozen=True)
class ExtractedResourceEvidence:
    """
    Factual evidence extracted from a window title.
    Keeps observed resource names distinct from verified filesystem paths.
    """
    name: Optional[str]
    resource_type: Literal["file", "project", "directory", "unknown"]
    source: str = "window_title"
    project: Optional[str] = None
    is_protected: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "resource_type": self.resource_type,
            "source": self.source,
            "project": self.project,
            "is_protected": self.is_protected,
        }


class WorkspaceResourceExtractor:
    """
    Deterministic, structural resource extractor for active window titles.
    Does not use LLMs, heuristics with high false-positive rates, or phrase whitelists.
    """

    # Unsaved buffer markers used by editors (e.g. '● ', '* ')
    DIRTY_BUFFER_PATTERN = re.compile(r"^[●\*\u25cf]\s*")

    # Regex patterns for structural title parsing
    # VS Code format: [● ]<file> - <project> - Visual Studio Code
    #              or <project> - Visual Studio Code
    VSCODE_THREE_PART = re.compile(r"^(.+?)\s+-\s+(.+?)\s+-\s+(?:Visual Studio Code|Code)$", re.IGNORECASE)
    VSCODE_TWO_PART = re.compile(r"^(.+?)\s+-\s+(?:Visual Studio Code|Code)$", re.IGNORECASE)

    # JetBrains format: <project> – <file> or <file> – <project> [path]
    JETBRAINS_TITLE = re.compile(r"^(.+?)\s+[–-]\s+\[?(.+?)\]?(?:\s+\[.*\])?$", re.IGNORECASE)

    # Notepad format: [*]<file> - Notepad
    NOTEPAD_TITLE = re.compile(r"^(.+?)\s+-\s+Notepad$", re.IGNORECASE)

    @classmethod
    def _is_valid_file_name(cls, name: str) -> bool:
        """Determines if a candidate string has a valid file structure."""
        if not name or not name.strip():
            return False
        clean = name.strip()
        lower = clean.lower()

        if any(lower.startswith(prefix) for prefix in ("release notes:", "untitled-")):
            return False
        if lower in INTERNAL_EDITOR_PAGES:
            return False

        # Check for dotfiles like .env, .gitignore, etc. or protected patterns
        if is_protected_resource(clean) or lower.startswith(".env") or lower.startswith(".git"):
            return True

        # Check for standard extension or standalone filename
        p = Path(clean)
        ext = p.suffix.lower()
        if ext in COMMON_FILE_EXTENSIONS:
            return True
        if lower in STANDALONE_FILENAMES:
            return True

        # Has an extension of 1-5 chars and no spaces in extension
        if ext and len(ext) <= 6 and " " not in ext and len(p.stem) >= 1:
            return True

        return False

    @classmethod
    def extract(
        cls, process_name: Optional[str], window_title: Optional[str]
    ) -> ExtractedResourceEvidence:
        """
        Extracts structured project/file evidence from an observed window title.
        Returns ExtractedResourceEvidence.
        """
        if not process_name or not window_title or not window_title.strip():
            return ExtractedResourceEvidence(
                name=None,
                resource_type="unknown",
                is_protected=False,
            )

        p_lower = process_name.lower()
        title = window_title.strip()

        # 0. ORBIS Control Plane Shell - Never treated as a project or file resource
        if is_orbis_control_plane(process_name, window_title):
            return ExtractedResourceEvidence(
                name=None,
                resource_type="unknown",
                is_protected=False,
            )

        # 1. Visual Studio Code / Code.exe / Antigravity IDE
        if p_lower in ("code.exe", "code", "antigravity ide.exe", "antigravity ide"):
            return cls._extract_vscode(title)

        # 2. JetBrains IDEs (PyCharm, IntelliJ, WebStorm, CLion, GoLand, Rider)
        if any(jb in p_lower for jb in ("pycharm", "idea", "webstorm", "clion", "goland", "rider")):
            return cls._extract_jetbrains(title)

        # 3. Notepad
        if p_lower in ("notepad.exe", "notepad"):
            return cls._extract_notepad(title)

        # 4. Browsers (Chrome, Edge, Firefox, Brave) - NEVER fabricate files or URLs
        # Browsers only expose web page tab titles in the OS window title.
        # We explicitly return unknown with no fake resources.
        if any(b in p_lower for b in ("chrome", "msedge", "firefox", "brave", "opera")):
            return ExtractedResourceEvidence(
                name=None,
                resource_type="unknown",
                is_protected=False,
            )

        # 5. Windows Explorer / Task Switching
        if p_lower in ("explorer.exe", "explorer"):
            # If explorer window title is 'Task Switching' or system window, no resource
            if title.lower() in ("task switching", "file explorer"):
                return ExtractedResourceEvidence(
                    name=None,
                    resource_type="unknown",
                    is_protected=False,
                )
            # If an actual folder path is shown (e.g. 'C:\Projects\ORBIS')
            if Path(title).is_absolute() or ":\\" in title or ":/" in title:
                return ExtractedResourceEvidence(
                    name=title,
                    resource_type="directory",
                    project=Path(title).name,
                    is_protected=is_protected_resource(title),
                )
            return ExtractedResourceEvidence(
                name=None,
                resource_type="unknown",
                is_protected=False,
            )

        # Default fallback
        return ExtractedResourceEvidence(
            name=None,
            resource_type="unknown",
            is_protected=False,
        )

    @classmethod
    def _extract_vscode(cls, title: str) -> ExtractedResourceEvidence:
        """Extracts project and file evidence from VS Code and Antigravity IDE titles."""
        # 1. Check 3-part: <part0> - <part1> - <part2>
        parts = [p.strip() for p in title.split(" - ")]
        if len(parts) >= 3:
            p0 = parts[0]
            p1 = parts[1]
            p2 = " - ".join(parts[2:])

            # Case A: <file> - <project> - <editor>
            if any(ed in p2.lower() for ed in ("visual studio code", "code", "antigravity ide")):
                clean_file = cls.DIRTY_BUFFER_PATTERN.sub("", p0).strip()
                if cls._is_valid_file_name(clean_file):
                    return ExtractedResourceEvidence(
                        name=clean_file,
                        resource_type="file",
                        project=p1 if p1 else None,
                        is_protected=is_protected_resource(clean_file),
                    )
                else:
                    return ExtractedResourceEvidence(
                        name=None,
                        resource_type="project" if p1 else "unknown",
                        project=p1 if p1 else None,
                        is_protected=False,
                    )

            # Case B: <project> - <editor> - <view/subpage>
            if any(ed in p1.lower() for ed in ("visual studio code", "code", "antigravity ide")):
                clean_target = cls.DIRTY_BUFFER_PATTERN.sub("", p2).strip()
                clean_proj = p0.strip()
                if cls._is_valid_file_name(clean_target):
                    return ExtractedResourceEvidence(
                        name=clean_target,
                        resource_type="file",
                        project=clean_proj if clean_proj else None,
                        is_protected=is_protected_resource(clean_target),
                    )
                else:
                    return ExtractedResourceEvidence(
                        name=None,
                        resource_type="project" if clean_proj else "unknown",
                        project=clean_proj if clean_proj else None,
                        is_protected=False,
                    )

        # 2. Check 2-part: <part0> - <editor>
        if len(parts) == 2:
            p0 = parts[0]
            p1 = parts[1]
            if any(ed in p1.lower() for ed in ("visual studio code", "code", "antigravity ide")):
                clean_target = cls.DIRTY_BUFFER_PATTERN.sub("", p0).strip()
                if cls._is_valid_file_name(clean_target):
                    return ExtractedResourceEvidence(
                        name=clean_target,
                        resource_type="file",
                        project=None,
                        is_protected=is_protected_resource(clean_target),
                    )
                elif clean_target.lower() not in INTERNAL_EDITOR_PAGES:
                    return ExtractedResourceEvidence(
                        name=None,
                        resource_type="project",
                        project=clean_target,
                        is_protected=False,
                    )

        # Standalone or unmatched title
        return ExtractedResourceEvidence(
            name=None,
            resource_type="unknown",
            is_protected=False,
        )

    @classmethod
    def _extract_jetbrains(cls, title: str) -> ExtractedResourceEvidence:
        """Extracts project and file evidence from JetBrains IDE titles."""
        m = cls.JETBRAINS_TITLE.match(title)
        if m:
            part1 = cls.DIRTY_BUFFER_PATTERN.sub("", m.group(1).strip()).strip()
            part2 = cls.DIRTY_BUFFER_PATTERN.sub("", m.group(2).strip()).strip()

            if cls._is_valid_file_name(part1):
                return ExtractedResourceEvidence(
                    name=part1,
                    resource_type="file",
                    project=part2,
                    is_protected=is_protected_resource(part1),
                )
            elif cls._is_valid_file_name(part2):
                return ExtractedResourceEvidence(
                    name=part2,
                    resource_type="file",
                    project=part1,
                    is_protected=is_protected_resource(part2),
                )
            else:
                return ExtractedResourceEvidence(
                    name=None,
                    resource_type="project",
                    project=part1 or part2,
                    is_protected=False,
                )

        return ExtractedResourceEvidence(
            name=None,
            resource_type="unknown",
            is_protected=False,
        )

    @classmethod
    def _extract_notepad(cls, title: str) -> ExtractedResourceEvidence:
        """Extracts file evidence from Notepad titles."""
        m = cls.NOTEPAD_TITLE.match(title)
        if m:
            raw = m.group(1).strip()
            clean = cls.DIRTY_BUFFER_PATTERN.sub("", raw).strip()
            if cls._is_valid_file_name(clean) or "." in clean:
                return ExtractedResourceEvidence(
                    name=clean,
                    resource_type="file",
                    project=None,
                    is_protected=is_protected_resource(clean),
                )
        return ExtractedResourceEvidence(
            name=None,
            resource_type="unknown",
            is_protected=False,
        )
