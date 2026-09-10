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
