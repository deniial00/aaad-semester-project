# Architecture

## Components

```mermaid
flowchart LR
    cli --> controller
    controller --> model[model: OllamaModel / ScriptedModel]
    controller --> tools[tools: ToolRegistry]
    tools --> repo_tools[repo_tools: RepoFiles]
    tools --> sandbox[sandbox: DockerSandbox]
    tools --> approval[cli: terminal_approver]
    cli --> workspace
    cli --> verify
    verify --> sandbox
    cli --> report
    controller --> report
```

## Controller loop and final verification

```mermaid
flowchart TD
    task[CLI task and system prompt] --> step{Step limit?}
    step -- yes --> stop[Stop reason]
    step -- no --> model[Model chat]
    model -- ModelError --> retry{Retries left?}
    retry -- yes --> model
    retry -- no --> stop
    model --> calls{Tool calls?}
    calls -- no: COMPLETED --> stop
    calls -- yes --> limit{Tool-call limit?}
    limit -- reached --> stop
    limit -- available --> validate[Validate tool name and arguments]
    validate -- invalid --> denied[DENIED: increment denied]
    validate -- valid --> command{run_command?}
    command -- no --> execute[Execute file tool]
    command -- yes --> allow{Allowed program?}
    allow -- no --> denied
    allow -- yes --> approve{Human types exact yes?}
    approve -- no / EOF --> denied
    approve -- yes --> sandbox[Execute offline sandbox command]
    execute --> output[Result or ERROR]
    sandbox --> output
    denied --> output
    output --> truncate[Truncate head and tail; append tool observation]
    truncate --> denialLimit{Denied limit?}
    denialLimit -- reached --> stop
    denialLimit -- no --> more{More calls in reply?}
    more -- yes --> limit
    more -- no --> step
    interrupt[Ctrl-C: kill active container] --> interrupted[INTERRUPTED]
    interrupted --> diff[Collect diff]
    stop --> checks[Configured final checks: no approval]
    checks --> diff
    diff --> report[Summary and artifacts]
```

## Sandbox boundary

```mermaid
flowchart LR
    subgraph Host
        harness[Python harness / file tools]
        ollama[Local Ollama]
        git[git clone and diff]
        work[Disposable target checkout]
        acceptance[Independent acceptance assets]
    end
    subgraph Docker["Offline container: no network, read-only root, resource limits"]
        command[pytest / python]
        mountwork["/work: rw"]
        mountaccept["/acceptance: ro, configured checks only"]
        temp["/tmp: tmpfs"]
        command --> mountwork
        command --> mountaccept
        command --> temp
    end
    harness --> ollama
    harness --> git
    harness --> command
    work --> mountwork
    acceptance --> mountaccept
```

Only the image build installs dependencies over the network. Model tools do not receive the acceptance mount. Every command uses a fresh container; the writable target checkout persists across calls. Host file tools guard repository paths; Docker bounds command execution. Final checks are independent of the model completion claim.
