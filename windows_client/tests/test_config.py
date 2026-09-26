"""Config defaults: the file is created on first load, missing keys fall back to computed
defaults, and unknown keys in an existing file are ignored rather than raising."""
import yaml

from jarvis_client.config import ClientConfig, load_config


def test_first_load_writes_client_yaml_with_defaults(tmp_path):
    cfg = load_config(base_dir=tmp_path)

    config_file = tmp_path / "client.yaml"
    assert config_file.exists()
    assert cfg.api_url == "http://localhost:8765"
    assert cfg.sample_rate == 16000
    assert cfg.hotkey_work == "<ctrl>+<alt>+w"
    assert cfg.hotkey_personal == "<ctrl>+<alt>+p"
    assert cfg.wsl_distro == "Ubuntu"
    assert cfg.vault_work == "Jarvis-Work"
    assert cfg.vault_personal == "Jarvis-Personal"
    assert cfg.token_path == str(tmp_path / "api_token")
    assert cfg.piper_exe is None and cfg.piper_voice is None
    # AD16: a file this code itself creates is treated as "predates the change" -- install.ps1 is
    # the only writer that puts profile: aws into a fresh file (see test_defaults... below).
    assert cfg.profile == "wsl"
    assert cfg.api_url_work == "http://localhost:8781"
    assert cfg.api_url_personal == "http://localhost:8782"
    assert cfg.token_source == "file"
    assert cfg.aws_profile == ""
    assert cfg.aws_region == "us-east-1"
    assert cfg.token_secret_work == "jarvis/work/api-token"
    assert cfg.token_secret_personal == "jarvis/personal/api-token"


def test_second_load_reads_the_persisted_file_unchanged(tmp_path):
    first = load_config(base_dir=tmp_path)
    second = load_config(base_dir=tmp_path)

    assert first == second


def test_existing_file_values_win_over_defaults(tmp_path):
    load_config(base_dir=tmp_path)  # create the default file first
    config_file = tmp_path / "client.yaml"
    data = yaml.safe_load(config_file.read_text())
    data["api_url"] = "http://localhost:9999"
    data["unknown_future_key"] = "ignored"
    config_file.write_text(yaml.safe_dump(data))

    cfg = load_config(base_dir=tmp_path)

    assert cfg.api_url == "http://localhost:9999"
    assert not hasattr(cfg, "unknown_future_key")


def test_missing_keys_in_an_existing_file_fall_back_to_defaults(tmp_path):
    (tmp_path / "client.yaml").write_text(yaml.safe_dump({"api_url": "http://localhost:1234"}))

    cfg = load_config(base_dir=tmp_path)

    assert cfg.api_url == "http://localhost:1234"
    assert cfg.sample_rate == 16000
    assert cfg.wsl_distro == "Ubuntu"


def test_a_file_predating_this_change_defaults_profile_to_wsl(tmp_path):
    """AD16: an existing client.yaml with none of the new keys (as written before this change)
    must still load, defaulting profile to "wsl" and the legacy api_url/token_path untouched."""
    (tmp_path / "client.yaml").write_text(
        yaml.safe_dump({"api_url": "http://localhost:8765", "token_path": "C:/old/api_token"})
    )

    cfg = load_config(base_dir=tmp_path)

    assert cfg.profile == "wsl"
    assert cfg.api_url == "http://localhost:8765"
    assert cfg.token_path == "C:/old/api_token"
    assert cfg.token_source == "file"


def test_an_aws_profile_file_round_trips_its_own_values(tmp_path):
    (tmp_path / "client.yaml").write_text(yaml.safe_dump({
        "profile": "aws",
        "api_url_work": "http://jarvis:8781",
        "api_url_personal": "http://jarvis:8782",
        "token_source": "secretsmanager",
        "aws_profile": "jarvis-client-sso",
        "token_secret_work": "jarvis/work/api-token",
        "token_secret_personal": "jarvis/personal/api-token",
    }))

    cfg = load_config(base_dir=tmp_path)

    assert cfg.profile == "aws"
    assert cfg.api_url_work == "http://jarvis:8781"
    assert cfg.api_url_personal == "http://jarvis:8782"
    assert cfg.token_source == "secretsmanager"
    assert cfg.aws_profile == "jarvis-client-sso"


def test_defaults_classmethod_derives_paths_from_base_dir(tmp_path):
    defaults = ClientConfig.defaults(tmp_path)

    assert defaults.whisper_exe == str(tmp_path / "whisper" / "whisper-cli.exe")
    assert defaults.whisper_model == str(tmp_path / "whisper" / "ggml-base.en.bin")
