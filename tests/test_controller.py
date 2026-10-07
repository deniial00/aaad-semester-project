from dataclasses import replace
from unittest.mock import Mock

import pytest

from harness.cli import terminal_approver
from harness.config import CheckConfig, LimitsConfig
from harness.controller import Controller
from harness.limits import StopReason, truncate
from harness.model import ModelError, ModelReply, ScriptedModel, ToolCall
from harness.repo_tools import RepoFiles
from harness.report import print_summary
from harness.sandbox import CommandResult
from harness.tools import Tool, ToolRegistry, build_registry
from harness.verify import all_passed, run_checks


DEFAULT_LIMITS = LimitsConfig(30, 40, 5, 2, 8000)


class FakeSandbox:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    def run(self, argv, *, with_acceptance=False):
        self.calls.append((list(argv), with_acceptance))
        if not self.results:
            raise AssertionError("unexpected sandbox call")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def command_result(*, exit_code=0, stdout="", stderr="", timed_out=False, error=None):
    return CommandResult(["pytest", "-q"], exit_code, stdout, stderr, timed_out, 0.1, error)


def tool_reply(name, arguments):
    return ModelReply("", [ToolCall(name, arguments)])


def observations(outcome):
    return [message for message in outcome.messages if message["role"] == "tool"]


@pytest.fixture
def setup_run(tmp_path):
    def setup(replies, *, results=(), approve=None, limits=DEFAULT_LIMITS, repeat_last=False):
        files = RepoFiles(tmp_path)
        read_spy = Mock(wraps=files.read_file)
        files.read_file = read_spy
        sandbox = FakeSandbox(results)
        approval = approve if approve is not None else Mock(return_value=True)
        registry = build_registry(files, sandbox, ["pytest", "python"], approve=approval)
        model = ScriptedModel(replies, repeat_last=repeat_last)
        events = []
        controller = Controller(model, registry, limits,
                                lambda kind, data: events.append((kind, data)), retry_delay_s=0)
        return controller, model, sandbox, approval, read_spy, events
    return setup


def test_read_then_done_delivers_file_observation(tmp_path, setup_run):
    (tmp_path / "a.py").write_text("answer = 42\n", encoding="utf-8")
    controller, model, sandbox, approval, read_spy, events = setup_run([
        tool_reply("read_file", {"path": "a.py"}), ModelReply("done", [])])

    outcome = controller.run("inspect the file", "system instructions")

    assert outcome.stop_reason == StopReason.COMPLETED
    assert outcome.final_message == "done"
    assert outcome.stats.steps == 2
    assert outcome.stats.executed == outcome.stats.tool_calls == 1
    assert outcome.stats.denied == outcome.stats.tool_errors == 0
    assert model.calls[0] == [{"role": "system", "content": "system instructions"},
                              {"role": "user", "content": "inspect the file"}]
    message = model.calls[1][-1]
    assert message["role"] == "tool"
    assert message["tool_name"] == "read_file"
    assert "1| answer = 42" in message["content"]
    read_spy.assert_called_once_with(path="a.py")
    approval.assert_not_called()
    assert sandbox.calls == []
    assert events[-1] == ("stop", {"reason": "completed"})


@pytest.mark.parametrize("name,args,prefix", [
    ("rm_rf", {}, "DENIED: unknown tool 'rm_rf'"),
    ("read_file", {}, "DENIED: missing required argument 'path'"),
    ("read_file", {"path": "a.py", "extra": 1}, "DENIED: unexpected argument 'extra'"),
    ("read_file", {"path": "a.py", "start_line": "abc"}, "DENIED: argument 'start_line' must be int"),
    ("read_file", {"path": "a.py", "start_line": True}, "DENIED: argument 'start_line' must be int"),
    ("read_file", "not json", "DENIED: arguments must be a JSON object"),
    ("read_file", "[]", "DENIED: arguments must be a JSON object"),
    ("read_file", [], "DENIED: arguments must be a JSON object"),
    ("read_file", None, "DENIED: arguments must be a JSON object"),
    ("run_command", {"command": "git push origin main"}, "DENIED: program 'git' is not allowed"),
    ("run_command", {"command": "git status"}, "DENIED: program 'git' is not allowed"),
    ("run_command", {"command": ""}, "DENIED: command must not be empty"),
    ("run_command", {"command": "pytest '"}, "DENIED: invalid command:"),
])
def test_invalid_requests_never_reach_file_handler_or_sandbox(setup_run, name, args, prefix):
    controller, model, sandbox, approval, read_spy, _ = setup_run([
        tool_reply(name, args), ModelReply("done", [])])

    outcome = controller.run("task", "system")

    assert outcome.stop_reason == StopReason.COMPLETED
    assert outcome.stats.denied == outcome.stats.tool_calls == 1
    assert outcome.stats.executed == outcome.stats.tool_errors == 0
    assert observations(outcome)[0]["content"].startswith(prefix)
    assert model.calls[1][-1] == observations(outcome)[0]
    read_spy.assert_not_called()
    approval.assert_not_called()
    assert sandbox.calls == []


