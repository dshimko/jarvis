# deps/

## `ofw-mcp.sha`

The full 40-character git commit sha of the `ofw-mcp` repo (`~/code/ofw-mcp`, eventually
`dshimko/ofw-mcp`) that `scripts/release.sh` packages into the Jarvis release tarball
(`infra/PLAN.md` AD40). One line, the sha only, no trailing content beyond a newline.

`scripts/release.sh` checks this sha out of `OFW_MCP_SRC` (default `~/code/ofw-mcp`) into a
scratch clone, verifies `git rev-parse HEAD` equals it, refuses an unknown sha, builds the wheel
with `uv build --wheel`, and ships the wheel plus that checkout's own `requirements-lock.txt`,
`pyproject.toml`, and `tests/` under `ofw-mcp/` in the tarball. `ops/aws/ssm/jarvis-deploy.sh`
installs that wheel into its own venv (`ofw-venv`) alongside the Jarvis venv and runs the ofw-mcp
test suite (`-m "not browser and not live and not repo"`) before switching `current`.

### The shipped `tests/` contract

The tarball ships only `ofw-mcp/tests/`, `ofw-mcp/pyproject.toml`, `ofw-mcp/requirements-lock.txt`,
and the built wheel -- nothing else from the ofw-mcp repo (no `scripts/`, no `.github/`, no
`src/`). The on-box run (`pytest -q -m "not browser and not live and not repo" -c
ofw-mcp/pyproject.toml ofw-mcp/tests`) must therefore be self-contained: any test that imports
from the repo's `scripts/` directory, reads `.github/` workflow files, or otherwise depends on
anything outside `ofw-mcp/tests/` must carry the ofw-mcp repo's own `repo` pytest marker (alongside
the existing `browser` and `live` markers) so it is excluded here and still runs in the ofw-mcp
repo's own CI, which has the full checkout.

### Creating this file

After the human makes the first commit to the `ofw-mcp` repo (private repo `dshimko/ofw-mcp`,
until then a local, uncommitted-to-GitHub checkout at `~/code/ofw-mcp` per `infra/PLAN.md` AD40):

```bash
cd ~/code/ofw-mcp
git rev-parse HEAD > /path/to/jarvis/deps/ofw-mcp.sha
```

Commit `deps/ofw-mcp.sha` in the Jarvis repo alongside whatever Jarvis-side change depends on the
new ofw-mcp commit (or on its own, to bump the pinned version). `git status` must be clean before
`make release` runs (or `ALLOW_DIRTY=1`), so this file needs to be committed like any other
tracked change, not left untracked.

### Without this file

`make release` / `scripts/release.sh` refuses to run without `deps/ofw-mcp.sha` present, unless
`OFW_MCP_SKIP=1` is set, in which case the release tarball is built with no `ofw-mcp/` directory
at all (Jarvis itself still releases; `ofw-mcp.service` stays condition-skipped on the box until a
release that does include `ofw-mcp/` is deployed).

### Requirements the ofw-mcp repo must provide

- `requirements-lock.txt` at the repo root, hash-pinned (`uv pip compile --generate-hashes`),
  covering runtime *and* test dependencies (the on-box test run installs only from this file plus
  the built wheel, `pip install --no-deps`). `scripts/release.sh` refuses a checkout that is
  missing it.
- `pyproject.toml` buildable with `uv build --wheel` (exactly one wheel must come out of the
  build; more or fewer is a release-time failure).
- A `tests/` directory that runs under `pytest -m "not browser and not live and not repo"`
  without network access and without anything outside `tests/` (see the contract above; browser
  and live tests are phase R/S/W's own concern).

Until `requirements-lock.txt` exists in the ofw-mcp repo, run `make release`/`scripts/release.sh`
with `OFW_MCP_SKIP=1` (or without `deps/ofw-mcp.sha`, which refuses release by default -- the same
flag either way).
