from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import subprocess
import time
from uuid import uuid4

import pytest

from harness.config import load_settings
from harness.sandbox import DockerSandbox, check_docker


pytestmark = pytest.mark.docker
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def sandbox_config():
    cfg = load_settings(PROJECT_ROOT / "harness.example.toml").sandbox
    error = check_docker(cfg.image)
    if error is not None:
        pytest.skip(error)
    return cfg


@pytest.fixture
def sandbox(tmp_path, sandbox_config):
    workspace = tmp_path / "repo"
    acceptance_dir = tmp_path / "acceptance"
    workspace.mkdir()
    acceptance_dir.mkdir()
    return DockerSandbox(
        sandbox_config,
        workspace,
        acceptance_dir,
        run_id=f"test-{uuid4().hex}",
    )


def test_nonzero_command_preserves_stdout(sandbox):
    result = sandbox.run(["python", "-c", "import sys; print('x'); sys.exit(3)"])

    assert result.error is None, result.render()
    assert not result.timed_out, result.render()
    assert result.exit_code == 3, result.render()
    assert result.stdout == "x\n", result.render()


def test_timeout_kills_and_removes_container(sandbox):
    sandbox = DockerSandbox(
        replace(sandbox.cfg, command_timeout_s=2),
        sandbox.workspace,
        sandbox.acceptance_dir,
        sandbox.run_id,
    )
    start = time.monotonic()
    result = sandbox.run(["python", "-c", "import time; time.sleep(60)"])
    elapsed = time.monotonic() - start
    containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=harness-{sandbox.run_id}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )

    assert containers.stdout.strip() == "", containers.stdout
    assert result.error is None, result.render()
    assert result.timed_out, result.render()
    assert elapsed < 20, f"timeout cleanup took {elapsed:.2f}s\n{result.render()}"


def test_network_is_unavailable(sandbox):
    result = sandbox.run([
        "python",
        "-c",
        "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)",
    ])

    assert result.error is None, result.render()
    assert not result.timed_out, result.render()
    assert result.exit_code is not None and result.exit_code != 0, result.render()
    assert "Traceback" in result.stderr, result.render()


def test_root_filesystem_is_read_only(sandbox):
    result = sandbox.run([
        "python",
        "-c",
        "import os; print(bool(os.statvfs('/').f_flag & os.ST_RDONLY), flush=True); open('/etc/x', 'w')",
    ])

    assert result.error is None, result.render()
    assert not result.timed_out, result.render()
    # Non-root permissions alone could reject the write even on a writable root.
    assert result.stdout == "True\n", result.render()
    assert result.exit_code is not None and result.exit_code != 0, result.render()
    assert "Traceback" in result.stderr, result.render()


def test_large_output_is_bounded_and_preserves_head_and_tail(sandbox):
    result = sandbox.run([
        "python",
        "-c",
        "import sys; sys.stdout.write('H' + 'x' * 2_999_998 + 'T')",
    ])
    marker = "\n…[output truncated: 2000000 of 3000000 chars omitted]…\n"

    assert result.error is None, result.render()
    assert not result.timed_out, result.render()
    assert result.exit_code == 0, result.render()
    assert marker in result.stdout
    assert len(result.stdout) == 1_000_000 + len(marker)
    assert result.stdout.startswith("H" + "x" * 63)
    assert result.stdout.endswith("x" * 63 + "T")


def test_interrupt_during_created_state_removes_container(sandbox, monkeypatch):
    name = f"harness-{sandbox.run_id}-1"
    subprocess.run(
        ["docker", "create", "--name", name, sandbox.cfg.image, "python", "-c", "print(1)"],
        check=True, capture_output=True, timeout=15,
    )

    class InterruptedStartup:
        def __init__(self):
            self.interrupted = False

        def wait(self, timeout=None):
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt
            return 0

    # The client is interrupted before start; Docker's Created resource is real.
    monkeypatch.setattr("harness.sandbox.subprocess", SimpleNamespace(
        Popen=lambda *a, **k: InterruptedStartup(),
        run=subprocess.run,
        TimeoutExpired=subprocess.TimeoutExpired,
    ))
    try:
        with pytest.raises(KeyboardInterrupt):
            sandbox.run(["python", "-c", "print(1)"])
        containers = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
            check=True, capture_output=True, text=True, timeout=15,
        )
        assert containers.stdout.strip() == ""
    finally:
        monkeypatch.undo()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
