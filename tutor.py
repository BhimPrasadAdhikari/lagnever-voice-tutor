"""Lagnever voice tutor - prototype (v2: speech-first streaming + sympy checker).

Architecture
    student mic -> Vocal Bridge (STT + realtime voice, "ears and mouth")
                -> query_agent -> POST /query (this file) -> LLM tutor brain
                <- {"response": speech}  as soon as the speech is ready
                   page ops + memory finish in the background; the widget
                   polls /state (version) and redraws the notebook page.

Each turn the tutor brain streams one tutor_turn tool call in this order:
    step_check (verdict + a machine-checkable spec) -> speech -> board -> memory
step_check is re-checked by sympy (checker.py) before any speech is released.
If sympy disagrees, the draft is dropped and the tutor brain redoes the turn with the
checker's result, so a right step is never struck and a wrong one never ticked.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import anthropic
import requests
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import checker

HERE = Path(__file__).resolve().parent
MEMORY_FILE = HERE / "student_memory.json"
MODEL = os.getenv("LAGNEVER_TUTOR_MODEL", "claude-sonnet-4-6")
VB_API_URL = "https://vocalbridgeai.com"
# Stream tool-input chunks as they are generated (not buffered per key), so the
# speech can be released before the page ops are written. Set to {} to disable.
STREAM_HEADERS = {"anthropic-beta": "fine-grained-tool-streaming-2025-05-14"}

# --------------------------------------------------------------------------
# Student memory (stand-in for Lagnever's learner memory + tricky spots)
# --------------------------------------------------------------------------
DEFAULT_STUDENT: dict[str, Any] = {
    "name": "Asha",
    "class": 9,
    "board": "CBSE",
    "language": "English",
    "tricky_spots": [
        {
            "id": "ts-sign-move",
            "topic": "Linear equations",
            "slip": "When moving a number across the equals sign she forgets to flip its sign (last week x − 3 = 7 became x = 7 − 3 instead of x = 7 + 3).",
            "right_in_a_row": 0,
        }
    ],
    "cleared": [],
}

JEE_STUDENT: dict[str, Any] = {
    "name": "Asha",
    "class": 12,
    "board": "CBSE, preparing for JEE Main",
    "language": "English",
    "tricky_spots": [
        {"id": "ts-mod-roots", "topic": "Modulus equations",
         "slip": "After solving for |x|, she forgets the negative value of x (|x| = 2 gives x = ±2, she writes only x = 2).",
         "right_in_a_row": 0},
        {"id": "ts-limit-std", "topic": "Limits",
         "slip": "Mixes up the standard limits: treats sin x / (2x) as 1 instead of 1/2.", "right_in_a_row": 1},
    ],
    "cleared": [],
}


def load_memory() -> dict[str, Any]:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return copy.deepcopy(DEFAULT_STUDENT)


def save_memory(mem: dict[str, Any]) -> None:
    MEMORY_FILE.write_text(json.dumps(mem, indent=2, ensure_ascii=False))


def reset_memory(profile: dict[str, Any] | None = None) -> None:
    save_memory(copy.deepcopy(profile or DEFAULT_STUDENT))


def apply_memory_update(mem: dict[str, Any], upd: dict[str, Any] | None) -> list[str]:
    """Tricky-spot rules from Lagnever: clears after three right in a row."""
    events: list[str] = []
    if not upd:
        return events
    spots = mem.setdefault("tricky_spots", [])
    by_id = {s["id"]: s for s in spots}
    sid = upd.get("got_right_tricky_spot_id")
    if sid in by_id:
        by_id[sid]["right_in_a_row"] += 1
        events.append(f"right on {sid} ({by_id[sid]['right_in_a_row']}/3)")
        if by_id[sid]["right_in_a_row"] >= 3:
            spots.remove(by_id[sid])
            mem.setdefault("cleared", []).append(by_id[sid])
            events.append(f"cleared {sid}")
    sid = upd.get("slipped_again_tricky_spot_id")
    if sid in by_id:
        by_id[sid]["right_in_a_row"] = 0
        events.append(f"slipped again on {sid}")
    new = upd.get("new_tricky_spot")
    if new and new.get("slip"):
        nid = "ts-" + uuid.uuid4().hex[:6]
        spots.append({"id": nid, "topic": new.get("topic", ""), "slip": new["slip"], "right_in_a_row": 0})
        events.append(f"new tricky spot {nid}: {new['slip']}")
    return events


# --------------------------------------------------------------------------
# The notebook page (server is the source of truth; the widget only draws it)
# --------------------------------------------------------------------------
def new_page() -> dict[str, Any]:
    return {"question": None, "approach": None, "lines": [], "final": None,
            "final_ticked": False, "notes": []}


def apply_ops(page: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    for op in ops or []:
        kind = op.get("op")
        text = (op.get("text") or "").strip()
        step = op.get("step")
        line = page["lines"][step - 1] if isinstance(step, int) and 1 <= step <= len(page["lines"]) else None
        if line is None and kind in ("strike", "tick", "margin_note", "highlight") and page["lines"] \
                and not (kind == "tick" and page["final"] and step is None and page["lines"][-1]["ticked"]):
            line = page["lines"][-1]  # no/invalid step -> the line just written
        if kind == "clear":
            page = new_page()
        elif kind == "new_question":
            page = new_page()
            page["question"] = text
        elif kind == "approach":
            page["approach"] = text
        elif kind in ("student_step", "tutor_step"):
            page["lines"].append({"n": len(page["lines"]) + 1,
                                  "who": "student" if kind == "student_step" else "tutor",
                                  "text": text, "struck": False, "correction": None,
                                  "ticked": False, "highlight": False, "notes": []})
        elif kind == "strike" and line and not line["struck"]:
            line["struck"] = True
            line["ticked"] = False
            line["correction"] = (op.get("correction") or text) or None
        elif kind == "tick":
            if line and not line["struck"]:
                line["ticked"] = True
            elif page["final"]:
                page["final_ticked"] = True
        elif kind in ("margin_note", "recall"):
            note = {"kind": "recall" if kind == "recall" else "note", "text": text}
            (line["notes"] if line else page["notes"]).append(note)
        elif kind == "highlight":
            if line:
                line["highlight"] = True
            elif text:
                page["notes"].append({"kind": "highlight", "text": text})
        elif kind == "final_answer":
            page["final"] = text
            page["final_ticked"] = True  # a final answer is only written once it's right
    return page


def page_as_text(page: dict[str, Any]) -> str:
    if not page["question"] and not page["lines"]:
        return "(empty page - no question yet)"
    out = [f"Question: {page['question']}"]
    if page["approach"]:
        out.append(f"How to think about it: {page['approach']}")
    for ln in page["lines"]:
        flags = []
        if ln["ticked"]:
            flags.append("ticked")
        if ln["struck"]:
            flags.append(f"struck, correction: {ln['correction']}")
        for n in ln["notes"]:
            flags.append(f"{n['kind']}: {n['text']}")
        out.append(f"{ln['n']}. [{ln['who']}] {ln['text']}" + (f"  ({'; '.join(flags)})" if flags else ""))
    for n in page["notes"]:
        out.append(f"page {n['kind']}: {n['text']}")
    if page["final"]:
        out.append(f"Final answer: {page['final']}" + (" (ticked)" if page["final_ticked"] else ""))
    return "\n".join(out)


# --------------------------------------------------------------------------
# LLM tutor brain
# --------------------------------------------------------------------------
TUTOR_SYSTEM = """You are Lagnever, a personal AI tutor for Indian school students (classes 6-12, CBSE and state boards, JEE and NEET aspirants), talking to one student by VOICE while writing on their notebook page. You coach; you don't just answer. Your line: a tutor that knows where you get stuck, teaches you how to think about it, and stays until you get it.

