"""On WSL both per-mode daemons share ~/.jarvis (one Linux user), so the tone ledger's read-modify-write
must hold across processes, not only threads."""
import json, os, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WRITES = 40
SCRIPT = """
import sys
from jarvis import ledger
mode = sys.argv[1]
for i in range(int(sys.argv[2])):
    ledger.flag_tone(mode, f"{mode}-{i}", "h")
"""


def test_two_daemons_do_not_lose_tone_flags(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(REPO)}
    procs = [subprocess.Popen([sys.executable, "-c", SCRIPT, m, str(WRITES)], env=env, cwd=REPO)
             for m in ("work", "personal")]
    assert all(p.wait(60) == 0 for p in procs)
    data = json.loads((tmp_path / ".jarvis" / "tone_flags.json").read_text())
    assert len(data["work"]) == WRITES and len(data["personal"]) == WRITES
