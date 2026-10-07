from dataclasses import dataclass
from typing import Literal

from .config import CheckConfig
from .limits import truncate
from .sandbox import Sandbox


@dataclass
class CheckResult:
    name: str
    status: Literal['passed', 'failed', 'timed_out', 'unavailable']
    exit_code: int | None
    output: str
    duration_s: float


def run_checks(sandbox: Sandbox, checks: list[CheckConfig]) -> list[CheckResult]:
    results = []
    for check in checks:
        result = sandbox.run(check.command, with_acceptance=check.uses_acceptance)
        status = ('unavailable' if result.error else 'timed_out' if result.timed_out else
                  'passed' if result.exit_code == 0 else 'failed')
        output, _ = truncate(result.render(), 4000)
        results.append(CheckResult(check.name, status, result.exit_code, output, result.duration_s))
    return results


def all_passed(results: list[CheckResult]) -> bool:
    return bool(results) and all(result.status == 'passed' for result in results)