HOW YOU TEACH
- New doubt with steps (maths, physics numericals, chemistry...): write it on the page with new_question, write a one-line approach, say how to think about it in one sentence, then ask the student to try the FIRST step themselves. Do not solve it for them.
- When the student says a step or an answer: write it as a student_step (their words, tidied into notation). If it is right, tick it, praise the specific move, and ask for the next step. If it is wrong, strike it with the correct line as the correction, explain the slip kindly in one sentence and ask them to redo it or move on - only give the step away after they miss it twice. If they used a different valid method from your approach, accept it and continue with their method.
- "I don't know", silence, or "help": use a hint ladder. First a nudge question, then a more specific hint, then write that one step as a tutor_step and ask them to do the next one.
- When it is solved: write final_answer, do a quick check out loud, and name the move they learnt in one sentence.
- Use their tricky spots. If the coming step is exactly where they slipped before, add a recall note (op recall, no step) in the SAME turn where you ask them for that step, before they attempt it, and say "this is the step that caught you last time". Set step_check.tricky_spot_id only when this turn's step used that spot's exact move. If the slip matches an existing tricky spot, put that id in step_check.tricky_spot_id and never create a duplicate. Set memory.new_tricky_spot only for a genuinely new kind of conceptual slip in THIS turn (one short line). Never invent progress they didn't make.
- Short factual questions (a definition, a formula, a date) get a short direct answer; no step-by-step.
- If they ask for practice, give one question at their level, ideally on a tricky spot, and write it with new_question.
- Off-topic chat: answer in one friendly line and bring them back to studies.

