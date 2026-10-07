from pathlib import Path
import os
import re


class ToolDenied(Exception):
    pass


class ToolError(Exception):
    pass


SKIP = {".git", "__pycache__", ".venv", ".pytest_cache"}


class RepoFiles:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, path: str) -> Path:
        raw = Path(path)
        if not path or raw.is_absolute():
            raise ToolDenied(f"path '{path}' is outside the allowed repository")
        try:
            resolved = (self.root / raw).resolve()
        except (OSError, RuntimeError) as error:
            raise ToolDenied(f"path '{path}' cannot be resolved: {error}") from error
        if not resolved.is_relative_to(self.root):
            raise ToolDenied(f"path '{path}' is outside the allowed repository")
        if '.git' in raw.parts or '.git' in resolved.relative_to(self.root).parts:
            raise ToolDenied(f"path '{path}': .git is not accessible")
        return resolved

    def _walk(self, target: Path, max_depth=None):
        if not target.exists():
            raise ToolError(f"path does not exist: {target.relative_to(self.root)}")
        if target.is_file():
            yield target
            return
        for current, dirs, files in os.walk(target, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            parent = Path(current)
            for name in sorted(dirs + files):
                if name in SKIP:
                    continue
                entry = parent / name
                depth = len(entry.relative_to(target).parts)
                if max_depth is not None and depth > max_depth:
                    continue
                try:
                    self.resolve(entry.relative_to(self.root).as_posix())
                except ToolDenied:
                    if name in dirs:
                        dirs.remove(name)
                    continue
                yield entry
            if max_depth is not None and len(parent.relative_to(target).parts) + 1 >= max_depth:
                dirs[:] = []

    def list_files(self, path='.', *, max_depth: int | None = None) -> str:
        target = self.resolve(path)
        try:
            entries = sorted(p.relative_to(self.root).as_posix() + ('/' if p.is_dir() else '') for p in self._walk(target, max_depth))
        except OSError as error:
            raise ToolError(str(error)) from error
        shown = entries[:500]
        if len(entries) > 500:
            shown.append(f"… ({len(entries) - 500} more entries)")
        return '\n'.join(shown)

    def _text(self, target: Path) -> str:
        try:
            if not target.is_file():
                raise ToolError(f"not a file: {target.relative_to(self.root)}")
            if target.stat().st_size > 1_000_000:
                raise ToolError('file exceeds 1 MB')
            raw = target.read_bytes()
            if b'\x00' in raw:
                raise ToolError('binary file is not readable')
            return raw.decode('utf-8', errors='replace')
        except OSError as error:
            raise ToolError(str(error)) from error

    def read_file(self, path, start_line=1, end_line=None):
        target = self.resolve(path)
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ToolDenied('line range must start at 1 or later and end after its start')
        lines = self._text(target).splitlines()
        end = min(end_line if end_line is not None else start_line + 399, start_line + 399, len(lines))
        numbered = [f"{n:>5}| {lines[n - 1]}" for n in range(start_line, end + 1)]
        numbered.append(f"[lines {start_line}-{end} of {len(lines)}]")
        return '\n'.join(numbered)

    def search(self, pattern, path='.'):
        try:
            regex = re.compile(pattern)
        except re.error as error:
            raise ToolDenied(f"invalid regex: {error}") from error
        target = self.resolve(path)
        matches = []
        try:
            for entry in self._walk(target):
                if not entry.is_file():
                    continue
                try:
                    text = self._text(entry)
                except ToolError:
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        matches.append(f"{entry.relative_to(self.root).as_posix()}:{n}: {line[:300]}")
                        if len(matches) == 100:
                            return '\n'.join(matches + ['[match limit reached]'])
        except OSError as error:
            raise ToolError(str(error)) from error
        return '\n'.join(matches) or 'no matches'

    def edit_file(self, path, old_text, new_text):
        target = self.resolve(path)
        content = self._text(target)
        count = content.count(old_text)
        if count == 0:
            raise ToolError(f"old_text not found in {path}")
        if count > 1:
            raise ToolError(f"old_text matches {count} times in {path}; include more surrounding lines")
        try:
            target.write_text(content.replace(old_text, new_text, 1), encoding='utf-8')
        except OSError as error:
            raise ToolError(str(error)) from error
        return f"edited {path}"

    def write_file(self, path, content):
        if len(content) > 200_000:
            raise ToolDenied('content exceeds 200000 characters')
        target = self.resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
        except OSError as error:
            raise ToolError(str(error)) from error
        return f"wrote {path} ({len(content)} chars)"
