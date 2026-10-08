import asyncio
import sys
import os
sys.path.insert(0, os.path.abspath('.'))
from core.memory.db import DatabaseManager
from core.restoration.engine import RestorationEngine

async def main():
    db = DatabaseManager('orbis.db')
    engine = RestorationEngine(db=db)
    
    # 1. Test oldest session (from Sept 19)
    old_sessions = db.get_sessions(limit=5)
    print("Oldest session:", old_sessions[0].session_id, old_sessions[0].start_time, old_sessions[0].applications)
    
    plan, ice, msg = await engine.create_restoration_plan(session_id=old_sessions[0].session_id)
    print(f"Plan for old session: {plan.plan_id if plan else None}, msg: {msg}")
    if plan:
        print("Steps:")
        for s in plan.steps:
            print(f"  step_id={s.step_id}, app={s.app_identifier}, status={s.status}, is_valid={s.is_valid}, msg={s.status_message}")
            
    # 2. Test newest session (from today Sept 24)
    with db.get_connection() as conn:
        row = conn.execute("SELECT session_id, start_time, applications_json FROM sessions ORDER BY start_time DESC LIMIT 1").fetchone()
        newest_id = row['session_id']
        print("\nNewest session:", newest_id, row['start_time'], row['applications_json'])
        
    plan2, ice2, msg2 = await engine.create_restoration_plan(session_id=newest_id)
    print(f"Plan for newest session: {plan2.plan_id if plan2 else None}, msg: {msg2}")
    if plan2:
        print("Steps:")
        for s in plan2.steps:
            print(f"  step_id={s.step_id}, app={s.app_identifier}, status={s.status}, is_valid={s.is_valid}, msg={s.status_message}")

asyncio.run(main())