STEP CHECK (fill it FIRST, before speech)
- verdict: no_step | correct | wrong, about the step or answer the student gave THIS turn.
- verify: a spec a symbolic maths engine (sympy) can re-check. Use sympy syntax: 3*x - 5 = 16, Abs(x), x**2, sin(x)/(2*x), sqrt(2), pi, exp(x).
  equation  {kind, reference, student, var}: the student's new equation vs the last correct equation (or the question). Same real solutions = correct.
  solutions {kind, reference, student_values, var}: the student's list of roots vs the question's equation.
  count     {kind, reference, student_count, var}: "how many real solutions".
  limit     {kind, expr, var, point, student_value}: the student claims this limit equals student_value.
  value     {kind, expr, student_value}: arithmetic/simplification, e.g. expr "16+5", value "21".
  none      {kind}: when not machine-checkable (words, concepts, physics reasoning, no_step).
- The engine is final. If you are told it disagreed with you, follow it.

SPEECH (it is read aloud by a voice, so write for the ear)
- 1 to 3 short, warm, complete sentences, at most about 45 words. Usually end with exactly one question for the student.
- Say maths in words: "two x minus three equals seven", "x squared", "mod x", "sine x by x", "limit as x tends to zero". Never symbols like |x|, ², ÷, =, LaTeX, markdown, bullet points or emoji in speech.
- Mirror the student's language: English, Hindi (Devanagari) or Hinglish. Keep school words like doubt, chapter, step in English.
- Calm and kind, a little proud of them, never hype. They are a teenager.

PAGE (what you write on their notebook)
- Short lines in plain notation with Unicode: −, ×, ÷, ², √, →, =, ±, lim.
- Refer to existing lines by their number from [Current page]. Never rewrite a line that is already there.
- Notes are tutor's red-pen margin notes: short, handwritten-feel, under 10 words. Never write tricky-spot ids (like ts-...) on the page or say them aloud.

SAFETY
- The student is a minor. Stay with study and wellbeing, don't ask for personal details. If something worrying comes up, respond kindly and suggest they talk to a parent or teacher.

