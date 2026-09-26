"""AD3: deployment profile resolution and deep merge of config.<profile>.yaml over config.yaml."""
import pytest
import yaml
from jarvis import config
from jarvis.config import deep_merge, load_config, resolve_deployment

REPO = config.ROOT


def write(root, name, data):
    (root / name).write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def cfg_root(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_DEPLOYMENT", raising=False)
    write(tmp_path, "config.yaml", {"deployment": "local", "api": {"bind": "loopback", "x": 1},
                                    "modes": {"work": {"daily_write_cap": 20, "agents": ["a", "b"]}}})
    write(tmp_path, "config.local.yaml", {"modes": {"work": {"vault": "/l/{mode}"}}})
    write(tmp_path, "config.aws.yaml", {"api": {"bind": "tailscale"},
                                        "modes": {"work": {"vault": "/home/jarvis-{mode}/vault", "agents": ["c"]}}})
    return tmp_path


def test_env_var_wins(cfg_root, monkeypatch):
    monkeypatch.setenv("JARVIS_DEPLOYMENT", "aws")
    assert resolve_deployment({"deployment": "local"}) == "aws"


def test_config_key_then_default(monkeypatch):
    monkeypatch.delenv("JARVIS_DEPLOYMENT", raising=False)
    assert resolve_deployment({"deployment": "aws"}) == "aws"
    assert resolve_deployment({}) == "local"
    assert resolve_deployment(None) == "local"


@pytest.mark.parametrize("bad", ["../etc", "AWS", "a/b", "", "x" * 40])
def test_bad_profile_name_refused(monkeypatch, bad):
    monkeypatch.setenv("JARVIS_DEPLOYMENT", bad)
    with pytest.raises(SystemExit, match="deployment"):
        resolve_deployment({})


def test_deep_merge_is_recursive_and_pure():
    base = {"a": {"b": 1, "c": [1, 2]}, "d": 1}
    over = {"a": {"c": [3], "e": 2}}
    merged = deep_merge(base, over)
    assert merged == {"a": {"b": 1, "c": [3], "e": 2}, "d": 1}
    assert base == {"a": {"b": 1, "c": [1, 2]}, "d": 1}          # inputs untouched
    merged["a"]["b"] = 9
    assert base["a"]["b"] == 1


def test_load_config_merges_profile(cfg_root):
    cfg = load_config(cfg_root, "aws")
    assert cfg["deployment"] == "aws"
    assert cfg["api"] == {"bind": "tailscale", "x": 1}
    assert cfg["modes"]["work"] == {"daily_write_cap": 20, "agents": ["c"], "vault": "/home/jarvis-{mode}/vault"}


def test_load_config_resolves_profile_from_env(cfg_root, monkeypatch):
    assert load_config(cfg_root)["deployment"] == "local"
    monkeypatch.setenv("JARVIS_DEPLOYMENT", "aws")
    assert load_config(cfg_root)["deployment"] == "aws"


def test_missing_profile_file_is_startup_error(cfg_root):
    with pytest.raises(SystemExit, match="config.staging.yaml"):
        load_config(cfg_root, "staging")


def test_repo_base_has_no_windows_paths():
    text = (REPO / "config.yaml").read_text(encoding="utf-8")
    assert "${WIN_HOME}" not in text
    assert yaml.safe_load(text)["deployment"] == "local"


def test_repo_aws_profile(monkeypatch):
    monkeypatch.delenv("JARVIS_DEPLOYMENT", raising=False)
    cfg = load_config(REPO, "aws")
    assert cfg["api"]["bind"] == "tailscale" and cfg["api"]["ports"] == {"work": 8781, "personal": 8782}
    for name, vault_name in (("work", "Jarvis-Work"), ("personal", "Jarvis-Personal")):
        m = cfg["modes"][name]
        assert m["vault"] == "/home/jarvis-{mode}/vault"
        assert m["env_file"] == "/home/jarvis-{mode}/.jarvis/env"
        assert m["obsidian_vault_name"] == vault_name
    assert cfg["ofw_watch"]["poll_minutes"] == 5
    assert cfg["ofw_watch"]["query"] == "from:@ourfamilywizard.com is:unread newer_than:2d"


def test_repo_local_profile(monkeypatch):
    monkeypatch.delenv("JARVIS_DEPLOYMENT", raising=False)
    cfg = load_config(REPO, "local")
    assert cfg["api"]["bind"] == "loopback" and cfg["api"]["ports"] == {"work": 8781, "personal": 8782}
    assert cfg["modes"]["work"]["vault"] == "${WIN_HOME}/Vaults/Jarvis-Work"
    assert cfg["modes"]["personal"]["env_file"] == "env/personal.env"
    assert cfg["modes"]["work"]["read_tools"]            # shared keys still come from config.yaml


@pytest.mark.parametrize("profile", ["local", "aws"])
def test_ofw_config_ad39(profile):
    cfg = load_config(REPO, profile)
    personal = cfg["modes"]["personal"]
    assert set(cfg["schedule"]["personal"]) == {"evening-review"}          # nothing polls OFW on a timer
    assert "ofw-mcp" not in (cfg["modes"]["work"].get("repos") or {})
    assert "mcp__ofw__ofw_status" in personal["read_tools"]
    assert "mcp__ofw__ofw_status" not in personal["write_tools"]
    control = {"mcp__ofw__confirm_privileged", "mcp__ofw__reset_breaker"}
    assert not control & set(personal["read_tools"])                        # write-token tools stay out of sessions
    assert not [t for t in cfg["modes"]["work"]["read_tools"] if t.startswith("mcp__ofw__")]
