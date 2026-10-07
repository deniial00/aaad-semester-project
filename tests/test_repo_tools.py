import pytest
from harness.repo_tools import RepoFiles, ToolDenied, ToolError


def test_file_operations(tmp_path):
    files = RepoFiles(tmp_path)
    files.write_file('a.py', '# header\ndef foo():\n    pass\n')
    assert '    2| def foo():' in files.read_file('a.py')
    assert files.search('def foo') == 'a.py:2: def foo():'
    files.edit_file('a.py', 'def foo():', 'def bar():')
    assert 'def bar():' in (tmp_path / 'a.py').read_text()
    files.write_file('nested/x.py', 'x')
    assert files.list_files() == 'a.py\nnested/\nnested/x.py'
    assert files.list_files(max_depth=1) == 'a.py\nnested/'


@pytest.mark.parametrize('old', ['missing', 'same'])
def test_edit_requires_unique_match(tmp_path, old):
    path = tmp_path / 'a.py'
    path.write_text('same same')
    with pytest.raises(ToolError):
        RepoFiles(tmp_path).edit_file('a.py', old, 'new')
    assert path.read_text() == 'same same'


def test_path_guards(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('secret')
    (root / 'link').symlink_to(outside)
    (root / '.git').mkdir()
    (root / '.git/config').write_text('secret')
    (root / 'gitlink').symlink_to(root / '.git')
    files = RepoFiles(root)
    for path in ('../outside.txt', '/etc/passwd', 'link', '.git/config', 'gitlink/config', ''):
        with pytest.raises(ToolDenied):
            files.read_file(path)
    with pytest.raises(ToolDenied):
        files.write_file('../x.py', 'oops')
    assert not (tmp_path / 'x.py').exists()
    assert 'secret' not in files.search('secret')


def test_read_limits_and_binary(tmp_path):
    files = RepoFiles(tmp_path)
    (tmp_path / 'long').write_text('\n'.join(str(n) for n in range(500)))
    text = files.read_file('long')
    assert '[lines 1-400 of 500]' in text
    assert '  401|' not in text
    (tmp_path / 'binary').write_bytes(b'a\x00b')
    with pytest.raises(ToolError):
        files.read_file('binary')
    with pytest.raises(ToolDenied):
        files.search('[')