def test_json_object_and_decimal_line_number_are_accepted(tmp_path, setup_run):
    (tmp_path / "a.py").write_text("first\nsecond\n", encoding="utf-8")
    controller, _, _, _, read_spy, _ = setup_run([
        tool_reply("read_file", '{"path": "a.py", "start_line": "2"}'),
        ModelReply("done", [])])
    outcome = controller.run("task", "system")
    assert outcome.stats.executed == 1
    assert "2| second" in observations(outcome)[0]["content"]
    read_spy.assert_called_once_with(path="a.py", start_line=2)


def test_approval_denial_counts_without_execution(setup_run):
    approval = Mock(return_value=False)
    controller, model, sandbox, _, _, _ = setup_run([
        tool_reply("run_command", {"command": "pytest -q"}), ModelReply("done", [])], approve=approval)
    outcome = controller.run("task", "system")
    approval.assert_called_once_with(["pytest", "-q"])
    assert sandbox.calls == []
    assert outcome.stats.denied == outcome.stats.tool_calls == 1
    assert outcome.stats.executed == outcome.stats.tool_errors == 0
    assert observations(outcome)[0]["content"] == "DENIED: command denied by user"
    assert model.calls[1][-1] == observations(outcome)[0]


@pytest.mark.parametrize("answer,expected", [("yes", True), ("y", False), ("YES", False),
                                             ("", False), (" yes", False), ("yes ", False),
                                             (EOFError(), False)])
def test_terminal_approver_requires_exact_yes(tmp_path, monkeypatch, capsys, answer, expected):
    input_spy = Mock(side_effect=answer) if isinstance(answer, EOFError) else Mock(return_value=answer)
    monkeypatch.setattr("builtins.input", input_spy)
    assert terminal_approver(tmp_path)(["python", "-c", "print('hello')"]) is expected
    input_spy.assert_called_once_with("Approve this command only? Type yes: ")
    output = capsys.readouterr().out
    assert "Command: python -c " in output
    assert "print" in output and "hello" in output
    assert f"cwd: /work (sandbox copy of {tmp_path})" in output
    assert f"approval: {'approved' if expected else 'denied'}" in output


@pytest.mark.parametrize("result,status,label", [
    (command_result(exit_code=1, stdout="1 failed"), "failed", "[FAILED]"),
    (command_result(exit_code=125, error="docker unavailable"), "unavailable", "[UNAVAILABLE]"),
])
def test_failed_checks_override_model_success_claim(setup_run, capsys, result, status, label):
    controller, model, sandbox, approval, _, _ = setup_run([
        tool_reply("run_command", {"command": "pytest -q"}),
        ModelReply("All tests pass", [])], results=[result, result, command_result(stdout="3 passed")])
    outcome = controller.run("task", "system")
    assert outcome.stop_reason == StopReason.COMPLETED
    assert outcome.final_message == "All tests pass"
    assert outcome.stats.executed == 1
    assert outcome.stats.tool_errors == outcome.stats.denied == 0
    assert f"exit_code: {result.exit_code}" in model.calls[1][-1]["content"]
    assert (result.error or result.stdout) in model.calls[1][-1]["content"]

    checks = run_checks(sandbox, [CheckConfig("regression", ["pytest", "tests/unit", "-q"]),
                                 CheckConfig("acceptance", ["pytest", "/acceptance", "-q"], True)])
    assert [check.status for check in checks] == [status, "passed"]
    assert not all_passed(checks)
    assert sandbox.calls == [(["pytest", "-q"], False),
                             (["pytest", "tests/unit", "-q"], False),
                             (["pytest", "/acceptance", "-q"], True)]
    approval.assert_called_once_with(["pytest", "-q"])
    print_summary(outcome, [], "", checks, limits=DEFAULT_LIMITS)
    output = capsys.readouterr().out
    assert "model final message: All tests pass" in output
    assert label in output
    assert (result.error or result.stdout) in output
    assert "RESULT: CHECKS NOT PASSED" in output
    assert "RESULT: ALL CHECKS PASSED" not in output


def test_check_timeout_and_empty_results_are_not_successful():
    sandbox = FakeSandbox([command_result(exit_code=None, timed_out=True)])
    checks = run_checks(sandbox, [CheckConfig("slow", ["pytest", "-q"])])
    assert checks[0].status == "timed_out"
    assert not all_passed(checks)
    assert not all_passed([])
    passed = run_checks(FakeSandbox([command_result()]), [CheckConfig("unit", ["pytest"])])
    assert all_passed(passed)


