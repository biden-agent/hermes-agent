---
name: opencode
description: Delegate coding tasks to OpenCode CLI agent for feature implementation, refactoring, PR review, and long-running autonomous sessions. Requires the opencode CLI installed and authenticated.
version: 1.2.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Coding-Agent, OpenCode, Autonomous, Refactoring, Code-Review]
    related_skills: [claude-code, codex, hermes-agent]
---

# OpenCode CLI

Use [OpenCode](https://opencode.ai) as an autonomous coding worker orchestrated by Hermes terminal/process tools. OpenCode is a provider-agnostic, open-source AI coding agent with a TUI and CLI.

## When to Use

- User explicitly asks to use OpenCode
- You want an external coding agent to implement/refactor/review code
- You need long-running coding sessions with progress checks
- You want parallel task execution in isolated workdirs/worktrees

## Prerequisites

- OpenCode installed: `npm i -g opencode-ai@latest` or `brew install anomalyco/tap/opencode`
- Auth configured: `opencode auth login` or set provider env vars (OPENROUTER_API_KEY, etc.)
- Verify: `opencode auth list` should show at least one provider
- Git repository for code tasks (recommended)
- `pty=true` for interactive TUI sessions

## Binary Resolution (Important)

Shell environments may resolve different OpenCode binaries. If behavior differs between your terminal and Hermes, check:

```
terminal(command="which -a opencode")
terminal(command="opencode --version")
```

If needed, pin an explicit binary path:

```
terminal(command="$HOME/.opencode/bin/opencode run '...'", workdir="/absolute/path/to/project", pty=true)
```

Prefer absolute `workdir` paths in skills/prompts. They are less brittle than `~/project` when a caller does not expand `~` the way you expect.

## One-Shot Tasks

Use `opencode run` for bounded, non-interactive tasks:

```
terminal(command="opencode run 'Add retry logic to API calls and update tests'", workdir="/absolute/path/to/project")
```

Attach context files with `-f`:

```
terminal(command="opencode run 'Review this config for security issues' -f config.yaml -f .env.example", workdir="/absolute/path/to/project")
```

Show model thinking with `--thinking`:

```
terminal(command="opencode run 'Debug why tests fail in CI' --thinking", workdir="/absolute/path/to/project")
```

Force a specific model:

```
terminal(command="opencode run 'Refactor auth module' --model openrouter/anthropic/claude-sonnet-4", workdir="/absolute/path/to/project")
```

## Interactive Sessions (Background)

For iterative work requiring multiple exchanges, start the TUI in background:

```
terminal(command="opencode", workdir="/absolute/path/to/project", background=true, pty=true)
# Returns session_id

# Send a prompt
process(action="submit", session_id="<id>", data="Implement OAuth refresh flow and add tests")

# Monitor progress
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")

# Send follow-up input
process(action="submit", session_id="<id>", data="Now add error handling for token expiry")

# Exit cleanly — Ctrl+C
process(action="write", session_id="<id>", data="\x03")
# Or just kill the process
process(action="kill", session_id="<id>")
```

**Important:** Do NOT use `/exit` — it is not a valid OpenCode command and will open an agent selector dialog instead. Use Ctrl+C (`\x03`) or `process(action="kill")` to exit.

### TUI Keybindings

| Key | Action |
|-----|--------|
| `Enter` | Submit message (press twice if needed) |
| `Tab` | Switch between agents (build/plan) |
| `Ctrl+P` | Open command palette |
| `Ctrl+X L` | Switch session |
| `Ctrl+X M` | Switch model |
| `Ctrl+X N` | New session |
| `Ctrl+X E` | Open editor |
| `Ctrl+C` | Exit OpenCode |

### Resuming Sessions

After exiting, OpenCode prints a session ID. Resume with:

```
terminal(command="opencode -c", workdir="/absolute/path/to/project", background=true, pty=true)  # Continue last session
terminal(command="opencode -s ses_abc123", workdir="/absolute/path/to/project", background=true, pty=true)  # Specific session
```

## ACP Mode (Preferred for Agent-to-Agent)

OpenCode supports the Agent Client Protocol (ACP) — a structured JSON-RPC protocol for agent-to-agent communication. When Hermes delegates to OpenCode, **prefer `opencode acp` over `opencode run`** for better structure, error handling, and session persistence:

```bash
# Start ACP server on a random port
opencode acp --port 0
```

With ACP, communication is structured (not shell text), sessions persist, and errors are typed. This pairs naturally with Hermes's `delegate_task` tool (`acp_command: "opencode"`).

When calling Hermes `delegate_task`, pass only `acp_command="opencode"` unless you have a very specific CLI override to make. Do **not** force Copilot-style args like `acp_args=["--acp", "--stdio"]` for OpenCode — current OpenCode exposes ACP as the `opencode acp` subcommand, and those flags can make it print help and exit early.

Key flags:
| Flag | Use |
|------|-----|
| `--port 0` | Auto-assign a port (use 0 to avoid conflicts) |
| `--hostname 127.0.0.1` | Bind to localhost only |
| `--mdns` | Enable mDNS service discovery |
| `--cors DOMAIN` | Additional CORS origins |
| `--pure` | Run without external plugins |

## Visibility vs `delegate_task` (Important)

If the user wants to **see OpenCode's intermediate progress/thinking/logs**, prefer launching OpenCode via `terminal(..., background=true)` and monitoring it with `process(action="poll"|"log")`.

Why:
- `delegate_task(acp_command="opencode")` is good for structured delegation
- but it generally returns only the child agent's final summary to the parent
- if the child stalls, is interrupted, or the user wants real-time reassurance, you won't get the step-by-step execution trace in the main chat

Use this pattern for visible progress:

```python
terminal(
    command="opencode run 'Investigate ...' --thinking",
    workdir="/absolute/path/to/project",
    background=True,
    pty=True,
)
# Then stream progress:
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")
```

Use `delegate_task(acp_command="opencode")` when you want isolation and only need the final synthesis.
Use `terminal` + `process` when the user is anxious about progress, wants live status, or you need to inspect intermediate reasoning/log output.

## Common Flags

| Flag | Use |
|------|-----|
| `run 'prompt'` | One-shot execution and exit |
| `--continue` / `-c` | Continue the last OpenCode session |
| `--session <id>` / `-s` | Continue a specific session |
| `--agent <name>` | Choose OpenCode agent (build or plan) |
| `--model provider/model` | Force specific model |
| `--format json` | Machine-readable output/events |
| `--file <path>` / `-f` | Attach file(s) to the message |
| `--thinking` | Show model thinking blocks |
| `--variant <level>` | Reasoning effort (high, max, minimal) |
| `--title <name>` | Name the session |
| `--attach <url>` | Connect to a running opencode server |

## Procedure

1. Verify tool readiness:
   - `terminal(command="opencode --version")`
   - `terminal(command="opencode auth list")`
2. For bounded tasks, use `opencode run '...'` (no pty needed).
3. For iterative tasks, start `opencode` with `background=true, pty=true`.
4. Monitor long tasks with `process(action="poll"|"log")`.
5. If OpenCode asks for input, respond via `process(action="submit", ...)`.
6. Exit with `process(action="write", data="\x03")` or `process(action="kill")`.
7. Summarize file changes, test results, and next steps back to user.

## PR Review Workflow

OpenCode has a built-in PR command:

```
terminal(command="opencode pr 42", workdir="/absolute/path/to/project", pty=true)
```

Or review in a temporary clone for isolation:

```
terminal(command="REVIEW=$(mktemp -d) && git clone https://github.com/user/repo.git $REVIEW && cd $REVIEW && opencode run 'Review this PR vs main. Report bugs, security risks, test gaps, and style issues.' -f $(git diff origin/main --name-only | head -20 | tr '\n' ' ')", pty=true)
```

## Parallel Work Pattern

Use separate workdirs/worktrees to avoid collisions:

```
terminal(command="opencode run 'Fix issue #101 and commit'", workdir="/tmp/issue-101", background=true, pty=true)
terminal(command="opencode run 'Add parser regression tests and commit'", workdir="/tmp/issue-102", background=true, pty=true)
process(action="list")
```

## Session & Cost Management

List past sessions:

```
terminal(command="opencode session list")
```

Check token usage and costs:

```
terminal(command="opencode stats")
terminal(command="opencode stats --days 7 --models anthropic/claude-sonnet-4")
```

## Pitfalls

- Interactive `opencode` (TUI) sessions require `pty=true`. The `opencode run` command does NOT need pty **for one-shot foreground use**.
- **CRITICAL — Background output capture**: If you launch `opencode run` with `background=true` and plan to monitor it via `process(action="poll"|"log")`, you **must** also set `pty=true`. Without a PTY, OpenCode's progress/thinking output may not be captured by the process tool, making it appear stuck when it is actually working. The `process` tool cannot read buffered output from a non-PTY background shell.
- **NEVER redirect output in background mode**: Do NOT use shell redirection like `opencode run '...' > /tmp/log.txt 2>&1` when running in `background=true`. The `process` tool reads from the process's stdout/stderr directly; redirecting to a file makes `process(action="log")` return empty. If you need a persistent log, let OpenCode write its own log file via flags, or tee after the process finishes.
- `/exit` is NOT a valid command — it opens an agent selector. Use Ctrl+C to exit the TUI.
- PATH mismatch can select the wrong OpenCode binary/model config.
- For Hermes ACP delegation, use `delegate_task(acp_command="opencode")`; do not bolt on Copilot-style `--acp --stdio` args unless you have verified the installed OpenCode CLI expects them.
- If OpenCode appears stuck, inspect logs before killing:
  - `process(action="log", session_id="<id>")`
  Check if `pty=true` was set and whether output was redirected away.
- Avoid sharing one working directory across parallel OpenCode sessions.
- Enter may need to be pressed twice to submit in the TUI (once to finalize text, once to send).

## Verification

Smoke test:

```
terminal(command="opencode run 'Respond with exactly: OPENCODE_SMOKE_OK'")
```

Success criteria:
- Output includes `OPENCODE_SMOKE_OK`
- Command exits without provider/model errors
- For code tasks: expected files changed and tests pass

## Rules

1. Prefer `opencode run` for one-shot automation — it's simpler and doesn't need pty.
2. Use interactive background mode only when iteration is needed.
3. Always scope OpenCode sessions to a single repo/workdir.
4. Prefer absolute `workdir` paths in prompts and skill examples.
5. For long tasks, provide progress updates from `process` logs.
6. Report concrete outcomes (files changed, tests, remaining risks).
7. Exit interactive sessions with Ctrl+C or kill, never `/exit`.
