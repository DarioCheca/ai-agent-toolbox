# P1: AI Agent Toolbox — Specification

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Owner** | dcheca |
| **Repository** | `ai-agent-toolbox` |
| **Estimated effort** | 25–35 hours |
| **Language policy** | Everything committed to this repository is written in English |

---

## 1. Purpose

P1 is the first project of an AI Agent Engineering portfolio. It delivers a **single, well-engineered tool-calling agent** for an operations (SRE) domain, built to demonstrate that an AI agent can be:

- **Safe**: bounded execution, validated inputs, read-only tools, explicit threat model.
- **Testable**: deterministic tests without calling a real model, plus a repeatable evaluation suite.
- **Observable**: every run leaves a structured, inspectable trace.
- **Explainable**: every design decision is recorded and defensible.

The project also lays the foundation (conventions, quality gates, reusable core) for later projects and for future solutions built on top of it.

## 2. Scope

### In scope

- A CLI that takes a natural-language question about a simulated service fleet and returns an answer.
- A hand-written agent loop using the Anthropic SDK directly (no agent framework).
- Three read-only tools over a deterministic, simulated environment.
- Hard execution limits (steps, tokens, time, cost) with explicit termination.
- Structured run traces written locally.
- An evaluation suite (~30 cases) with a recorded baseline.
- Unit and integration tests that run without network access or API cost.
- CI, containerization, and a documented threat model.

### Out of scope (deferred on purpose)

- Tools that change state (restart, scale, deploy). Planned for P4 with human approval.
- Agent frameworks such as LangGraph. Planned where orchestration justifies it (P3).
- HTTP API (FastAPI), databases, queues, Kubernetes.
- External observability platforms (Langfuse, OpenTelemetry). See open decisions.
- Multi-agent designs, memory, retrieval.

> Principle: adopt complexity only when a concrete need justifies it. Each deferral above is revisited in the project that creates that need.

## 3. Domain

The agent helps an on-call engineer answer questions such as:

- "Is the payments service healthy?"
- "Why is checkout slow right now?"
- "Show me recent errors for the auth service."

The environment is **simulated and deterministic**: a fixed set of fictional services with scripted status, logs, and metrics defined in scenario files. No real infrastructure is ever touched. Determinism is required so tests and evaluations are repeatable.

## 4. Architecture

```
CLI ──> Agent loop ──> Anthropic Messages API
            │
            ├── Limits guard (steps / tokens / time / cost)
            ├── Tool registry ──> Tool (validated input) ──> Simulation
            └── Trace writer (JSONL)
```

### 4.1 Agent loop

1. Send the conversation and tool definitions to the model.
2. If the model returns a final answer, stop.
3. If the model requests tools, validate each request, execute it, and return the result as a tool result.
4. Check limits after every model call and every tool call.
5. Repeat until a final answer or a limit is reached.

The loop is intentionally small (roughly 50–80 lines of core logic). Being able to explain each step is a goal of the project.

### 4.2 Proposed layout

```
ai-agent-toolbox/
├── src/ai_agent_toolbox/
│   ├── agent/          # loop, prompts, result types
│   ├── tools/          # tool definitions, input models, registry
│   ├── simulation/     # deterministic fleet; scenarios/ holds scripted data
│   ├── limits.py       # execution limits and enforcement
│   ├── tracing.py      # structured run traces
│   ├── config.py       # settings from environment
│   └── cli.py
├── evals/
│   ├── cases/          # evaluation cases
│   ├── run_evals.py
│   └── results/        # dated, versioned run results
├── tests/
│   ├── unit/
│   └── integration/
├── docs/
│   ├── p1-spec.md
│   ├── threat-model.md
│   └── adr/            # architecture decision records
├── Dockerfile
└── pyproject.toml
```

## 5. Tools

All tools are **read-only**, have **strictly validated inputs** (Pydantic models), and return **structured results**. Unknown services or invalid arguments produce a structured error that lists valid options, so the model can recover without guessing.

