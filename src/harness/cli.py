import argparse
from datetime import datetime
from pathlib import Path
import shlex
import sys

from .config import ConfigError, load_settings
from .controller import Controller
from .limits import RunStats, StopReason
from .model import OllamaModel
from .prompts import build_system_prompt
from .repo_tools import RepoFiles
from .report import print_event, print_checks, print_summary, write_artifacts
from .sandbox import DockerSandbox, check_docker
from .tools import build_registry
from .verify import all_passed, run_checks
from .workspace import WorkspaceError, collect_diff, prepare_workspace


def terminal_approver(workspace: Path):
    def approve(argv):
        print(f'Command: {shlex.join(argv)}')
        print(f'cwd: /work (sandbox copy of {workspace})')
        try:
            approved = input('Approve this command only? Type yes: ') == 'yes'
        except EOFError:
            approved = False
        print_event('approval', {'argv': argv, 'cwd': '/work', 'approved': approved})
        return approved
    return approve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Offline coding harness with native Ollama tool calling')
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run')
    run.add_argument('--config', type=Path, default=Path('harness.toml'))
    task = run.add_mutually_exclusive_group(required=True)
    task.add_argument('--task')
    task.add_argument('--task-file', type=Path)
    check = sub.add_parser('check')
    check.add_argument('--config', type=Path, default=Path('harness.toml'))
    check.add_argument('--workspace', type=Path)
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        error = check_docker(settings.sandbox.image)
        if error:
            print(error, file=sys.stderr)
            return 2
        task_text = None
        if args.action == 'run':
            task_text = args.task_file.read_text(encoding='utf-8') if args.task_file else args.task
        run_id = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        run_dir = settings.base_dir / 'runs' / run_id
        workspace = (args.workspace.resolve() if args.action == 'check' and args.workspace else
                     prepare_workspace(settings.target, settings.base_dir / '.cache/targets', run_dir))
        if not workspace.is_dir():
            raise WorkspaceError(f'workspace does not exist: {workspace}')
        sandbox = DockerSandbox(settings.sandbox, workspace, settings.target.acceptance_dir, run_id)
        print(f'Workspace: {workspace}', flush=True)
        if args.action == 'check':
            results = run_checks(sandbox, settings.checks)
            print_checks(results)
            return 0 if all_passed(results) else 1
        files = RepoFiles(workspace)
        registry = build_registry(files, sandbox, settings.sandbox.allowed_programs, terminal_approver(workspace))
        prompt = build_system_prompt(files.list_files(max_depth=2), settings.sandbox.allowed_programs, settings.limits)
        outcome = Controller(OllamaModel(settings.model), registry, settings.limits, print_event).run(task_text, prompt)
        results = []
        if outcome.stop_reason != StopReason.INTERRUPTED:
            try:
                results = run_checks(sandbox, settings.checks)
            except KeyboardInterrupt:
                outcome.stop_reason = StopReason.INTERRUPTED
        changed, patch = collect_diff(workspace)
        print_summary(outcome, changed, patch, results, limits=settings.limits)
        write_artifacts(run_dir, outcome, changed, patch, results)
        print(f'Artifacts: {run_dir}')
        if outcome.stop_reason == StopReason.INTERRUPTED:
            return 130
        return 0 if outcome.stop_reason == StopReason.COMPLETED and all_passed(results) else 1
    except (ConfigError, WorkspaceError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Checks: not run (interrupted)', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
