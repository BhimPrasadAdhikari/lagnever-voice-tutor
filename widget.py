"""Lagnever Notebook voice widget for Jupyter (v2).

Draws the tutor's notebook page (blue ink = student, red pen = tutor,
green tick, highlighter) and connects the mic to Vocal Bridge in AI Agent
mode. Every spoken turn -> onQuery -> POST /query -> speech comes back first,
then the widget polls /state until the page version moves and redraws.
"""
import json
import time

VB_REACT_VER = "0.1.1"
VB_SDK_VER = "0.1.1"

_HTML = r"""
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Anek+Latin:wght@400..700&family=Anek+Devanagari:wght@400..700&family=Mukta:wght@400;500;600;700&family=Kalam:wght@400;700&display=swap">
<style>
#__ROOT__ { --nb-paper:#fbfaf6; --nb-surface:#ffffff; --nb-sunken:#f3f1ea; --nb-rule:#e6e2d7; --nb-rule-strong:#d2ccbd; --nb-control:#8a8576; --nb-margin:#f0b4ac;
  --nb-ink:#141b2d; --nb-pencil:#4a5163; --nb-pencil-light:#626878; --nb-blue-ink:#1f3fb0; --nb-blue-wash:#e8ecfb; --nb-red-pen:#b8322a; --nb-red-wash:#fbeceb;
  --nb-tick:#1b7343; --nb-tick-wash:#e6f3ec; --nb-highlighter:#ffe15a; --nb-highlighter-wash:#fff6c9;
  font-family: Mukta, system-ui, sans-serif; color: var(--nb-ink); background: var(--nb-paper); border:1px solid var(--nb-rule-strong); border-radius: 14px; padding: 16px; max-width: 1100px; }
#__ROOT__ * { box-sizing: border-box; }
#__ROOT__ .lv-wrap { display:flex; gap:16px; flex-wrap:wrap; }
#__ROOT__ .lv-page { flex: 3 1 420px; min-height: 460px; position:relative; background-color: var(--nb-surface);
  background-image: repeating-linear-gradient(to bottom, transparent 0, transparent 39px, var(--nb-rule) 39px, var(--nb-rule) 40px);
  background-position: 0 56px; border:1px solid var(--nb-rule); border-radius: 10px; padding: 14px 18px 18px 72px; line-height: 40px; }
#__ROOT__ .lv-page:before { content:""; position:absolute; left:56px; top:0; bottom:0; width:2px; background: var(--nb-margin); }
#__ROOT__ .lv-brand { font-family:'Anek Latin'; font-weight:700; font-size:15px; color:var(--nb-pencil); letter-spacing:.02em; line-height:28px; }
#__ROOT__ .lv-q { font-family:'Anek Latin','Anek Devanagari'; font-weight:600; font-size:24px; line-height:40px; }
#__ROOT__ .lv-q b { color: var(--nb-pencil-light); font-weight:600; margin-right:8px; }
#__ROOT__ .lv-approach { color: var(--nb-pencil); font-size:17px; }
#__ROOT__ .lv-approach span { font-family:'Anek Latin'; font-weight:600; color: var(--nb-ink); margin-right: 8px; }
#__ROOT__ .lv-line { position:relative; font-size:21px; animation: lvIn .45s cubic-bezier(0.2,0,0,1) both; }
#__ROOT__ .lv-n { position:absolute; left:-52px; width:36px; text-align:right; color: var(--nb-pencil-light); font-size:14px; }
#__ROOT__ .lv-student { color: var(--nb-blue-ink); }
#__ROOT__ .lv-tutor { color: var(--nb-ink); }
#__ROOT__ .lv-txt { position:relative; display:inline-block; }
#__ROOT__ .lv-hl { background: var(--nb-highlighter); padding: 0 4px; }
#__ROOT__ .lv-strike:after { content:""; position:absolute; left:-3px; top:52%; height:2.5px; background: var(--nb-red-pen); border-radius:2px; transform: rotate(-2deg); animation: lvStrike .6s cubic-bezier(0.65,0,0.35,1) forwards; width:0; }
#__ROOT__ .lv-pen { font-family: Kalam, cursive; color: var(--nb-red-pen); font-size: 20px; margin-left: 14px; display:inline-block; animation: lvWrite .7s cubic-bezier(0.65,0,0.35,1) both; }
#__ROOT__ .lv-tick { font-family: Kalam, cursive; color: var(--nb-tick); font-weight:700; font-size: 26px; margin-left: 12px; display:inline-block; animation: lvWrite .5s cubic-bezier(0.65,0,0.35,1) both; }
#__ROOT__ .lv-final { margin-top: 4px; font-family:'Anek Latin'; font-weight:600; font-size:22px; }
#__ROOT__ .lv-final .lv-box { border: 2px solid var(--nb-ink); border-radius: 6px; padding: 0 10px; background: var(--nb-highlighter); }
#__ROOT__ .lv-empty { color: var(--nb-pencil-light); font-size: 18px; }
#__ROOT__ .lv-side { flex: 2 1 300px; display:flex; flex-direction:column; gap:12px; }
#__ROOT__ .lv-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
#__ROOT__ .lv-btn { font-family: Mukta; font-weight:600; font-size:15px; padding: 9px 16px; border-radius: 8px; border: 1px solid var(--nb-ink); background: var(--nb-ink); color: #fff; cursor:pointer; }
#__ROOT__ .lv-btn.ghost { background: transparent; color: var(--nb-ink); border-color: var(--nb-control); }
#__ROOT__ .lv-btn:disabled { opacity:.5; cursor:default; }
#__ROOT__ .lv-state { font-size: 13px; color: var(--nb-pencil); }
#__ROOT__ .lv-dot { display:inline-block; width:9px; height:9px; border-radius:50%; background: var(--nb-control); margin-right:6px; vertical-align:middle; }
#__ROOT__ .lv-dot.on { background: var(--nb-tick); }
#__ROOT__ .lv-pill { font-size: 12px; font-weight:600; letter-spacing:.04em; padding: 2px 10px; border-radius: 999px; background: var(--nb-sunken); color: var(--nb-pencil); }
#__ROOT__ .lv-pill.think { background: var(--nb-highlighter-wash); color: var(--nb-ink); }
#__ROOT__ .lv-card { background: var(--nb-surface); border:1px solid var(--nb-rule); border-radius:10px; padding: 10px 12px; }
#__ROOT__ .lv-h { font-family:'Anek Latin'; font-weight:600; font-size:13px; color: var(--nb-pencil); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
#__ROOT__ .lv-tr { height: 230px; overflow-y:auto; font-size: 15px; line-height: 1.45; }
#__ROOT__ .lv-tr div { margin-bottom: 6px; }
#__ROOT__ .lv-who { font-weight:700; font-size:11px; text-transform:uppercase; letter-spacing:.08em; margin-right:6px; }
#__ROOT__ .lv-who.user { color: var(--nb-blue-ink); } #__ROOT__ .lv-who.agent, #__ROOT__ .lv-who.tutor { color: var(--nb-ink); } #__ROOT__ .lv-who.app { color: var(--nb-pencil-light); }
#__ROOT__ input.lv-in { flex:1; min-width: 160px; font-family: Mukta; font-size: 15px; padding: 8px 10px; border: 1px solid var(--nb-control); border-radius: 8px; background: var(--nb-surface); color: var(--nb-ink); }
#__ROOT__ .lv-spot { font-size: 14px; color: var(--nb-ink); margin-bottom: 4px; }
#__ROOT__ .lv-spot i { font-style: normal; color: var(--nb-tick); font-weight: 600; }
#__ROOT__ .lv-err { color: var(--nb-red-pen); font-size: 13px; }
@keyframes lvIn { from { opacity:0; transform: translateY(12px); } to { opacity:1; transform:none; } }
@keyframes lvStrike { to { width: calc(100% + 6px); } }
@keyframes lvWrite { from { clip-path: inset(0 100% 0 0); } to { clip-path: inset(0 0 0 0); } }
</style>
<div id="__ROOT__">loading Lagnever tutor...</div>
<script type="module">
import React, { useState, useEffect, useRef } from 'https://esm.sh/react@18';
import ReactDOM from 'https://esm.sh/react-dom@18/client';
const e = React.createElement;
const TOKEN = __TOKEN__;
const RAW_BASE = __BASE__;

function resolveBase(raw) {
  const m = raw.match(/^https?:\/\/(?:localhost|127\.0\.0\.1):(\d+)$/);
  if (!m) return raw;
  const loc = window.location;
  if (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') return raw;
  const port = m[1];
  const sub = loc.hostname.match(/^(.+)p(\d+)\.(.+)$/);
  if (sub) return loc.protocol + '//' + sub[1] + 'p' + port + '.' + sub[3];
  const bm = loc.pathname.match(/^(.*?)\/(notebooks|lab|tree|files|edit|doc)\//);
  return loc.origin + (bm ? bm[1] : '') + '/proxy/' + port;
}
const BASE = resolveBase(RAW_BASE);

async function call(path, body) {
  const headers = { 'Content-Type': 'application/json' };
  const x = document.cookie.match(/(?:^|;\s*)_xsrf=([^;]+)/);
  if (x) headers['X-XSRFToken'] = decodeURIComponent(x[1]);
  const res = await fetch(BASE + path, body === undefined ? { headers, credentials: 'same-origin' }
    : { method: 'POST', headers, credentials: 'same-origin', body: JSON.stringify(body) });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function Page({ page }) {
  if (!page || (!page.question && page.lines.length === 0)) {
    return e('div', { className: 'lv-page' },
      e('div', { className: 'lv-brand' }, 'lagnever.'),
      e('div', { className: 'lv-empty' }, 'Ask your doubt out loud, or type it on the right. The tutor writes here.'));
  }
  return e('div', { className: 'lv-page' },
    e('div', { className: 'lv-brand' }, 'lagnever.'),
    page.question && e('div', { className: 'lv-q', key: 'q' + page.question }, e('b', null, 'Q.'), page.question),
    page.approach && e('div', { className: 'lv-approach lv-line' }, e('span', null, 'How to think about it'), page.approach),
    page.notes.map((n, i) => e('div', { key: 'pn' + i + n.text, className: 'lv-line' },
      n.kind === 'highlight' ? e('span', { className: 'lv-hl' }, n.text) : e('span', { className: 'lv-pen', style: { marginLeft: 0 } }, '← ' + n.text))),
    page.lines.map((ln) => e('div', { key: ln.n + ln.text, className: 'lv-line ' + (ln.who === 'student' ? 'lv-student' : 'lv-tutor') },
      e('span', { className: 'lv-n' }, ln.n),
      e('span', { className: 'lv-txt' + (ln.struck ? ' lv-strike' : '') + (ln.highlight ? ' lv-hl' : '') }, ln.text),
      ln.struck && ln.correction && e('span', { className: 'lv-pen' }, '→ ' + ln.correction),
      ln.ticked && e('span', { className: 'lv-tick' }, '✓'),
      ln.notes.map((n, i) => e('span', { key: i, className: 'lv-pen' }, '← ' + n.text)))),
    page.final && e('div', { className: 'lv-final lv-line' }, 'Answer: ', e('span', { className: 'lv-box' }, page.final),
      page.final_ticked && e('span', { className: 'lv-tick' }, '✓')));
}

function Shell({ voice }) {
  const [page, setPage] = useState(null);
  const [spots, setSpots] = useState([]);
  const [log, setLog] = useState([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const trRef = useRef(null);
  const add = (who, t) => setLog((p) => p.concat([{ who, text: t }]));

  useEffect(() => { call('/state').then((s) => { setPage(s.page); setSpots(s.tricky_spots); }).catch((x) => setErr('tutor server: ' + x.message)); }, []);

  async function followPage(v0, t0) {
    for (let i = 0; i < 60; i++) {
      await sleep(250);
      try {
        const s = await call('/state');
        if (s.version > v0) {
          setPage(s.page); setSpots(s.tricky_spots || []);
          const L = s.last || {};
          add('app', 'page +' + (Date.now() - t0) + ' ms' + (L.memory_events && L.memory_events.length ? ' · ' + L.memory_events.join(', ') : ''));
          return;
        }
      } catch (x) { /* keep polling */ }
    }
  }

  async function ask(q, source) {
    if (source === 'typed') add('user', q);
    add('app', 'query_agent → ' + q);
    setBusy(true);
    const t0 = Date.now();
    try {
      const r = await call('/query', { query: q });
      const ck = r.checker && r.checker.status && r.checker.status !== 'unchecked' ? ' · sympy: ' + r.checker.status : '';
      add('app', 'speech ' + (Date.now() - t0) + ' ms' + ck + (r.override ? ' (overrode draft)' : '') + (r.error ? ' · error: ' + r.error : ''));
      if (r.pending) { followPage(r.version, t0); } else { setPage(r.page); setSpots(r.tricky_spots || []); }
      if (source === 'typed') add('tutor', r.response);
      return r.response;
    } catch (x) { setErr(x.message); return 'Sorry, I could not reach my notes. Could you say that again?'; }
    finally { setBusy(false); }
  }

  const transcript = voice ? voice.useLines() : [];
  const lines = transcript.concat(log);
  useEffect(() => { if (trRef.current) trRef.current.scrollTop = trRef.current.scrollHeight; }, [lines.length]);
  if (voice) voice.useQuery(ask);

  return e('div', { className: 'lv-wrap' },
    e(Page, { page }),
    e('div', { className: 'lv-side' },
      e('div', { className: 'lv-row' },
        voice ? e(voice.Controls) : e('div', { className: 'lv-state' }, 'Text test mode (no voice token).'),
        e('span', { className: 'lv-pill' + (busy ? ' think' : '') }, busy ? 'thinking…' : 'listening')),
      e('div', { className: 'lv-card' }, e('div', { className: 'lv-h' }, 'Conversation'),
        e('div', { className: 'lv-tr', ref: trRef }, lines.length === 0 ? e('div', { className: 'lv-state' }, 'Transcript appears here.')
          : lines.map((l, i) => e('div', { key: i }, e('span', { className: 'lv-who ' + (l.who || l.role) }, l.who || l.role), l.text)))),
      e('form', { className: 'lv-row', onSubmit: (ev) => { ev.preventDefault(); if (text.trim() && !busy) { ask(text.trim(), 'typed'); setText(''); } } },
        e('input', { className: 'lv-in', value: text, placeholder: 'Type as the student (test without mic)', onChange: (ev) => setText(ev.target.value) }),
        e('button', { className: 'lv-btn', type: 'submit', disabled: busy }, busy ? 'Thinking…' : 'Send'),
        e('button', { className: 'lv-btn ghost', type: 'button', onClick: () => call('/reset', {}).then((s) => { setPage(s.page); setSpots(s.tricky_spots); add('app', 'new session'); }) }, 'New page')),
      e('div', { className: 'lv-card' }, e('div', { className: 'lv-h' }, 'Tricky spots (memory)'),
        spots.length === 0 ? e('div', { className: 'lv-state' }, 'None open.') :
        spots.map((s) => e('div', { key: s.id, className: 'lv-spot' }, s.slip, ' ', e('i', null, s.right_in_a_row + '/3')))),
      err && e('div', { className: 'lv-err' }, err)));
}

async function makeVoice() {
  if (!TOKEN) return null;
  const vbr = await import('https://esm.sh/@vocalbridgeai/react@__VBR__?deps=react@18,react-dom@18');
  const sdk = await import('https://esm.sh/@vocalbridgeai/sdk@__VBS__');
  const { VocalBridgeProvider, useVocalBridge, useTranscript, useAIAgent } = vbr;
  const CS = sdk.ConnectionState;
  const tokenProvider = async () => ({ url: TOKEN.connection_url, token: TOKEN.token, room_name: TOKEN.session_name,
    participant_identity: 'lagnever-student', expires_in: 3600, agent_mode: TOKEN.agent_mode });
  function Controls() {
    const { state, connect, disconnect, error } = useVocalBridge();
    const off = state === CS.Disconnected;
    const wait = state === CS.Connecting || state === CS.WaitingForAgent;
    return e('div', { className: 'lv-row' },
      e('button', { className: 'lv-btn', onClick: off ? connect : disconnect, disabled: wait }, off ? 'Start voice tutor' : 'End session'),
      e('span', { className: 'lv-state' }, e('span', { className: 'lv-dot' + (off ? '' : ' on') }), String(state)),
      error && e('div', { className: 'lv-err' }, error.message));
  }
  return {
    Provider: (p) => e(VocalBridgeProvider, { options: { auth: { tokenProvider }, debug: false } }, p.children),
    Controls,
    useLines: () => { const { transcript } = useTranscript(); return transcript.map((t) => ({ role: t.role, text: t.text })); },
    useQuery: (ask) => { useAIAgent({ onQuery: async (q) => ask(q, 'voice') }); },
  };
}

const root = document.getElementById('__ROOT__');
makeVoice().then((voice) => {
  root.innerHTML = '';
  const app = voice ? e(voice.Provider, null, e(Shell, { voice })) : e(Shell, { voice: null });
  ReactDOM.createRoot(root).render(app);
}).catch((x) => { root.textContent = 'Could not load voice SDK: ' + x.message; });
</script>
"""


def notebook_widget(server_url: str, token: dict | None = None) -> str:
    """HTML for the Lagnever voice tutor. token=None gives text-only test mode."""
    root = f"lv-{int(time.time() * 1000)}"
    return (_HTML.replace("__ROOT__", root)
            .replace("__TOKEN__", json.dumps(token) if token else "null")
            .replace("__BASE__", json.dumps(server_url))
            .replace("__VBR__", VB_REACT_VER).replace("__VBS__", VB_SDK_VER))
