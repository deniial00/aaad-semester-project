from dataclasses import dataclass
from enum import StrEnum


class StopReason(StrEnum):
    COMPLETED = "completed"
    STEP_LIMIT = "step_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    DENIED_LIMIT = "denied_limit"
    MODEL_ERROR = "model_error"
    INTERRUPTED = "interrupted"


@dataclass
class RunStats:
    steps: int = 0
    tool_calls: int = 0
    executed: int = 0
    denied: int = 0
    tool_errors: int = 0
    retries: int = 0


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    half = max_chars // 2
    omitted = len(text) - 2 * half
    marker = f"\n…[output truncated: {omitted} of {len(text)} chars omitted]…\n"
    return text[:half] + marker + (text[-half:] if half else ""), True
