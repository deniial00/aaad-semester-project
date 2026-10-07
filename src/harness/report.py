from dataclasses import asdict
from pathlib import Path
import json

from .config import LimitsConfig
from .controller import RunOutcome
from .limits import truncate
from .verify import all_passed


def print_event(kind, data):
    if kind == 'tool_call':
        args, _ = truncate(json.dumps(data['arguments'], ensure_ascii=False), 200)
        print(f"[step {data['step']}] → {data['name']} {args}", flush=True)
    elif kind == 'tool_result':
        print(f"  ← {'ok' if data['ok'] else 'error'} ({data['chars']} chars{', truncated' if data['truncated'] else ''})", flush=True)
    elif kind == 'denied':
        print(f"  ✗ DENIED: {data['message']}", flush=True)
    elif kind == 'retry':
        print(f"  ↻ model retry {data['retry']}: {data['error']}", flush=True)
    elif kind == 'approval':
        print(f"  approval: {'approved' if data['approved'] else 'denied'}", flush=True)
    elif kind == 'stop':
        print(f"Stop: {data['reason']}", flush=True)


def print_checks(results):
    print('== Checks ==')
    for result in results:
        print(f'[{result.status.upper()}] {result.name} (exit {result.exit_code}, {result.duration_s:.1f}s)')
        if result.status != 'passed':
            print('\n'.join('  ' + line for line in result.output.splitlines()))
    print('RESULT: ALL CHECKS PASSED' if all_passed(results) else
          "RESULT: CHECKS NOT PASSED — the model's completion claim is not verified")


def print_summary(outcome: RunOutcome, changed, patch, results, *, limits: LimitsConfig | None = None):
    print('== Run summary ==')
    print(f'stop reason: {outcome.stop_reason.value}')
    print(f'model final message: {outcome.final_message}')
    s = outcome.stats
    def count(value, key):
        return f'{value}/{getattr(limits, key)}' if limits else str(value)
    print(f"steps {count(s.steps, 'max_steps')}  tool calls {count(s.tool_calls, 'max_tool_calls')}  denied {count(s.denied, 'max_denied')}  tool errors {s.tool_errors}  model retries {s.retries}")
    print('== Changed files ==')
    print('\n'.join(changed) or '(none)')
    print('== Diff ==')
    print(patch or '(none)')
    if outcome.stop_reason.value == 'interrupted':
        print('Checks: not run (interrupted)')
    else:
        print_checks(results)


def write_artifacts(run_dir: Path, outcome, changed, patch, results):
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / 'transcript.jsonl').open('w', encoding='utf-8') as stream:
        for message in outcome.messages:
            stream.write(json.dumps(message, ensure_ascii=False) + '\n')
    (run_dir / 'changes.diff').write_text(patch, encoding='utf-8')
    summary = {'stop_reason': outcome.stop_reason.value, 'final_message': outcome.final_message,
               'stats': asdict(outcome.stats), 'changed_files': changed,
               'checks': [asdict(result) for result in results]}
    (run_dir / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
