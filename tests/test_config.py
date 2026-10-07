import pytest

from harness.config import ConfigError, load_settings


VALID_CONFIG = '''\
[model]
provider = "ollama"
name = "qwen3:8b"
host = "http://configured-host:11434"
temperature = 0.0
num_ctx = 32768
think = false

[target]
name = "cosmicpython"
repo_url = "https://example.invalid/repo.git"
commit = "abc123"
acceptance_dir = "acceptance/cosmic"

[sandbox]
image = "harness-test:latest"
allowed_programs = ["pytest", "python"]
command_timeout_s = 30
memory = "1g"
cpus = "1.0"
pids_limit = 64
env = { PYTHONPATH = "/work/src" }

[limits]
max_steps = 5
max_tool_calls = 8
max_denied = 2
max_model_retries = 1
max_output_chars = 1000

[[checks]]
name = "regression"
command = ["pytest", "-q"]
'''


def write_config(tmp_path, text=VALID_CONFIG):
    path = tmp_path / "harness.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_settings_resolves_acceptance_path_from_config_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config_path = write_config(tmp_path)
    settings = load_settings(config_path)
    assert settings.base_dir == tmp_path.resolve()
    assert settings.target.acceptance_dir == (tmp_path / "acceptance/cosmic").resolve()
    assert settings.model.host == "http://configured-host:11434"
    assert settings.checks[0].name == "regression"
    assert settings.checks[0].command == ["pytest", "-q"]


def test_ollama_host_environment_overrides_config_host(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://override-host:11434")
    settings = load_settings(write_config(tmp_path))
    assert settings.model.host == "http://override-host:11434"


@pytest.mark.parametrize("text,expected", [
    (VALID_CONFIG.replace('max_steps = 5\n', ''), "missing key: limits.max_steps"),
    (VALID_CONFIG.replace('max_steps = 5', 'max_steps = "five"'), "wrong type: limits.max_steps"),
    (VALID_CONFIG.replace('acceptance_dir = "acceptance/cosmic"', 'acceptance_dir = 42'),
     "wrong type: target.acceptance_dir"),
    (VALID_CONFIG.replace('provider = "ollama"', 'provider = "other"'), "model.provider must be ollama"),
    (VALID_CONFIG.replace('command = ["pytest", "-q"]', 'command = []'), "checks.command must not be empty"),
])
def test_config_validation_identifies_invalid_values(tmp_path, monkeypatch, text, expected):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    with pytest.raises(ConfigError, match=expected.replace("[", r"\[").replace("]", r"\]")):
        load_settings(write_config(tmp_path, text))


def test_missing_config_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="config .*missing.toml"):
        load_settings(tmp_path / "missing.toml")