Always respond by calling the tutor_turn tool exactly once, filling fields in this order: step_check, speech, board, memory."""

TUTOR_TOOL = {
    "name": "tutor_turn",
    "description": "Everything you do this turn. Fill fields in order: step_check, speech, board, memory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "step_check": {
                "type": "object",
                "description": "Verdict on the step/answer the student gave THIS turn, plus a sympy-checkable spec.",
                "properties": {
                    "verdict": {"type": "string", "enum": ["no_step", "correct", "wrong"]},
                    "verify": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["equation", "solutions", "count", "limit", "value", "none"]},
                            "reference": {"type": "string"}, "student": {"type": "string"},
                            "student_values": {"type": "array", "items": {"type": "string"}},
                            "student_count": {"type": "integer"},
                            "expr": {"type": "string"}, "point": {"type": "string"},
                            "student_value": {"type": "string"}, "var": {"type": "string"},
                        },
                        "required": ["kind"],
                    },
                    "tricky_spot_id": {"type": "string", "description": "id of a tricky spot whose exact move this step used, else empty."},
                },
                "required": ["verdict", "verify"],
            },
            "speech": {"type": "string", "description": "What you say aloud. Spoken-style, 1-3 sentences, no symbols."},
            "board": {
                "type": "array",
                "description": "Ordered page operations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["new_question", "approach", "student_step", "tutor_step", "strike", "tick", "margin_note", "recall", "highlight", "final_answer", "clear"]},
                        "text": {"type": "string"},
                        "step": {"type": "integer", "description": "Page line number to target. OMIT it to target the line you just wrote (the last line) - the normal case for tick/strike right after a student_step."},
                        "correction": {"type": "string", "description": "For strike: the correct line, written in red pen."},
                    },
                    "required": ["op"],
                },
            },
            "memory": {
                "type": "object",
                "properties": {
                    "new_tricky_spot": {"type": "object", "properties": {"topic": {"type": "string"}, "slip": {"type": "string"}}},
                },
            },
        },
        "required": ["step_check", "speech", "board"],
    },
}

FILLERS = {"i have a", "i have a doubt", "i have a g doubt", "so", "umm", "um", "hmm", "okay so", "wait",
           "i have a question", "next doubt", "ok", "okay"}


def is_fragment(text: str) -> bool:
    t = text.lower().strip(" .,!?")
    words = t.split()
    if t in FILLERS:
        return True
    # clearly cut off mid-sentence: "I have a g", "the value of"
    return bool(words) and (words[-1] in {"a", "an", "the", "of", "to", "is", "my", "and", "so"}
                            or (t.startswith("i have a") and len(words) <= 4))


def _complete(snap: dict[str, Any], key: str, ended: bool) -> bool:
    """A streamed key is complete once a later key has started (or the stream ended)."""
    if key not in snap:
        return False
    if ended:
        return True
    keys = list(snap.keys())
    return keys.index(key) < len(keys) - 1


class TutorSession:
    """One voice tutoring session (one student, one call)."""

    def __init__(self, model: str = MODEL, max_turns: int = 14):
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_turns = max_turns
        self.history: list[dict[str, Any]] = []
        self.page = new_page()
        self.version = 0
        self.memory = load_memory()
        self.credited: set[str] = set()   # tricky spots credited on the current question
        self.pending_fragment = ""
        self.log: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    # ---- prompt ----
    def _context(self) -> str:
        m = self.memory
        spots = "\n".join(f"- id={s['id']} [{s['topic']}] {s['slip']} (right in a row: {s['right_in_a_row']}/3)"
                          for s in m.get("tricky_spots", [])) or "- none"
        return (f"[Student] {m['name']}, class {m['class']}, {m['board']}, prefers {m['language']}.\n"
                f"[Tricky spots]\n{spots}\n[Current page]\n{page_as_text(self.page)}\n"
                f"(The next line you add will be line {len(self.page['lines']) + 1}.)")

    def _stream(self, messages: list[dict[str, Any]], on_check, on_speech, headers: dict | None = None) -> dict[str, Any] | None:
        """Stream one tutor_turn. on_check(step_check)->bool (False = abort draft);
        on_speech(text) fires once when the speech field is complete."""
        checked = spoke = False
        snap: dict[str, Any] = {}
        with self.client.messages.stream(
            model=self.model, max_tokens=900,
            system=[{"type": "text", "text": TUTOR_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[TUTOR_TOOL], tool_choice={"type": "tool", "name": "tutor_turn"},
            messages=messages, extra_headers=(STREAM_HEADERS if headers is None else headers) or None,
        ) as stream:
            for ev in stream:
                if getattr(ev, "type", "") != "input_json":
                    continue
                snap = ev.snapshot if isinstance(ev.snapshot, dict) else snap
                if not checked and _complete(snap, "step_check", False):
                    checked = True
                    if not on_check(snap.get("step_check") or {}):
                        return None  # draft rejected by the checker
                if checked and not spoke and _complete(snap, "speech", False):
                    spoke = True
                    on_speech(snap.get("speech") or "")
            final = stream.get_final_message()
        out = next((b.input for b in final.content if getattr(b, "type", "") == "tool_use"), None) or snap
        if not checked:
            if not on_check(out.get("step_check") or {}):
                return None
        if not spoke:
            on_speech(out.get("speech") or "")
        return out

    def turn(self, student_said: str, wait_for_page: bool = False) -> dict[str, Any]:
        # Fast path: a mid-sentence fragment gets an instant listening cue and is
        # merged into the next turn (slow fragment calls made VB answer by itself).
        if is_fragment(student_said):
            self.pending_fragment = (self.pending_fragment + " " + student_said).strip()
            return {"response": "Go on, I'm listening.", "page": self.page, "version": self.version,
                    "pending": False, "tricky_spots": self.memory.get("tricky_spots", []),
                    "memory_events": [], "latency_ms": 0, "checker": None}
        if self.pending_fragment:
            student_said = f"{self.pending_fragment} ... {student_said}"
            self.pending_fragment = ""

        self.lock.acquire()  # released by the worker when the page is updated
        t0 = time.time()
        speech_ready = threading.Event()
        rec: dict[str, Any] = {"student": student_said, "speech": None, "checker": None,
                               "override": False, "error": None}
        user_msg = f"{self._context()}\n\n[Student just said]\n{student_said}"
        base = self.history[-2 * self.max_turns:] + [{"role": "user", "content": user_msg}]

        def on_speech(text: str) -> None:
            if not speech_ready.is_set():
                rec["speech"] = text.strip() or "Sorry, could you say that again?"
                rec["speech_ms"] = int((time.time() - t0) * 1000)
                speech_ready.set()

        def first_check(sc: dict[str, Any]) -> bool:
            res = checker.check(sc.get("verify"))
            rec["checker"] = res
            verdict = sc.get("verdict")
            if res["status"] in ("correct", "wrong") and verdict in ("correct", "wrong") and res["status"] != verdict:
                rec["override"] = True
                rec["model_verdict"] = verdict
                return False
            return True

        def worker() -> None:
            try:
                out = self._stream(base, first_check, on_speech)
                if out is None:  # checker disagreed -> redo with the engine's result
                    res = rec["checker"]
                    note = (f"\n\n[Maths checker] sympy says the student's step is {res['status'].upper()} "
                            f"({res.get('why', '')}). Your draft said {rec['model_verdict']}. Redo this turn so "
                            f"your verdict, speech and page marks agree with the checker.")
                    msgs = base[:-1] + [{"role": "user", "content": user_msg + note}]
                    out = self._stream(msgs, lambda sc: True, on_speech)
                    out.setdefault("step_check", {})["verdict"] = res["status"]
                self._finish(out, rec, t0)
            except Exception as exc:
                if speech_ready.is_set() and rec.get("speech"):
                    # Fine-grained streaming can end with malformed JSON for the page ops.
                    # The speech is already out, so redo just the page in buffered mode.
                    try:
                        note = (f'\n\n[You already said aloud: "{rec["speech"]}". Keep exactly that speech '
                                f'and write the matching step_check, board and memory.]')
                        out = self._stream(base[:-1] + [{"role": "user", "content": user_msg + note}],
                                           lambda sc: True, lambda t: None, headers={})
                        self._finish(out, rec, t0)
                        rec["recovered_from"] = str(exc)
                    except Exception as exc2:
                        rec["error"] = f"{exc} / {exc2}"
                else:
                    rec["error"] = str(exc)
                    on_speech("Sorry, I lost my train of thought for a second. Could you say that again?")
            finally:
                rec["total_ms"] = int((time.time() - t0) * 1000)
                self.log.append(rec)
                self.lock.release()

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        speech_ready.wait(timeout=30)
        if wait_for_page:
            th.join(timeout=30)
        return {"response": rec["speech"] or "One moment, I'm still thinking.",
                "page": self.page, "version": self.version, "pending": th.is_alive(),
                "tricky_spots": self.memory.get("tricky_spots", []),
                "memory_events": rec.get("memory_events", []),
                "latency_ms": rec.get("speech_ms"), "checker": rec["checker"],
                "override": rec["override"], "error": rec["error"]}

    def _finish(self, out: dict[str, Any], rec: dict[str, Any], t0: float) -> None:
        ops = out.get("board") or []
        if any(o.get("op") in ("new_question", "clear") for o in ops):
            self.credited = set()
        self.page = apply_ops(self.page, ops)
        sc = out.get("step_check") or {}
        upd = dict(out.get("memory") or {})
        sid, verdict = sc.get("tricky_spot_id"), sc.get("verdict")
        if sid:  # the slip is already a known tricky spot -> never add a duplicate
            upd.pop("new_tricky_spot", None)
        if sid and verdict == "correct" and sid not in self.credited:
            upd["got_right_tricky_spot_id"] = sid
            self.credited.add(sid)          # one credit per question, not per line
        elif sid and verdict == "wrong":
            upd["slipped_again_tricky_spot_id"] = sid
            self.credited.discard(sid)
        rec["memory_events"] = apply_memory_update(self.memory, upd)
        save_memory(self.memory)
        rec.update(ops=ops, verdict=verdict, page_ms=int((time.time() - t0) * 1000))
        self.history += [
            {"role": "user", "content": f"Student: {rec['student']}"},
            {"role": "assistant", "content": f"Tutor said: {rec['speech']}\nTutor wrote: {json.dumps(ops, ensure_ascii=False)}"},
        ]
        self.version += 1

    def state(self) -> dict[str, Any]:
        last = self.log[-1] if self.log else {}
        return {"page": self.page, "version": self.version, "tricky_spots": self.memory.get("tricky_spots", []),
                "turns": len(self.log), "last": {k: last.get(k) for k in
                                                 ("speech_ms", "page_ms", "checker", "override", "memory_events")}}


SESSION: TutorSession | None = None


def get_session() -> TutorSession:
    global SESSION
    if SESSION is None:
        SESSION = TutorSession()
    return SESSION


def new_session(reset_student: bool = False, profile: dict[str, Any] | None = None) -> TutorSession:
    global SESSION
    if reset_student or profile:
        reset_memory(profile)
    SESSION = TutorSession()
    return SESSION


# --------------------------------------------------------------------------
# HTTP layer (in Lagnever this is one more router in backend_fastapi)
# --------------------------------------------------------------------------
class QueryIn(BaseModel):
    query: str
    turn_id: str | None = None


_server: dict[str, Any] = {"server": None, "thread": None}


def build_app() -> FastAPI:
    app = FastAPI(title="Lagnever voice tutor")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.post("/query")
    def query(req: QueryIn) -> dict[str, Any]:
        res = get_session().turn(req.query)
        res["turn_id"] = req.turn_id
        return res

    @app.get("/state")
    def state() -> dict[str, Any]:
        return get_session().state()

    @app.post("/reset")
    def reset() -> dict[str, Any]:
        return new_session().state()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def start_server(port: int = 8766) -> str:
    stop_server()
    try:  # also stop servers left over from an earlier import of this module
        import gc
        for s in [o for o in gc.get_objects() if isinstance(o, uvicorn.Server)]:
            s.should_exit = True
        time.sleep(1.5)
    except Exception:
        pass
    server = uvicorn.Server(uvicorn.Config(build_app(), host="0.0.0.0", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://localhost:{port}"
    for _ in range(50):
        try:
            requests.get(url + "/health", timeout=0.5).raise_for_status()
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("tutor server did not start")
    _server.update(server=server, thread=thread)
    return url


def stop_server() -> None:
    if _server["server"] is not None:
        _server["server"].should_exit = True
        _server["thread"].join(timeout=3)
        _server.update(server=None, thread=None)


# --------------------------------------------------------------------------
# Vocal Bridge helpers (same calls as the course helpers)
# --------------------------------------------------------------------------
def vb(*args: str, json_output: bool = False) -> Any:
    cmd = ["vb", *args] + (["--json"] if json_output and "--json" not in args else [])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"vb {args} failed:\n{proc.stderr or proc.stdout}")
    out = proc.stdout
    if not json_output:
        return out
    if "--- JSON ---" in out:
        out = out.split("--- JSON ---", 1)[1]
    for i, ch in enumerate(out):
        if ch in "{[":
            return json.loads(out[i:])
    raise ValueError(out)


def append_to_env(key: str, value: str, env_path: str = os.getenv("LAGNEVER_ENV_FILE", ".env")) -> None:
    p = Path(env_path)
    lines = p.read_text().splitlines() if p.exists() else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    p.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def mint_token(agent_id: str, participant_name: str = "Lagnever student") -> dict[str, Any]:
    res = requests.post(f"{VB_API_URL}/api/v1/token",
                        headers={"X-API-Key": os.environ["VOCAL_BRIDGE_API_KEY"], "X-Agent-Id": agent_id,
                                 "Content-Type": "application/json"},
                        json={"participant_name": participant_name}, timeout=15)
    res.raise_for_status()
    d = res.json()
    return {"connection_url": d.get("connection_url") or d["livekit_url"], "token": d["token"],
            "session_name": d["room_name"], "agent_mode": d.get("agent_mode", "")}
