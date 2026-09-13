"""Run one HTTP control plane; inference lives in its spawned executor."""
import os
import uvicorn
from voice_coach.backend_analysis.http_api import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, workers=1,
                access_log=False, server_header=False, proxy_headers=False,
                timeout_graceful_shutdown=30, log_level="warning")
