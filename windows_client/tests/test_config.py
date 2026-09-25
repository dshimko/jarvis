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


def test_defaults_classmethod_derives_paths_from_base_dir(tmp_path):
    defaults = ClientConfig.defaults(tmp_path)

    assert defaults.whisper_exe == str(tmp_path / "whisper" / "whisper-cli.exe")
    assert defaults.whisper_model == str(tmp_path / "whisper" / "ggml-base.en.bin")
