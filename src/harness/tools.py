from dataclasses import dataclass
from typing import Callable
import json
import shlex

from .repo_tools import RepoFiles, ToolDenied
from .sandbox import Sandbox


@dataclass(frozen=True)
class Param:
    name: str
    type: type
    required: bool
    description: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: list[Param]
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools = {tool.name: tool for tool in tools}

    def schemas(self):
        return [{'type': 'function', 'function': {
            'name': tool.name, 'description': tool.description,
            'parameters': {'type': 'object', 'properties': {
                p.name: {'type': 'integer' if p.type is int else 'string', 'description': p.description}
                for p in tool.params}, 'required': [p.name for p in tool.params if p.required]}}}
                for tool in self.tools.values()]

    def validate(self, name, raw_args):
        if name not in self.tools:
            raise ToolDenied(f"unknown tool '{name}'; available: {sorted(self.tools)}")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except ValueError as error:
                raise ToolDenied('arguments must be a JSON object') from error
        if not isinstance(raw_args, dict):
            raise ToolDenied('arguments must be a JSON object')
        params = {p.name: p for p in self.tools[name].params}
        args = dict(raw_args)
        for key in args:
            if key not in params:
                raise ToolDenied(f"unexpected argument '{key}' for tool '{name}'")
        for key, param in params.items():
            if key not in args:
                if param.required:
                    raise ToolDenied(f"missing required argument '{key}' for tool '{name}'")
                continue
            value = args[key]
            if param.type is int and isinstance(value, str) and value.isdecimal():
                value = int(value)
            if type(value) is not param.type:
                raise ToolDenied(f"argument '{key}' must be {param.type.__name__}")
            args[key] = value
        return args

    def execute(self, name, args):
        return self.tools[name].handler(**args)


def build_registry(files: RepoFiles, sandbox: Sandbox, allowed_programs: list[str],
                   approve: Callable[[list[str]], bool]) -> ToolRegistry:
    def run_command(command):
        try:
            argv = shlex.split(command)
        except ValueError as error:
            raise ToolDenied(f'invalid command: {error}') from error
        if not argv:
            raise ToolDenied('command must not be empty')
        if argv[0] not in allowed_programs:
            raise ToolDenied(f"program '{argv[0]}' is not allowed; allowed: {allowed_programs}")
        if approve(argv) is not True:
            raise ToolDenied('command denied by user')
        return sandbox.run(argv).render()

    path = Param('path', str, True, 'Path relative to repository root')
    optional_path = Param('path', str, False, 'Path relative to repository root; defaults to .')
    return ToolRegistry([
        Tool('list_files', 'List files recursively; paths are relative to repository root.', [optional_path], files.list_files),
        Tool('read_file', 'Read numbered text lines; paths are relative to repository root. The prefix through "| " is display only, not file content.', [path, Param('start_line', int, False, 'First line, defaults to 1'), Param('end_line', int, False, 'Last line, maximum 400 lines')], files.read_file),
        Tool('search', 'Search text with a regex; paths are relative to repository root.', [Param('pattern', str, True, 'Regex'), optional_path], files.search),
        Tool('edit_file', 'Replace old_text matching exactly once; paths are relative to repository root. Prefer a small unique snippet, not an entire function.', [path, Param('old_text', str, True, 'Exact file text after the read_file "| " prefix, including only actual indentation; top-level def/class has no leading spaces'), Param('new_text', str, True, 'Replacement text')], files.edit_file),
        Tool('write_file', 'Create or overwrite text; paths are relative to repository root.', [path, Param('content', str, True, 'Full file content')], files.write_file),
        Tool('run_command', 'Run an argv command without a shell in an offline sandbox at /work; requires user approval.', [Param('command', str, True, 'Command using allowed programs: ' + ', '.join(allowed_programs))], run_command),
    ])
