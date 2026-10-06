"""Standalone anonymous API process; never starts the old Backend HTTP lane."""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("voice_coach.direct_analysis.api:create_app", factory=True,
                host=os.environ.get("AI_DIRECT_BIND_HOST", "127.0.0.1"),
                port=int(os.environ.get("AI_DIRECT_PORT", "8095")), workers=1,
                access_log=False, limit_concurrency=32)
