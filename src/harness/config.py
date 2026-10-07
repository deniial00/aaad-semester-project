from dataclasses import dataclass, fields, MISSING
from pathlib import Path
import os
import tomllib
from typing import get_type_hints, get_origin, get_args


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    name: str
    host: str
    temperature: float
    num_ctx: int
    think: bool


@dataclass(frozen=True)
class TargetConfig:
    name: str
    repo_url: str
    commit: str
    acceptance_dir: Path


@dataclass(frozen=True)
class SandboxConfig:
    image: str
    allowed_programs: list[str]
    command_timeout_s: int
    memory: str
    cpus: str
    pids_limit: int
    env: dict[str, str]


@dataclass(frozen=True)
class LimitsConfig:
    max_steps: int
    max_tool_calls: int
    max_denied: int
    max_model_retries: int
    max_output_chars: int


@dataclass(frozen=True)
class CheckConfig:
    name: str
    command: list[str]
    uses_acceptance: bool = False


@dataclass(frozen=True)
class Settings:
    model: ModelConfig
    target: TargetConfig
    sandbox: SandboxConfig
    limits: LimitsConfig
    checks: list[CheckConfig]
    base_dir: Path


def _matches(value, kind):
    if get_origin(kind) is list:
        return isinstance(value, list) and all(_matches(v, get_args(kind)[0]) for v in value)
    if get_origin(kind) is dict:
        key, val = get_args(kind)
        return isinstance(value, dict) and all(_matches(k, key) and _matches(v, val) for k, v in value.items())
    if kind is float:
        return type(value) in (int, float)
    if kind is Path:
        return isinstance(value, str)
    return type(value) is kind


def _section(cls, data, prefix, base):
    if not isinstance(data, dict):
        raise ConfigError(f"wrong type: {prefix} must be a table")
    values = {}
    hints = get_type_hints(cls)
    for field in fields(cls):
        key = f"{prefix}.{field.name}"
        if field.name not in data:
            if field.default is not MISSING:
                continue
            raise ConfigError(f"missing key: {key}")
        value = data[field.name]
        kind = hints[field.name]
        if not _matches(value, kind):
            raise ConfigError(f"wrong type: {key} must be {kind}")
        values[field.name] = (base / value).resolve() if kind is Path else value
    return cls(**values)


def load_settings(path: Path) -> Settings:
    path = path.resolve()
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"config {path}: {error}") from error
    base = path.parent
    sections = {}
    for name, cls in (("model", ModelConfig), ("target", TargetConfig), ("sandbox", SandboxConfig), ("limits", LimitsConfig)):
        if name not in data:
            raise ConfigError(f"missing key: {name}")
        section = dict(data[name]) if isinstance(data[name], dict) else data[name]
        if name == "model" and isinstance(section, dict) and "OLLAMA_HOST" in os.environ:
            section["host"] = os.environ["OLLAMA_HOST"]
        sections[name] = _section(cls, section, name, base)
    if sections["model"].provider != "ollama":
        raise ConfigError("model.provider must be ollama")
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise ConfigError("missing or wrong type: checks must be an array of tables")
    parsed = [_section(CheckConfig, c, f"checks[{i}]", base) for i, c in enumerate(checks)]
    for field in fields(LimitsConfig):
        value = getattr(sections["limits"], field.name)
        minimum = 0 if field.name == "max_model_retries" else 1
        if value < minimum:
            raise ConfigError(f"limits.{field.name} must be >= {minimum}")
    for key in ("command_timeout_s", "pids_limit"):
        if getattr(sections["sandbox"], key) <= 0:
            raise ConfigError(f"sandbox.{key} must be positive")
    if any(not check.command for check in parsed):
        raise ConfigError("checks.command must not be empty")
    return Settings(**sections, checks=parsed, base_dir=base)
