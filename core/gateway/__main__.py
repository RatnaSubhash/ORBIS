"""
Entry point for running the ORBIS backend gateway via `python -m core.gateway`.
"""

import sys
import uvicorn

if __name__ == "__main__":
    reload = "--reload" in sys.argv
    uvicorn.run("core.gateway.app:create_app", factory=True, host="127.0.0.1", port=8000, reload=reload)
