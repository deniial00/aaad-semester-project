from pathlib import Path
import os
import subprocess

from .config import TargetConfig


class WorkspaceError(Exception):
    pass


def _git(*args):
    try:
        return subprocess.run(['git', *map(str, args)], check=True, capture_output=True,
                              env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout.decode(errors='replace')
    except subprocess.CalledProcessError as error:
        raise WorkspaceError(error.stderr.decode(errors='replace')) from error
    except OSError as error:
        raise WorkspaceError(str(error)) from error


def prepare_workspace(target: TargetConfig, cache_dir: Path, run_dir: Path) -> Path:
    cache = cache_dir / target.name
    cache_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        _git('clone', '--quiet', target.repo_url, cache)
    try:
        _git('-C', cache, 'cat-file', '-e', f'{target.commit}^{{commit}}')
    except WorkspaceError:
        _git('-C', cache, 'fetch', '--quiet', 'origin')
    repo = run_dir / 'repo'
    _git('clone', '--quiet', cache, repo)
    _git('-C', repo, 'checkout', '--quiet', '-b', 'harness-run', target.commit)
    _git('-C', repo, 'remote', 'remove', 'origin')
    return repo


def collect_diff(repo: Path) -> tuple[list[str], str]:
    _git('-C', repo, 'add', '-A')
    changed = _git('-C', repo, 'diff', '--cached', '--name-status').splitlines()
    return changed, _git('-C', repo, 'diff', '--cached')
