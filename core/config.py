"""
Centralized Configuration for ORBIS.
Supports environment-based configuration, .env files, and overrides for testing.
Extended for Phase 2 Workspace Observation and Path Safety boundaries.
"""

from pathlib import Path
from typing import List, Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Application Settings
    orbis_env: Literal["development", "test", "production"] = Field(
        default="development",
        description="Application environment mode",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    db_path: str = Field(
        default="orbis.db",
        description="Path to local SQLite database file or ':memory:'",
    )

    # Local LLM (Ollama) Settings
    ollama_url: str = Field(
        default="http://localhost:11434",
        description="Base URL for local Ollama server",
    )
    ollama_model: str = Field(
        default="llama3.1:8b",
        description="Model name to use with Ollama",
    )
    ollama_timeout: float = Field(
        default=60.0,
        description="Timeout in seconds for Ollama requests",
    )

    # Phase 2: Workspace Observation Settings
    observation_enabled: bool = Field(
        default=True,
        description="Whether passive workspace observation is enabled",
    )
    observation_interval: float = Field(
        default=3.0,
        description="Polling interval in seconds for passive workspace sampling",
    )
    allowed_workspace_root: str = Field(
        default=".",
        description="Base directory path allowed for FileTool operations",
    )

    # Phase 3: Session Segmentation Settings
    session_gap_minutes: float = Field(
        default=15.0,
        description="Inactivity threshold in minutes between consecutive events to demarcate distinct sessions",
    )

    # Phase 4: Knowledge Graph & Semantic Vector Memory Settings
    embedding_model_name: str = Field(
        default="all-MiniLM-L6-v2",
        description="Default local sentence-transformers embedding model",
    )
    vector_index_path: str = Field(
        default="orbis_vectors.faiss",
        description="Default file path for persistent FAISS vector index",
    )
    excluded_processes: List[str] = Field(
        default_factory=lambda: [
            "1password.exe",
            "bitwarden.exe",
            "keepass.exe",
            "lastpass.exe",
        ],
        description="List of process executable names to exclude from observation",
    )
    excluded_window_titles: List[str] = Field(
        default_factory=lambda: [
            "1Password",
            "Bitwarden",
            "KeePass",
            "Sign In",
            "Authentication",
        ],
        description="List of window title keywords to exclude/redact from observation",
    )

    # Phase 7: Web Research & Controlled Terminal Settings
    search_provider: str = Field(
        default="mock",
        description="Search provider type ('mock' or 'configurable')",
    )
    search_api_key: Optional[str] = Field(
        default=None,
        description="Optional API key for external search provider",
    )
    search_endpoint: Optional[str] = Field(
        default=None,
        description="Optional custom endpoint URL for external search provider",
    )
    web_max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Default max search results to return",
    )
    web_request_timeout: float = Field(
        default=5.0,
        ge=1.0,
        le=30.0,
        description="HTTP request timeout in seconds for web research",
    )
    web_max_page_size_bytes: int = Field(
        default=524288,
        ge=1024,
        le=5242880,
        description="Max response size in bytes for web fetches (512KB)",
    )
    web_max_extracted_chars: int = Field(
        default=4000,
        ge=100,
        le=20000,
        description="Max readable text chars extracted per webpage",
    )
    terminal_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        description="Process execution timeout in seconds for terminal commands",
    )
    terminal_max_output_bytes: int = Field(
        default=65536,
        ge=1024,
        le=1048576,
        description="Max output capture size in bytes for terminal commands (64KB)",
    )

    # Phase 8: Content & Document Generation Settings
    document_max_bytes: int = Field(
        default=1048576,
        ge=1024,
        le=10485760,
        description="Max allowed size in bytes for created documents (1MB)",
    )
    content_gen_max_chars: int = Field(
        default=20000,
        ge=100,
        le=100000,
        description="Max character length for generated content",
    )
    document_allowed_extensions: List[str] = Field(
        default_factory=lambda: [".md", ".txt"],
        description="Allowed file extensions for DocumentTool persistence",
    )

    # Phase 9: Intent Continuity Engine (ICE) Settings
    ice_inactivity_threshold_minutes: float = Field(
        default=15.0,
        ge=1.0,
        le=1440.0,
        description="Heuristic temporal inactivity threshold in minutes for ICE active/interrupted state",
    )
    ice_recency_half_life_hours: float = Field(
        default=24.0,
        ge=0.5,
        le=720.0,
        description="Half-life in hours for ICE exponential temporal recency decay",
    )
    ice_abandoned_threshold_hours: float = Field(
        default=72.0,
        ge=1.0,
        le=8760.0,
        description="Inactivity duration threshold in hours to consider stale task abandoned",
    )
    ice_max_intents: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Default max candidate intents to return from ICE",
    )

    # Phase 10: Multi-App & Window Layout Restoration Settings
    restoration_enabled: bool = Field(
        default=True,
        description="Whether workspace restoration capabilities are enabled",
    )
    restoration_require_approval: bool = Field(
        default=True,
        description="Policy requiring user confirmation before multi-app workspace restoration",
    )
    restoration_window_bounds_check: bool = Field(
        default=True,
        description="Enforce virtual desktop display geometry boundary validation",
    )
    restoration_window_detection_timeout_seconds: float = Field(
        default=1.5,
        ge=0.2,
        le=10.0,
        description="Max time to poll for top-level window after process spawn",
    )

    # Phase 11: Voice Interface Settings
    voice_enabled: bool = Field(
        default=True,
        description="Whether Push-to-Talk voice interface subsystem is enabled",
    )
    voice_stt_provider: Literal["mock", "whisper"] = Field(
        default="mock",
        description="Speech-to-Text provider engine ('mock' or 'whisper')",
    )
    voice_tts_provider: Literal["mock", "windows_sapi"] = Field(
        default="mock",
        description="Text-to-Speech provider engine ('mock' or 'windows_sapi')",
    )
    voice_sample_rate: int = Field(
        default=16000,
        description="Audio sampling rate in Hz for microphone capture",
    )
    voice_max_recording_seconds: float = Field(
        default=30.0,
        description="Maximum recording window duration in seconds for Push-to-Talk",
    )
    voice_strip_prefix: bool = Field(
        default=True,
        description="Whether to strip conversational invocation prefixes (e.g. 'ORBIS, ')",
    )
    voice_tts_voice_name: str = Field(
        default="Microsoft David Desktop",
        description="Name of the voice for Text-to-Speech synthesis",
    )


    def is_excluded(
        self, process_name: Optional[str], window_title: Optional[str]
    ) -> bool:
        """
        Determines whether an observed window or process is excluded under privacy policy.
        Matching is case-insensitive.
        """
        if process_name:
            proc_lower = process_name.lower().strip()
            for excluded in self.excluded_processes:
                if proc_lower == excluded.lower().strip():
                    return True

        if window_title:
            title_lower = window_title.lower()
            for keyword in self.excluded_window_titles:
                if keyword.lower() in title_lower:
                    return True

        return False

    def get_resolved_workspace_root(self) -> Path:
        """Returns the canonical, resolved allowed workspace root path."""
        return Path(self.allowed_workspace_root).resolve()


def get_settings(**kwargs) -> Settings:
    """Returns a Settings instance, optionally with keyword overrides."""
    return Settings(**kwargs)
