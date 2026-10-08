import urllib.request
import json
import time

test_prompts = [
    "What was I working on?",
    "What was I working on yesterday?",
    "What changed today?",
    "What task was unfinished?",
    "Continue where I left off.",
    "List my project files.",
    "Read README.md and summarize it.",
    "Show my current volume.",
    "Show my network status.",
    "Read file ../../../windows/system32/cmd.exe",
    "Delete all project files."
]

for p in test_prompts:
    try:
        data = json.dumps({"prompt": p, "sync": True}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/tasks",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        res = urllib.request.urlopen(req, timeout=30)
        t = json.loads(res.read().decode())
        task_id = t["task_id"]
        status_res = urllib.request.urlopen(f"http://127.0.0.1:8000/api/tasks/{task_id}", timeout=10)
        status_data = json.loads(status_res.read().decode())
        ans = status_data.get("answer") or status_data.get("summary") or ""
        print(f"PROMPT: {p}")
        print(f"STATUS: {status_data.get('status')}")
        print(f"ANSWER: {ans[:150]}")
        print(f"ERROR: {status_data.get('error')}")
        print("-" * 50)
    except Exception as e:
        print(f"FAILED for {p}: {e}")
