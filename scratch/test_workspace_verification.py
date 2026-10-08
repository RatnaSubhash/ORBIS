import requests
import json

def test_continuity_and_restoration():
    base_url = "http://127.0.0.1:8000"
    
    # 1. Test continuity context
    res = requests.get(f"{base_url}/api/continuity/context")
    assert res.status_code == 200, f"Continuity error: {res.text}"
    data = res.json()
    print("=== CONTINUITY TELEMETRY ===")
    print("Observation active:", data.get("observation", {}).get("active"))
    print("Total events:", data.get("observation", {}).get("total_events"))
    print("Recent sessions count:", len(data.get("recent_sessions", [])))
    
    recent_sessions = data.get("recent_sessions", [])
    if recent_sessions:
        first = recent_sessions[0]
        print("First session ID:", first.get("session_id"))
        print("First session start time:", first.get("start_time"))
        print("First session apps:", first.get("applications"))
        
        # 2. Test preview restoration plan
        plan_res = requests.post(
            f"{base_url}/api/restoration/preview",
            json={"session_id": first.get("session_id")}
        )
        print("Preview plan status:", plan_res.status_code)
        plan_data = plan_res.json()
        plan = plan_data.get("plan")
        if plan:
            print("Plan goal:", plan.get("goal_description"))
            print("Plan steps count:", len(plan.get("steps", [])))
            for s in plan.get("steps", []):
                print(f"  Step ID: {s.get('step_id')}, App: {s.get('app_identifier')}, Status: {s.get('status')}")
            
            step_ids = [s["step_id"] for s in plan.get("steps", []) if s.get("status") != "SKIPPED_INVALID"]
            print("Selected step IDs for execution test:", step_ids)
            
            # 3. Test execution endpoint (simulate with approved=True)
            if step_ids:
                exec_res = requests.post(
                    f"{base_url}/api/restoration/execute",
                    json={
                        "session_id": first.get("session_id"),
                        "selected_step_ids": step_ids,
                        "approved": True
                    }
                )
                print("Execute status:", exec_res.status_code)
                exec_data = exec_res.json()
                print("Execution summary:", exec_data.get("summary"))
                print("Completed steps:", exec_data.get("completed_steps"))
                print("Failed steps:", exec_data.get("failed_steps"))
                print("Skipped steps:", exec_data.get("skipped_steps"))
                print("Step results:", json.dumps(exec_data.get("step_results", {}), indent=2))
        else:
            print("No plan:", plan_data)

if __name__ == "__main__":
    test_continuity_and_restoration()