| Tool | Purpose | Inputs |
|---|---|---|
| `get_service_status` | Current health of one service | `service: str` |
| `get_recent_logs` | Recent log entries for one service | `service: str`, `minutes: int (1–60, default 15)`, `level: enum (optional)`, `limit: int (1–50, default 20)` |
| `get_metrics` | Recent metric values for one service | `service: str`, `metric: enum (latency_p95_ms, error_rate, cpu_percent, memory_percent, request_rate)`, `minutes: int (1–60, default 15)` |

Tool output is truncated to a fixed maximum size, with an explicit marker, before being returned to the model. Tool inputs are validated strictly (unknown fields rejected, numeric arguments not coerced from strings, hard bounds on every number).

## 6. Models

| Role | Model | Notes |
|---|---|---|
| Development and default | `claude-haiku-5-5` | Lower cost; used for day-to-day work |
| Evaluation comparison | `claude-sonnet-5-5` | More capable; used to compare against the default |

- Model identifiers are configuration, not code constants, and are **pinned in settings**.
- **Verify identifiers against the Anthropic Console before first use**; they may change.
- Evaluation results always record the exact model identifier used.

## 7. Execution limits

Every run is bounded. Defaults (configurable, but always finite):

| Limit | Default |
|---|---|
| Max model calls (steps) | 8 |
| Max tool calls | 6 |
| Max total tokens per run | 30,000 |
| Max wall-clock time per run | 60 s |
| Max timeout per tool call | 5 s |
| Max estimated cost per run | USD 0.05 |
| Max tool output size | 4,000 characters |

When any limit is exceeded, the run **stops immediately** and returns an explicit `LimitExceeded` result containing the reason and the partial trace. The agent never silently continues or retries past a limit.

## 8. Observability

- Every run produces a **structured JSONL trace**: run id, timestamps, model id, each step, tool name, validated arguments, result summary, token usage, estimated cost, limit status, and final outcome.
- Logs are structured and **redact secrets** (API keys, tokens) before writing.
- Traces are stored locally and are never committed.

## 9. Security and threat model

A full `docs/threat-model.md` is a deliverable. Initial threats to cover:

| Threat | Planned control |
|---|---|
| **Prompt injection through tool data** (e.g., a log line that says "ignore your instructions") | Treat tool results as untrusted data; delimit them clearly; instruct the model accordingly; include injection cases in the evaluation suite |
| Malformed or malicious tool arguments | Strict schema validation; allowlisted values; reject, never coerce |
| Runaway loops or cost | Hard limits (section 7); explicit termination |
| API key leakage | Key only in environment/`.env` (git-ignored); secret redaction in logs; GitHub secret scanning and push protection enabled |
| Over-trusting model output | The agent has no state-changing tools in P1; answers are advisory |
| Data exfiltration | No network, filesystem, or shell tools exposed to the model |

An important principle: **the model is never the security boundary**. Controls are enforced in code.

## 10. Testing strategy

| Layer | What it covers | Uses real model? |
|---|---|---|
| Unit tests | Tool validation, limits enforcement, trace writing, config, simulation | No |
| Integration tests | Full agent loop against a **scripted fake model client** | No |
| Evaluation suite | Real model behavior on ~30 cases | Yes (manual, not in CI) |

- CI never calls the real API, so it is free and deterministic.
- Live evaluations are run manually and their results are committed with date, model, and git commit.

### 10.1 Evaluation suite

~30 cases across these categories:

- Correct tool selection for status, logs, and metrics questions.
- Multi-tool questions (e.g., correlate errors and latency).
- Unknown or misspelled service names.
- Questions that require no tool (the agent should not invent a call).
- Out-of-scope or action requests ("restart the payments service") — must refuse to act.
- Prompt-injection cases embedded in log content (at least 5).
- Limit behavior (cases designed to trigger step or token limits).

