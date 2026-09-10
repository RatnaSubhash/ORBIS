"""
ORBIS Phase 3 Demonstration Script.
Demonstrates deterministic Session Segmentation and Context DNA v1.0 synthesis
using realistic synthetic workspace observations, SQLite persistence, and
end-to-end evidence traceability.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from core.continuity.dna import ContextDNASynthesizer
from core.continuity.sessionizer import SessionSegmenter
from core.memory.db import DatabaseManager
from core.types import WorkspaceEvent, WorkspaceEventType


def run_phase3_demo() -> None:
    print("=" * 60)
    print("ORBIS PHASE 3 DEMONSTRATION")
    print("Session Segmentation + Context DNA Engine")
    print("=" * 60)

    # 1. Generate 8 realistic synthetic workspace events
    base_time = datetime(2026, 9, 9, 9, 0, 0, tzinfo=timezone.utc)

    # Cluster 1 (Session 1): Development & Browser Research (5 events across 10 minutes)
    raw_events = [
        WorkspaceEvent(
            event_id="evt_001",
            timestamp=base_time,
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="main.py - ORBIS - Visual Studio Code",
            resource_path="c:\\Projects\\ORBIS\\main.py",
        ),
        WorkspaceEvent(
            event_id="evt_002",
            timestamp=base_time + timedelta(minutes=2),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="chrome.exe",
            window_title="Python asyncio Documentation - Google Chrome",
            resource_path="https://docs.python.org/3/library/asyncio.html",
        ),
        WorkspaceEvent(
            event_id="evt_003",
            timestamp=base_time + timedelta(minutes=5),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="chrome.exe",
            window_title="SQLite WAL Mode Reference - Google Chrome",
            resource_path="https://sqlite.org/wal.html",
        ),
        WorkspaceEvent(
            event_id="evt_004",
            timestamp=base_time + timedelta(minutes=7),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="db.py - ORBIS - Visual Studio Code",
            resource_path="c:\\Projects\\ORBIS\\core\\memory\\db.py",
        ),
        WorkspaceEvent(
            event_id="evt_005",
            timestamp=base_time + timedelta(minutes=10),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="test_db.py - ORBIS - Visual Studio Code",
            resource_path="c:\\Projects\\ORBIS\\tests\\test_db.py",
        ),
        # --- LARGE INACTIVITY GAP: 45 minutes of inactivity (> 15 min threshold) ---
        # Cluster 2 (Session 2): Terminal & Verification (3 events across 5 minutes)
        WorkspaceEvent(
            event_id="evt_006",
            timestamp=base_time + timedelta(minutes=55),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="PowerShell - pytest",
            resource_path="c:\\Projects\\ORBIS",
        ),
        WorkspaceEvent(
            event_id="evt_007",
            timestamp=base_time + timedelta(minutes=57),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="walkthrough.md - Visual Studio Code",
            resource_path="c:\\Projects\\ORBIS\\walkthrough.md",
        ),
        WorkspaceEvent(
            event_id="evt_008",
            timestamp=base_time + timedelta(minutes=60),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="PowerShell - git status",
            resource_path="c:\\Projects\\ORBIS",
        ),
    ]

    print(f"\nTotal Input Workspace Events: {len(raw_events)}")
    for i, e in enumerate(raw_events, 1):
        rel_time = (e.timestamp - base_time).total_seconds() / 60.0
        print(f"  [{i}] +{rel_time:4.1f}m | {e.process_name:18} | {e.event_type.value:21} | {e.resource_path}")

    # 2. Setup SQLite persistence database
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "demo_phase3.db")
        db = DatabaseManager(db_path=db_path)
        db.initialize()

        # Persist raw workspace events for evidence traceability
        for e in raw_events:
            db.record_workspace_event(e)

        # 3. Perform Session Segmentation
        segmenter = SessionSegmenter(session_gap_seconds=900.0)  # 15 minutes threshold
        sessions = segmenter.segment(raw_events)

        print(f"\nDetected Sessions Count: {len(sessions)}")
        print("-" * 60)

        synthesizer = ContextDNASynthesizer()

        for idx, session in enumerate(sessions, 1):
            # 4. Generate Context DNA
            dna = synthesizer.synthesize(session=session, events=raw_events)

            # 5. Persist Session & Context DNA to SQLite
            db.record_session(session)
            db.record_context_dna(dna)

            # 6. Retrieve persisted records to verify roundtrip fidelity
            retrieved_session = db.get_session(session.session_id)
            retrieved_dna = db.get_context_dna(session.session_id)
            traced_events = db.get_session_events(session.session_id)

            assert retrieved_session is not None
            assert retrieved_dna is not None
            assert len(traced_events) == session.event_count

            # 7. Print Session and Context DNA details
            print(f"\nSession {idx} [{session.session_id[:8]}...]")
            print(f"  Start:     {session.start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"  End:       {session.end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"  Duration:  {session.duration_seconds / 60.0:.1f} minutes ({session.duration_seconds:.0f}s)")
            print(f"  Events:    {session.event_count}")
            print(f"  Confidence:{session.confidence:.2f}")
            print(f"  Applications:")
            for app in session.applications:
                print(f"    - {app}")

            print(f"\n  Context DNA v{dna.context_dna_version} [{dna.dna_id[:8]}...]")
            print(f"    Primary App:     {dna.application_context.get('primary_application')}")
            print(f"    App Switches:    {dna.application_context.get('transition_count')}")
            print(f"    Observed Paths:  {len(dna.resource_context.get('observed_resource_paths', []))}")
            print(f"    File Extensions: {dna.resource_context.get('resource_extensions')}")
            print(f"    Event Types:     {dna.activity_context.get('event_types')}")
            print(f"    Privacy Status:  Exclusions Active={dna.privacy_context.get('privacy_exclusions_active')}, Sensitive={dna.privacy_context.get('contains_sensitive_content')}")
            print(f"    Data Quality:    {dna.quality_indicators.get('data_quality')} (density: {dna.quality_indicators.get('event_density_per_minute')} events/min)")
            print(f"    Source Events:   {retrieved_dna.source_event_ids}")
            print(f"    DB Traceability: Verified {len(traced_events)} linked WorkspaceEvent rows in SQLite.")

        db.close()

    print("\n" + "=" * 60)
    print("Session Segmentation Completed")
    print("Context DNA Generated")
    print("Traceability Verified")
    print("=" * 60)


if __name__ == "__main__":
    run_phase3_demo()
