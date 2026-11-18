"""Create or update the Vocal Bridge voice agent for the Lagnever tutor.

    python setup_agent.py                 # create a new agent (paid Vocal Bridge plan)
    python setup_agent.py --agent-id ID   # configure an agent you already have

Applies vocal_bridge/prompt.md, vocal_bridge/ai-agent.json and
vocal_bridge/model-settings.json, then saves the agent id to .env.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
CFG = HERE / "vocal_bridge"
ENV = HERE / ".env"
GREETING = "Hi! I'm your Lagnever tutor. Tell me the question you're stuck on, or ask me for a practice question."


def vb(*args: str) -> str:
    proc = subprocess.run(["vb", *args], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        sys.exit(f"vb {' '.join(args)} failed:\n{proc.stderr or proc.stdout}")
    return proc.stdout


def save_env(key: str, value: str) -> None:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    ENV.write_text("\n".join(lines) + "\n")


def main() -> None:
    load_dotenv(ENV)
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-id", default=os.getenv("VOCAL_BRIDGE_AGENT_ID_LAGNEVER") or None)
    ap.add_argument("--name", default="Lagnever Tutor")
    a = ap.parse_args()

    key = os.getenv("VOCAL_BRIDGE_API_KEY")
    if not key:
        sys.exit("Set VOCAL_BRIDGE_API_KEY in .env first (copy .env.example to .env).")
    vb("auth", "login", key)

    common = ["--deploy-targets", "web", "--background-enabled", "false", "--web-search-enabled", "false"]
    if a.agent_id:
        print(f"Configuring existing agent {a.agent_id} ...")
        vb("agent", "use", a.agent_id)
        vb("prompt", "set", "-f", str(CFG / "prompt.md"))
        vb("config", "set", "--greeting", GREETING, *common,
           "--ai-agent-file", str(CFG / "ai-agent.json"),
           "--model-settings-file", str(CFG / "model-settings.json"), "--merge")
        agent_id = a.agent_id
    else:
        print("Creating a new Vocal Bridge agent ...")
        out = vb("agent", "create", "--name", a.name, "--style", "Chatty",
                 "--prompt-file", str(CFG / "prompt.md"), "--greeting", GREETING, *common,
                 "--ai-agent-file", str(CFG / "ai-agent.json"),
                 "--model-settings-file", str(CFG / "model-settings.json"))
        m = re.search(r"ID:\s*([0-9a-fA-F-]{8,})", out)
        if not m:
            sys.exit("Agent created but its id was not found in the output:\n" + out)
        agent_id = m.group(1)

    save_env("VOCAL_BRIDGE_AGENT_ID_LAGNEVER", agent_id)
    print(f"Done. Agent {agent_id} is ready and saved to .env. Now run: python app.py")


if __name__ == "__main__":
    main()
