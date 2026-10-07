from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import os
import subprocess
import tempfile
import time

from .config import SandboxConfig


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    error: str | None = None

    def render(self) -> str:
        text = f"exit_code: {self.exit_code}\ntimed_out: {str(self.timed_out).lower()}\n--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}"
        if self.error:
            text += f"\nDocker error: {self.error}"
        return text


class Sandbox(Protocol):
    def run(self, argv: list[str], *, with_acceptance: bool = False) -> CommandResult: ...


def _bounded_read(stream) -> str:
    size = stream.seek(0, os.SEEK_END)
    stream.seek(0)
    if size <= 1_000_000:
        raw = stream.read()
    else:
        first = stream.read(500_000)
        stream.seek(-500_000, os.SEEK_END)
        last = stream.read(500_000)
        marker = f"\n…[output truncated: {size - 1_000_000} of {size} chars omitted]…\n".encode()
        raw = first + marker + last
    return raw.decode('utf-8', errors='replace')


class DockerSandbox:
    def __init__(self, cfg: SandboxConfig, workspace: Path, acceptance_dir: Path, run_id: str):
        self.cfg = cfg
        self.workspace = workspace.resolve()
        self.acceptance_dir = acceptance_dir.resolve()
        self.run_id = run_id
        self.seq = 0

    def _kill(self, name, proc):
        try:
            subprocess.run(['docker', 'kill', name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        # kill rejects a container still in Created state; removal also cancels startup.
        try:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def run(self, argv, *, with_acceptance=False):
        self.seq += 1
        name = f'harness-{self.run_id}-{self.seq}'
        cfg = self.cfg
        cmd = ['docker', 'run', '--rm', '--init', '--name', name,
               '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,size=64m',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--memory', cfg.memory, '--cpus', cfg.cpus, '--pids-limit', str(cfg.pids_limit),
               '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HOME=/tmp']
        for key, value in cfg.env.items():
            cmd.extend(['-e', f'{key}={value}'])
        cmd.extend(['-v', f'{self.workspace}:/work:rw'])
        if with_acceptance:
            cmd.extend(['-v', f'{self.acceptance_dir}:/acceptance:ro'])
        cmd.extend(['-w', '/work', cfg.image, *argv])
        start = time.monotonic()
        timed_out = False
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
            except OSError as error:
                return CommandResult(argv, None, '', '', False, time.monotonic() - start, str(error))
            try:
                proc.wait(timeout=cfg.command_timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill(name, proc)
            except KeyboardInterrupt:
                self._kill(name, proc)
                raise
            out, err = _bounded_read(stdout), _bounded_read(stderr)
            error = err or 'Docker failed with exit 125' if proc.returncode == 125 else None
            return CommandResult(argv, proc.returncode, out, err, timed_out, time.monotonic() - start, error)


def check_docker(image: str) -> str | None:
    try:
        result = subprocess.run(['docker', 'info'], capture_output=True, timeout=20)
        if result.returncode:
            return 'Docker daemon not reachable; start Docker Desktop'
        result = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, timeout=20)
        if result.returncode:
            return f'sandbox image {image} missing; run: docker build -t {image} -f sandbox/cosmic.Dockerfile sandbox/'
    except (OSError, subprocess.TimeoutExpired):
        return 'Docker daemon not reachable; start Docker Desktop'
    return None