@pytest.mark.parametrize("changes,name,reason,count", [
    ({"max_tool_calls": 3}, "list_files", StopReason.TOOL_CALL_LIMIT, 3),
    ({"max_steps": 2}, "list_files", StopReason.STEP_LIMIT, 2),
    ({"max_denied": 2}, "unknown", StopReason.DENIED_LIMIT, 2),
])
def test_repeated_calls_stop_at_configured_limits(setup_run, changes, name, reason, count):
    controller, _, sandbox, _, _, events = setup_run([tool_reply(name, {})],
        limits=replace(DEFAULT_LIMITS, **changes), repeat_last=True)
    outcome = controller.run("task", "system")
    assert outcome.stop_reason == reason
    assert outcome.stats.tool_calls == count
    assert len(observations(outcome)) == count
    assert outcome.stats.executed == (0 if name == "unknown" else count)
    assert outcome.stats.denied == (count if name == "unknown" else 0)
    if reason == StopReason.STEP_LIMIT:
        assert outcome.stats.steps == 2
    assert sandbox.calls == []
    assert events[-1] == ("stop", {"reason": reason.value})


def test_tool_budget_stops_inside_multi_call_reply(setup_run):
    controller, model, sandbox, approval, _, events = setup_run([
        ModelReply("", [ToolCall("run_command", {"command": f"pytest test_{n}.py"})
                        for n in range(4)])],
        results=[command_result(), command_result(), command_result()],
        limits=replace(DEFAULT_LIMITS, max_tool_calls=3))
    outcome = controller.run("task", "system")
    assert outcome.stop_reason == StopReason.TOOL_CALL_LIMIT
    assert outcome.stats.steps == 1
    assert outcome.stats.executed == outcome.stats.tool_calls == 3
    assert len(model.calls) == 1
    assert len(observations(outcome)) == 3
    assert sandbox.calls == [(["pytest", f"test_{n}.py"], False) for n in range(3)]
    assert approval.call_count == 3
    assert len([event for event in events if event[0] == "tool_call"]) == 3


def test_output_limit_retains_head_and_tail_for_model(setup_run):
    controller, model, _, _, _, events = setup_run([tool_reply("large_output", {}), ModelReply("done", [])],
        limits=replace(DEFAULT_LIMITS, max_output_chars=1000))
    text = "H" + "x" * 49998 + "T"
    controller.registry = ToolRegistry([Tool("large_output", "Return large data", [], lambda: text)])
    outcome = controller.run("task", "system")
    output = observations(outcome)[0]["content"]
    marker = "\n…[output truncated: 49000 of 50000 chars omitted]…\n"
    assert output == text[:500] + marker + text[-500:]
    assert len(output) <= 1000 + len(marker)
    assert output.startswith("H") and output.endswith("T")
    assert model.calls[1][-1]["content"] == output
    event = next(data for kind, data in events if kind == "tool_result")
    assert event == {"name": "large_output", "chars": len(output), "truncated": True, "ok": True}


@pytest.mark.parametrize("text,max_chars", [("abc", 3), ("ab", 3), ("", 1)])
def test_truncate_at_or_below_boundary_is_unchanged(text, max_chars):
    assert truncate(text, max_chars) == (text, False)


@pytest.mark.parametrize("error_count,reason", [(2, StopReason.COMPLETED), (3, StopReason.MODEL_ERROR)])
def test_model_retries_and_exhaustion(setup_run, error_count, reason):
    controller, model, sandbox, _, _, events = setup_run(
        [ModelError(f"offline {n}") for n in range(error_count)] + [ModelReply("recovered", [])])
    outcome = controller.run("task", "system")
    assert outcome.stop_reason == reason
    assert outcome.stats.retries == error_count
    assert outcome.stats.steps == (1 if reason == StopReason.COMPLETED else 0)
    assert outcome.final_message == ("recovered" if reason == StopReason.COMPLETED else "offline 2")
    assert len(model.calls) == error_count + (reason == StopReason.COMPLETED)
    assert [data["retry"] for kind, data in events if kind == "retry"] == list(range(1, error_count + 1))
    assert sandbox.calls == []


@pytest.mark.parametrize("interrupt_at", ["model", "approval", "sandbox"])
def test_keyboard_interrupt_stops_without_reraising(setup_run, monkeypatch, interrupt_at):
    approval = Mock(side_effect=KeyboardInterrupt()) if interrupt_at == "approval" else Mock(return_value=True)
    controller, model, sandbox, _, _, events = setup_run([
        tool_reply("run_command", {"command": "pytest -q"})],
        approve=approval, results=[KeyboardInterrupt()])
    if interrupt_at == "model":
        monkeypatch.setattr(model, "chat", Mock(side_effect=KeyboardInterrupt()))
    outcome = controller.run("task", "system")
    assert outcome.stop_reason == StopReason.INTERRUPTED
    assert outcome.stats.executed == outcome.stats.denied == outcome.stats.tool_errors == 0
    assert outcome.stats.tool_calls == (0 if interrupt_at == "model" else 1)
    assert sandbox.calls == ([] if interrupt_at != "sandbox" else [(["pytest", "-q"], False)])
    assert events[-1] == ("stop", {"reason": "interrupted"})
