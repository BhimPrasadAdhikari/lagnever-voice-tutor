"""Run the Lagnever voice tutor on your own machine.

    cp .env.example .env      # add your keys
    python setup_agent.py     # once: creates the voice agent
    python app.py             # then open http://localhost:8766 and click "Start voice tutor"

Without Vocal Bridge keys the page still works in text mode (type as the student).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import uvicorn  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

import tutor  # noqa: E402
import widget  # noqa: E402

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lagnever voice tutor</title>
<style>body{margin:0;padding:24px;background:#fbfaf6;font-family:system-ui,sans-serif}</style>
</head><body>__WIDGET__</body></html>"""


def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY in .env first (copy .env.example to .env).")
    port = int(os.getenv("LAGNEVER_PORT", "8766"))
    profile = tutor.JEE_STUDENT if os.getenv("LAGNEVER_PROFILE", "jee") == "jee" else tutor.DEFAULT_STUDENT
    tutor.new_session(profile=profile)
    app = tutor.build_app()
    agent_id = os.getenv("VOCAL_BRIDGE_AGENT_ID_LAGNEVER")
    voice = bool(agent_id and os.getenv("VOCAL_BRIDGE_API_KEY"))

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        # a fresh voice token per page load (tokens expire after a few minutes)
        token = tutor.mint_token(agent_id) if voice else None
        return PAGE.replace("__WIDGET__", widget.notebook_widget(f"http://localhost:{port}", token))

    print(f"Lagnever voice tutor on http://localhost:{port}  "
          f"({'voice' if voice else 'text-only: add Vocal Bridge keys for voice'}, model {tutor.MODEL})")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
