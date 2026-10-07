from dataclasses import dataclass
from typing import Callable
import time

from .config import LimitsConfig
from .limits import RunStats, StopReason, truncate
from .model import ModelClient, ModelError
from .repo_tools import ToolDenied, ToolError
from .tools import ToolRegistry


@dataclass
class RunOutcome:
    stop_reason: StopReason
    final_message: str
    stats: RunStats
    messages: list[dict]


class Controller:
    def __init__(self, model: ModelClient, registry: ToolRegistry, limits: LimitsConfig,
                 on_event: Callable[[str, dict], None] = lambda k, d: None, *, retry_delay_s=2.0):
        self.model = model
        self.registry = registry
        self.limits = limits
        self.on_event = on_event
        self.retry_delay_s = retry_delay_s

    def run(self, task, system_prompt):
        messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': task}]
        stats = RunStats()
        final = ''
        reason = None
        try:
            while reason is None:
                if stats.steps >= self.limits.max_steps:
                    reason = StopReason.STEP_LIMIT
                    break
                retries = 0
                while True:
                    try:
                        reply = self.model.chat(messages, self.registry.schemas())
                        break
                    except ModelError as error:
                        retries += 1
                        stats.retries += 1
                        self.on_event('retry', {'retry': retries, 'error': str(error)})
                        if retries > self.limits.max_model_retries:
                            reason, final = StopReason.MODEL_ERROR, str(error)
                            break
                        time.sleep(self.retry_delay_s)
                if reason is not None:
                    break
                stats.steps += 1
                messages.append({'role': 'assistant', 'content': reply.content, 'tool_calls': [
                    {'function': {'name': call.name, 'arguments': call.arguments}} for call in reply.tool_calls]})
                if not reply.tool_calls:
                    reason, final = StopReason.COMPLETED, reply.content
                    break
                for call in reply.tool_calls:
                    if stats.tool_calls >= self.limits.max_tool_calls:
                        reason = StopReason.TOOL_CALL_LIMIT
                        break
                    stats.tool_calls += 1
                    self.on_event('tool_call', {'step': stats.steps, 'name': call.name, 'arguments': call.arguments})
                    ok = False
                    try:
                        args = self.registry.validate(call.name, call.arguments)
                        output = self.registry.execute(call.name, args)
                        stats.executed += 1
                        ok = True
                    except ToolDenied as error:
                        stats.denied += 1
                        output = f'DENIED: {error}'
                        self.on_event('denied', {'name': call.name, 'message': str(error)})
                    except ToolError as error:
                        stats.tool_errors += 1
                        output = f'ERROR: {error}'
                    except Exception as error:
                        stats.tool_errors += 1
                        output = f'ERROR: internal {type(error).__name__}: {error}'
                    output, clipped = truncate(output, self.limits.max_output_chars)
                    messages.append({'role': 'tool', 'content': output, 'tool_name': call.name})
                    self.on_event('tool_result', {'name': call.name, 'chars': len(output), 'truncated': clipped, 'ok': ok})
                    if stats.denied >= self.limits.max_denied:
                        reason = StopReason.DENIED_LIMIT
                        break
        except KeyboardInterrupt:
            reason = StopReason.INTERRUPTED
        self.on_event('stop', {'reason': reason.value})
        return RunOutcome(reason, final, stats, messages)