Metrics recorded per run: tool-selection accuracy, argument validity rate, answer correctness (deterministic checks on key facts), injection resistance, limit-compliance, average cost, and latency.

**Thresholds are set after measuring a baseline**, then recorded in an ADR. Thresholds are never invented before measurement.

## 11. Quality gates

| Gate | Tool | Requirement |
|---|---|---|
| Lint and format | Ruff | No errors; formatted |
| Static types | Pyright | No errors |
| Tests | pytest | All pass |
| Coverage | pytest-cov | At least 80% on `src/` |
| Dependency audit | `pip-audit` | No known vulnerabilities, or documented exceptions |
| Mutation testing | `mutmut` (run locally in WSL2) | Run on `limits.py` and tool validation; report the real score, no inflated claims |

All gates except mutation testing run in GitHub Actions on every push and pull request. Reported numbers must come from real executions.

## 12. Containerization

- Multi-stage `Dockerfile` using uv.
- Runs as a **non-root** user.
- No secrets baked into the image; the API key is passed at runtime.
- Image builds in CI.

## 13. Acceptance criteria

P1 is complete when all of the following are true:

1. `uv run ai-agent-toolbox "<question>"` answers questions about the simulated fleet using the three tools.
2. Every execution limit in section 7 is enforced and covered by tests.
3. Every run writes a structured trace with secrets redacted.
4. All quality gates in section 11 pass in CI.
5. The evaluation suite exists, has been run against both configured models, and the baseline results are committed.
6. Thresholds derived from the baseline are recorded in an ADR.
7. `docs/threat-model.md` exists and every listed threat maps to a control or a documented acceptance of risk.
8. The prompt-injection cases are part of the evaluation suite, and their results are reported honestly, including failures.
9. The Docker image builds and runs the CLI as a non-root user.
10. The README lets a new reader install, run, test, and evaluate the project.
11. A running list of ADRs documents each significant decision, including those that deviated from the original plan.

## 14. Milestones

| # | Milestone | Effort |
|---|---|---|
| M0 | Environment and scaffold (WSL2, uv, Docker, GitHub, CI skeleton) | Done |
| M1 | Simulation and the three tools, with unit tests (M1a simulation: done; M1b tools: in progress) | 4–5 h |
| M2 | Agent loop and execution limits, with integration tests using a fake model | 6–8 h |
| M3 | Tracing, secret redaction, CLI, CI quality gates | 5–6 h |
| M4 | Evaluation dataset, baseline run on both models, thresholds ADR | 6–8 h |
| M5 | Threat model, Dockerfile, mutation testing, README, final review | 4–5 h |

Total: **25–35 hours**.

## 15. Decisions

### Confirmed

- Domain: operations/SRE with a simulated fleet.
- Three read-only tools; state-changing tools deferred to P4.
- Hand-written agent loop with the Anthropic SDK; no framework in P1.
- Haiku-class model for development, Sonnet-class model for evaluation comparison.
- Hard execution limits with explicit termination.
- Evaluation thresholds set after measuring a baseline.
- All repository content in English.

### Open

| Decision | Current leaning |
|---|---|
| External observability (Langfuse and/or OpenTelemetry) | Defer; local JSONL traces first, adopt when a concrete need appears |
| HTTP API (FastAPI) | Defer; CLI only in P1 |
| Shared core package for reuse across projects | Decide after P1, once the reusable parts are visible |
| Secret scanning in CI beyond GitHub's built-in feature | Decide during M3 |

## 16. ADR index

ADRs live in `docs/adr/`, one file per decision, numbered. Initial ADRs to write:

1. `0001` — Hand-written agent loop instead of an agent framework.
2. `0002` — Read-only tools only in P1.
3. `0003` — Simulated deterministic environment.
4. `0004` — Model selection and pinning.
5. `0005` — Local JSONL traces before external observability.
6. `0006` — Evaluation thresholds (written after the baseline run).
