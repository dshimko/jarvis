"""Thin adapter for TypeSafe Jev (System One decision model).

Jev returns typed decisions (Choice / Score / Noul probability) with confidence.
It is ADVISORY here: it never triggers a side effect and never replaces a code gate.
Payload shape below follows the public examples (model, state, questions); confirm
against current TypeSafe docs and swap in their SDK if you prefer.
If Jev is unavailable, falls back to Claude Haiku via `claude -p` returning JSON.
"""
from __future__ import annotations
import json, logging, subprocess
import httpx
from . import paths
from .modes import CFG, base_env

log = logging.getLogger(__name__)
JCFG = CFG["jev"]
NO_HOOKS = json.dumps({"disableAllHooks": True})
FALLBACK_TIMEOUT = 60


class Decision(dict):
    @property
    def value(self): return self.get("value")
    @property
    def confidence(self) -> float: return float(self.get("confidence", 0.0))


def _jev_call(env: dict, state: str, questions: dict) -> dict:
    url, key = env.get("JEV_API_URL"), env.get("JEV_API_KEY")
    if not (JCFG["enabled"] and url and key):
        raise RuntimeError("jev disabled")
    r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=5.0,
                   json={"model": JCFG["model"], "state": state, "questions": questions})
    r.raise_for_status()
    return r.json()


def _fallback_cwd():
    """Neutral cwd for the fallback call: never the repo, never a vault (C9)."""
    d = paths.jarvis_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _haiku_fallback(state: str, questions: dict) -> dict:
    """C9: minimal env, no MCP servers, no hooks, neutral cwd. Any failure here raises (fail closed);
    callers (router: caught broadly; outbox._check_jev: uncaught, blocks the send) must not see a
    permissive default."""
    prompt = ("Answer each question about the STATE. Respond with only JSON mapping each question key to "
              '{"value": <answer>, "confidence": <0..1>}. For choice questions value must be one of the options; '
              "for probability questions value is a number 0..1.\n"
              f"QUESTIONS: {json.dumps(questions)}\nSTATE:\n{state}")
    cmd = ["claude", "-p", prompt, "--model", "haiku", "--output-format", "json", "--allowedTools", "",
           "--strict-mcp-config", "--settings", NO_HOOKS]
    try:
        out = subprocess.run(cmd, cwd=_fallback_cwd(), env=base_env(), capture_output=True, text=True,
                              timeout=FALLBACK_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("jev haiku fallback timed out") from e
    except OSError as e:
        raise RuntimeError("jev haiku fallback could not start claude") from e
    if out.returncode != 0:
        log.warning("jev haiku fallback exited %d", out.returncode)
        raise RuntimeError(f"jev haiku fallback exited {out.returncode}")
    try:
        payload = json.loads(out.stdout)
        text = str(payload.get("result", "{}")).strip().strip("`").removeprefix("json")
        return json.loads(text)
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        raise RuntimeError("jev haiku fallback returned malformed JSON") from e


def decide(env: dict, state: str, questions: dict) -> dict[str, Decision]:
    try:
        raw = _jev_call(env, state, questions)
        answers = raw.get("answers", raw)
    except Exception:
        answers = _haiku_fallback(state, questions)
    return {k: Decision(v if isinstance(v, dict) else {"value": v, "confidence": 0.0}) for k, v in answers.items()}


def choice(instructions: str, options: list[str]) -> dict:
    return {"type": "choice", "instructions": instructions, "options": options}


def probability(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}
