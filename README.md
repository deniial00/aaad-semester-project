# Stage 1 coding harness

A Python CLI that asks a local Ollama model to fix a bug in a disposable Cosmic Python checkout. A hand-written controller validates native tool calls, applies repository edits, runs approved commands in offline Docker containers, and independently verifies the resulting patch. No agent framework or JSON-in-content protocol.

## Setup

Prerequisites: Python 3.11 or later, git, Docker Desktop running, and Ollama. The default `qwen3:8b` model needs several GB of storage and RAM.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp harness.example.toml harness.toml
ollama serve
```

In another terminal:

```sh
ollama pull qwen3:8b
docker build -t harness-sandbox-cosmic:latest -f sandbox/cosmic.Dockerfile sandbox/
```

Only dependency installation, image build, initial repository clone/fetch, and model download need network access. Model-requested commands never do. `OLLAMA_HOST` overrides `model.host`.

## Tools and configuration

File tools: `list_files`, numbered `read_file` (400 lines), regex `search` (100 matches), exact unique-match `edit_file`, and create-or-overwrite `write_file`. Paths are repository-relative. `run_command` uses `shlex.split`, not a shell; shell operators are ordinary arguments.


| Key                                                     | Purpose                                                     |
| ------------------------------------------------------- | ----------------------------------------------------------- |
| `model.name`, `host`, `temperature`, `num_ctx`, `think` | Local model and inference settings                          |
| `target.repo_url`, `commit`, `acceptance_dir`           | Pinned input and independent acceptance assets              |
| `sandbox.image`, `allowed_programs`                     | Image and executable allowlist (`pytest`, `python`)         |
| `sandbox.command_timeout_s`                             | Wall-clock deadline per container                           |
| `sandbox.memory`, `cpus`, `pids_limit`, `env`           | Resource limits and explicit container environment          |
| `limits.max_steps`                                      | Maximum successful model replies                            |
| `limits.max_tool_calls`                                 | Maximum requested calls, including denials                  |
| `limits.max_denied`                                     | Stop after this many rejected requests                      |
| `limits.max_model_retries`                              | Retries per failed model step                               |
| `limits.max_output_chars`                               | Head/tail tool output retained, plus truncation marker      |
| `checks[].name`, `command`, `uses_acceptance`           | Independent final argv checks and optional acceptance mount |


## Run

```sh
.venv/bin/harness check --config harness.toml
.venv/bin/harness run --config harness.toml --task-file tasks/cosmic-invalid-batchref.md
```

The baseline check deliberately exits 1: regression passes, acceptance fails on the unknown-batch-reference bug. The target is pinned to `14c84797ffa77255d53cf1a02fe6aafda2b68aeb`. See [acceptance contract](acceptance/cosmic/README.md).

Before **every model-requested `run_command`**, the CLI prints the full command and `/work` sandbox context. Read it, then type exactly `yes` at `Approve this command only? Type yes:`. Any other response, including EOF or empty input, denies execution. A denial counts toward `max_denied` and is returned to the model. Disallowed commands are rejected before prompting. Configured final checks and `harness check` need no approval: they are user-defined, not model-requested. Approval never persists for the next command.

Progress shows tool requests, denials, results, retries, and stop reasons. The final report lists counters, changed files, the complete diff, independent check outcomes, and an explicit result. A model saying “tests pass” does not override a failed check. Exit codes: 0 completed with all checks passed; 1 failed/limited run; 2 configuration/workspace/preflight error; 130 interrupted.

Each run uses `runs/<id>/repo` and writes:

- `transcript.jsonl`: system, task, assistant/tool messages (may include source and task text);
- `changes.diff`: staged diff, including new files;
- `summary.json`: stop reason, final message, counters, changed files, check results.

```sh
.venv/bin/harness check --config harness.toml --workspace runs/<id>/repo
```

Ctrl-C kills the active container and ends the run. Interrupted agent runs retain their diff/transcript and skip final checks.

## Isolation and limits

Git stays on the host. Target workspaces have no remote; the harness never edits the source repository. File tools reject absolute paths, escapes (including symlinks), and `.git`. No read-only/edit modes, protected-path rules, or create-only write policy.

Containers use `--network none`, a read-only root, writable `/tmp`, dropped capabilities, `no-new-privileges`, and CPU/memory/process limits. Only the disposable `/work` is writable. Independent `/acceptance` is mounted read-only **only during final checks**. No host home directory or credentials are mounted; only configured environment variables are forwarded. stdout/stderr spool to temporary files and are bounded when read.

This is a POC boundary, not a hardened hostile-code service. Allowlisted Python can execute arbitrary code inside its container and modify `/work`; it can also alter target tests. Independent acceptance mitigates that, but target edits can still affect imports during checks. Docker shares a kernel with its VM; disk spooling is not disk-quota enforcement. File checks are not race-proof against a concurrent malicious writer. Configs are trusted. There is no context compaction until Stage 2.

## Tests and deterministic use

```sh
.venv/bin/pytest -m 'not docker' -q
.venv/bin/pytest -q
```

Core tests require neither Docker nor Ollama. Docker tests require the built image and running daemon; the bug-fix test needs network on its first clone. It proves acceptance fails before the reference patch and passes afterward, with regression passing in both states.

`harness.model.ScriptedModel` allows deterministic controller experiments:

```python
from harness.model import ScriptedModel, ModelReply, ToolCall
model = ScriptedModel([
    ModelReply('', [ToolCall('read_file', {'path': 'src/allocation/service_layer/handlers.py'})]),
    ModelReply('Inspection complete', []),
])
# Supply model to Controller alongside a registry, limits, and a system prompt.
```

Architecture: [components, loop, and sandbox boundary](docs/architecture.md).

Lab 1 informed typed approval, observation-based error handling, Ctrl-C behavior, and escape/.git test cases. Its urllib adapter, JSON protocol, modes, protected paths, create-only writes, and fetch tool are not reused.

## Model and runtime troubleshooting

The approved example retains temperature `0.0`, `num_ctx = 32768`, and non-thinking mode. On this 16 GB machine, initial `qwen3:8b` runs repeated malformed exact-match edits. `qwen2.5-coder:7b` emitted tool-looking prose instead of native tool calls; that prose was not executed, and independent acceptance failed.

[Qwen3's checkpoint guidance](https://huggingface.co/Qwen/Qwen3-8B#best-practices) warns against greedy decoding in thinking mode because it can cause endless repetition, and recommends temperature `0.7` for non-thinking mode. The local guided demo uses `qwen3:8b`, `think = false`, `temperature = 0.7`, `num_ctx = 8192`, and `max_tool_calls = 60`. The smaller context reduces model memory; long conversations can still exceed it. To reproduce these settings after copying the example, edit the corresponding keys in `harness.toml`.

Manual assistance: the task supplies exact minimal replacements for the exception class, handler guard, consumer import/catch, and new unit test after models repeatedly generated incorrect indentation or destructive test rewrites. Tool descriptions and the system prompt explain numbered-line prefixes and exact indentation. This verifies a human-guided real-model tool loop, not an autonomous bug-fix benchmark. No target code is hand-patched for real-model runs, and exact-match validation is not relaxed.

Cancellation kills the container and force-removes it, including a container interrupted in Docker's `Created` state. A wedged Docker daemon can still prevent cleanup; recover Docker Desktop before rerunning. Restarting Docker interrupts other running containers and needs the machine owner's approval.

## Verification receipt


| Scenario                                      | Observed result                                                                     |
| --------------------------------------------- | ----------------------------------------------------------------------------------- |
| Core suite, no Docker/Ollama required         | 52 passed                                                                           |
| Complete suite after startup-cancellation fix | 59 passed, no skips                                                                 |
| Baseline CLI check                            | Regression passed; acceptance failed; exit 1                                        |
| Guided real-model run                         | 21 target unit tests and 3 independent acceptance tests passed; exit 0              |
| Controlled CLI interrupt                      | Observed Docker start event, then Ctrl-C; exit 130; no remaining harness containers |


Successful local run: `runs/20261007-194714-183334/`. It made only the three task-authorized changes, used 10 model steps and 9 native tool calls, and reported zero denials, tool errors, or model retries. `InvalidSku` and all existing unit tests were preserved. The checkout has no git remote; `summary.json`, `changes.diff`, and `transcript.jsonl` were emitted. Runs are ignored local artifacts, not submission files.

The interrupt smoke used `ScriptedModel` to request a 60-second sandbox sleep through the actual CLI, with individual human approval. The bug-fix run used real Ollama. Docker Desktop needed an approved restart during verification after container startup wedged; normal shutdown timed out, so its application processes were force-quit and relaunched.

## Demo recording

Record at most four minutes: show the baseline failing acceptance, run the task with a real model, read/approve requested commands, and show the diff, independent checks, and artifacts. The video and repository push are submission actions performed by the user.