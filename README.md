# PythonAPIWithClaude

A proof of concept API to explore Claude interfacing (Python). Primarily built using AI directives.

A [FastAPI](https://fastapi.tiangolo.com/) service that takes a user's question, sends it to
Claude through the official [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python),
and returns Claude's answer. It sends traces, metrics and logs to an OpenTelemetry (OTEL) collector.

- **Port:** `56601`
- **Default model:** `claude-opus-5-5` (Claude Opus 5.5)
- **Python:** 3.12 or later (developed on 3.14)

---

## Project layout

```
PythonAPIWithClaude/
├── app/
│   ├── main.py                 # App factory (create_app), lifespan, uvicorn entry point
│   ├── config.py               # Settings from env vars / .env (pydantic-settings)
│   ├── schemas.py              # Request/response models
│   ├── telemetry.py            # OTEL traces, metrics and logs → OTLP collector
│   ├── api/
│   │   ├── dependencies.py     # Dependency injection (ClaudeService)
│   │   ├── errors.py           # Maps SDK errors to HTTP status codes
│   │   └── routes/
│   │       ├── health.py       # GET  /health
│   │       └── questions.py    # POST /api/v1/questions
│   └── services/
│       └── claude_service.py   # Calls Claude; adds GenAI spans and token metrics
├── tests/                      # pytest suite (no network: Claude and OTEL are mocked)
│   └── Bruno/                  # Bruno API tests, run against the running service
├── docs/                       # Generation prompts
├── .env.example                # Template for local configuration
└── pyproject.toml              # Dependencies and tool config (pytest, coverage, ruff)
```

---

## Setup

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1            # PowerShell  (Git Bash: source .venv/Scripts/activate)

# 2. Install the app plus dev tools
pip install -e ".[dev]"

# 3. Configure
Copy-Item .env.example .env           # then set ANTHROPIC_API_KEY in .env
```

### Getting a Claude API key

1. Sign in at the [Claude Console](https://platform.claude.com/) and create an API key.
2. Put it in `.env` as `ANTHROPIC_API_KEY=...`, or set it as an environment variable.
   The app reads it from either place and passes it to the SDK. It is held as a secret and never logged.

If no credentials are found, `POST /api/v1/questions` returns `503` with a message telling you
to set `ANTHROPIC_API_KEY`.

### Configuration

All settings come from environment variables or `.env` (see [.env.example](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key (from `.env` or the environment) |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model ID |
| `CLAUDE_MAX_TOKENS` | `16000` | Maximum answer length in tokens |
| `CLAUDE_EFFORT` | `medium` | `low` / `medium` / `high` / `xhigh` / `max`: how much effort Claude spends (and how many tokens it uses) |
| `CLAUDE_TIMEOUT_SECONDS` | `120` | Timeout for each request |
| `CLAUDE_MAX_RETRIES` | `2` | SDK retries for 408/409/429/5xx and connection errors |
| `CLAUDE_REFUSAL_FALLBACK` | `true` | Use the server-side refusal fallback (see below) |
| `CLAUDE_SYSTEM_PROMPT` | built-in | System prompt sent with every question |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `56601` | Bind address |
| `LOG_LEVEL` | `INFO` | Python log level |
| `OTEL_ENABLED` | `true` | Turn OTEL export on or off |
| `OTEL_SERVICE_NAME` | `python-api-with-claude` | `service.name` resource attribute |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Collector endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` (port 4317) or `http/protobuf` (port 4318) |

Available models at the time of publishing:
- claude-opus-5-5
- claude-sonnet-5-5
- claude-fable-5-1
- claude-haiku-5-5

---

## Running

```powershell
python -m app.main
# or, with auto-reload while developing:
uvicorn app.main:create_app --factory --reload --port 56601
```

| URL | What |
|---|---|
| http://localhost:56601/docs | Swagger UI |
| http://localhost:56601/redoc | ReDoc |
| http://localhost:56601/health | Liveness probe |

---

## Usage

### `POST /api/v1/questions`

Request:

```json
{ "question": "What is the capital of France?" }
```

| Field | Required | Description |
|---|---|---|
| `question` | yes | 1–10,000 characters, not only whitespace. Leading and trailing whitespace is removed. |
| `model` | no | Claude model ID to use for this request, e.g. `claude-sonnet-5-5`. Must look like `claude-…`. Defaults to `CLAUDE_MODEL`. |
| `max_tokens` | no | Maximum answer length for this request, 1–128,000. Defaults to `CLAUDE_MAX_TOKENS`. Some models allow less; Claude rejects values above a model's limit with `400`. |

Request with overrides:

```json
{
  "question": "Summarise the plot of Hamlet.",
  "model": "claude-sonnet-5-5", 
  "max_tokens": 2000
}
```

Response `200`:

```json
{
  "answer": "The capital of France is Paris.",
  "model": "claude-opus-5-5",
  "stop_reason": "end_turn",
  "usage": { "input_tokens": 42, "output_tokens": 9 }
}
```

`model` is the model that actually answered. `stop_reason` is `max_tokens` when the answer was
cut off at the `max_tokens` limit.

Examples:

```powershell
# PowerShell
Invoke-RestMethod -Method Post -Uri http://localhost:56601/api/v1/questions `
  -ContentType 'application/json' -Body '{"question":"What is the capital of France?"}'
```

```bash
# curl
curl -X POST http://localhost:56601/api/v1/questions \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the capital of France?"}'
```

### Error responses

All errors use the body `{"detail": "..."}`.

| Status | When |
|---|---|
| `400` | Claude rejected the request, e.g. an unknown `model` or a `max_tokens` above that model's limit. Claude's error message is included. |
| `422` | Invalid request body, or Claude declined to answer (`stop_reason: refusal`) |
| `429` | The Claude API rate limit was hit; `Retry-After` is passed on when Claude sends it |
| `502` | The Claude API returned any other error (authentication failure, 5xx, …) |
| `503` | The Claude API can't be reached, timed out, or no credentials are configured |

---

## How the Claude API is used

The call is in [app/services/claude_service.py](app/services/claude_service.py). One
`AsyncAnthropic` client is created when the app starts (see the lifespan in
[app/main.py](app/main.py)) and closed when it shuts down. Each question becomes one Messages API
request:

```python
response = await client.beta.messages.create(
    model=model,            # request "model", else CLAUDE_MODEL (claude-opus-5-5)
    max_tokens=max_tokens,  # request "max_tokens", else CLAUDE_MAX_TOKENS (16000)
    system=SYSTEM_PROMPT,
    messages=[{"role": "user", "content": question}],
    output_config={"effort": "medium"},
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",
)
answer = "".join(b.text for b in response.content if b.type == "text")
```

Key points:

- **Messages API.** Everything goes through `POST /v1/messages`. A request has a `system` prompt
  and a list of `messages`; the response has a list of content blocks. Only `text` blocks are
  joined into the answer. Other block types, such as `thinking`, are skipped.
- **Model.** Claude Opus 5.5 always uses adaptive thinking. The `effort` setting controls how
  deeply it reasons and how many tokens it uses. `medium` is a good default for Q&A. Use `low`
  for cheaper, faster answers, or `high` and above for harder questions.
- **Refusals and fallback.** Claude's safety classifiers can decline a request. That still
  returns HTTP 200, but with `stop_reason: "refusal"`. `fallbacks="default"` lets the API retry
  the request on Anthropic's recommended fallback model within the same call. If every model
  declines, the service returns `422`. The fallback is only sent for models that support it
  (`claude-opus-5-5`, `claude-opus-5`, `claude-sonnet-5-5`, `claude-fable-5-1`); requests for
  other models are sent without it. Set `CLAUDE_REFUSAL_FALLBACK=false` to turn it off entirely.
- **Choosing a model per request.** Callers can pick a cheaper or faster model, such as
  `claude-sonnet-5-5` or `claude-haiku-5-5`, or a more capable one, such as `claude-fable-5-1`.
  `effort` is sent with every request, so very old models that don't support it (such as
  `claude-haiku-4-5`) return `400`.
- **Retries and timeouts.** The SDK retries 408/409/429/5xx and connection errors with
  exponential backoff (`CLAUDE_MAX_RETRIES`). Errors that remain after retries are mapped to HTTP
  status codes in [app/api/errors.py](app/api/errors.py).
- **Cost.** Every question is a billed API call. The response's `usage` field shows the tokens
  used. Current pricing is on the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing).

Ways to extend it:

- **Conversations:** keep earlier turns and send them as alternating `user`/`assistant` messages.
- **Streaming:** use `client.messages.stream(...)` with a FastAPI `StreamingResponse` to send the
  answer as it is generated.
- **Prompt caching:** add `cache_control={"type": "ephemeral"}` when the system prompt is large
  and doesn't change.
- **Tools:** define tools so Claude can call your own functions.

Docs: [Claude API documentation](https://platform.claude.com/docs) ·
[Python SDK](https://github.com/anthropics/anthropic-sdk-python).

---

## OpenTelemetry

The service exports all three signals over OTLP to the `otel-collector` container. The
collector then routes them to Tempo (traces), Mimir (metrics) and Loki (logs), which you view in
Grafana.

| Collector port | Protocol | Setting |
|---|---|---|
| `4317` | OTLP gRPC (default) | `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` |
| `4318` | OTLP HTTP | `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` |

### Starting the collector stack

Docker Desktop must be running. Then run:

```powershell
C:\Working\Storage\Dev\GitHub\Docker\Otel\Docker-Otel-builder.bat
```

This starts `otel-collector`, Tempo, Mimir, Loki and Grafana. Grafana is at
http://localhost:3000. The compose file uses an external Docker network called `pilot-net`.
Create it once with `docker network create pilot-net` if it doesn't exist.

### What is emitted

| Signal | Details |
|---|---|
| Traces | One server span per HTTP request (FastAPI instrumentation; `/health` is excluded). Each Claude call adds a child `chat <model>` client span with GenAI attributes: `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`. Errors are recorded on the span. |
| Metrics | HTTP server metrics, plus `gen_ai.client.token.usage` (histogram by `gen_ai.token.type`) and `gen_ai.client.operation.duration` (seconds). |
| Logs | Python `logging` records are sent to Loki with their trace and span IDs, so you can jump from a log line to its trace. |

To run without a collector, set `OTEL_ENABLED=false`. If the collector is down while OTEL is
enabled, the service still works; the exporters just log connection warnings.

---

## Development

```powershell
pytest                       # run tests with a coverage report (configured in pyproject.toml)
pytest tests/test_routes.py  # run one file
ruff check app tests         # lint
ruff format app tests        # format
```

Testing conventions:

- Tests never call the real Claude API or an OTEL collector. The Anthropic client is an
  `AsyncMock`, and the SDK responses are real `BetaMessage` objects built in
  [tests/conftest.py](tests/conftest.py).
- Route tests use FastAPI's `TestClient` and replace `ClaudeService` through
  `app.dependency_overrides`.
- Spans are checked with an in-memory exporter. Telemetry setup is tested by injecting in-memory
  exporters.
- Async tests run through `pytest-asyncio` (`asyncio_mode = "auto"`).
- Tests use the Arrange / Act / Assert layout, with one behaviour per test.

### Bruno API tests

[tests/Bruno/PythonAPIWithClaude](tests/Bruno/PythonAPIWithClaude) is a [Bruno](https://www.usebruno.com/)
collection that tests the running service over HTTP. Unlike the pytest suite, it calls the real
Claude API.

| Folder | Requests | Checks |
|---|---|---|
| `System` | `HealthCheck` | `GET /health` returns `200` with `status` and `version` |
| `v1/Questions` | `Ask`, `Ask_WithOverrides` | `200` with an answer, the model used, `stop_reason` and token `usage` |
| `v1/Questions` | `Ask_UnknownModel` | `400` when Claude rejects an unknown model |
| `v1/Questions` | `Ask_MissingQuestion`, `Ask_EmptyQuestion`, `Ask_BlankQuestion`, `Ask_QuestionTooLong`, `Ask_InvalidModel`, `Ask_MaxTokensTooSmall`, `Ask_MaxTokensTooLarge` | `422` for invalid request bodies |

The `Local` environment points to `http://localhost:56601/`. Start the service, then either open
the [tests/Bruno](tests/Bruno) folder in the Bruno app, select the `Local` environment and run the
collection, or use the Bruno CLI (requires Node.js):

```powershell
cd tests/Bruno/PythonAPIWithClaude
npx @usebruno/cli run --env Local
```

Only `Ask`, `Ask_WithOverrides` and `Ask_UnknownModel` reach Claude, and only the first two use
tokens, so a run costs very little. The service rejects the other requests before calling Claude. The API has no
authentication, so the collection needs no secrets.

Workflow: work on a branch off `develop`, keep `pytest` and `ruff check` passing, then open a PR
to `main`.

---

## License

MIT. See [LICENSE](LICENSE).
