# 2026-04-20 — pi CLI ↔ self-hosted vLLM integration

## Outcome

Wired the [pi coding-agent CLI](https://github.com/badlogic/pi-mono)
(`@mariozechner/pi-coding-agent@0.67.68`, installed globally via nvm-managed
npm) directly at the Qwen3.5-35B-A3B-FP8 vLLM endpoint. Client-direct over
LAN, no tunnel, no masquerade. Round-trip ~1.1 s for "Reply with just the
word: ACK" after the 4-bug fixup below.

Parallel to [zed-vllm-integration](./2026-04-20-zed-vllm-integration.md) —
pi CLI joins Zed as a second working daily-driver against the FP8 stack.
Both client-direct; no Cursor-style cloud middleman.

## Final config

### `~/.pi/agent/models.json`

```json
{
  "providers": {
    "qwen-spark": {
      "baseUrl": "http://vllm:8000/v1",
      "api": "openai-completions",
      "apiKey": "EMPTY",
      "authHeader": true,
      "compat": {
        "supportsStore": false,
        "supportsDeveloperRole": false,
        "supportsReasoningEffort": false,
        "maxTokensField": "max_tokens",
        "thinkingFormat": "qwen-chat-template"
      },
      "models": [
        {
          "id": "Qwen/Qwen3.5-35B-A3B-FP8",
          "name": "Qwen3.5 35B FP8 (Spark)",
          "reasoning": true,
          "input": ["text"],
          "contextWindow": 262144,
          "maxTokens": 16384,
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 }
        }
      ]
    }
  }
}
```

### `~/.pi/agent/settings.json` (relevant keys)

```json
{
  "defaultProvider": "qwen-spark",
  "defaultModel": "Qwen/Qwen3.5-35B-A3B-FP8",
  "defaultThinkingLevel": "medium",
  "thinkingBudgets": {
    "minimal": 1024,
    "low": 4096,
    "medium": 10240,
    "high": 32768
  }
}
```

### `/etc/hosts` Mac-side entry

```text
192.168.1.104 vllm
```

The `vllm` hostname is resolved before any DNS lookup, so pi's
`baseUrl: http://vllm:8000/v1` becomes `http://192.168.1.104:8000/v1` at
connect time. Zed config uses the raw IP (GUI apps bypass /etc/hosts in
some sandbox modes); pi's node process reads /etc/hosts normally so the
alias is safe.

## Bugs fixed in the initial user-supplied config

pi's canonical schema is documented in the installed package at
`node_modules/@mariozechner/pi-coding-agent/docs/models.md` and
`custom-provider.md`. The user's first attempt diverged in four places:

1. **`id: "qwen-35b"` — the critical blocker.** pi sends the model `id`
   verbatim in the `model:` field of `POST /v1/chat/completions`. Our vLLM
   advertises `['gpt-4o-mini', 'qwen3', 'Qwen/Qwen3.5-35B-A3B-FP8']` — no
   `qwen-35b`. Symptom: pi stuck in an infinite
   `GET /v1/models` poll loop (visible in vLLM logs as
   ~30 consecutive 200 OK's from the Mac on short sequential TCP ports,
   zero POSTs) while it waited for the id to appear. Fixed by setting
   `id: "Qwen/Qwen3.5-35B-A3B-FP8"` — matches what vLLM actually serves.
2. **`capabilities: ["thinking", "vision"]` — not a pi field.** pi uses
   `reasoning: true|false` + `input: ["text"] | ["text", "image"]`. Unknown
   fields are silently ignored, so `reasoning` stayed at its default
   `false` and pi never emitted the Qwen thinking template directives.
   Since Qwen3.5-35B-A3B-FP8 is text-only MoE, `vision`/`image` is wrong
   anyway. Fixed: `reasoning: true`, `input: ["text"]`.
3. **`api: "openai"` — not a valid enum.** The correct value is
   `"openai-completions"` (full list in `docs/custom-provider.md`:
   `anthropic-messages` | `openai-completions` | `openai-responses` |
   `google-generative-ai` | ...). Invalid values are accepted silently at
   config-parse time and fail at request-time with no clear error.
4. **Missing `compat` block for local Qwen-on-vLLM.** Documented pattern
   in `docs/models.md` section "OpenAI Compatibility" — without it:
   - `supportsDeveloperRole: true` (default) → pi sends a `developer`
     role message that Qwen's chat template doesn't understand.
   - `thinkingFormat: "openai"` (default when `reasoning: true`) → pi
     emits `reasoning_effort: "medium"` which vLLM ignores; Qwen's
     thinking is never enabled/disabled correctly.
   - `maxTokensField: "max_completion_tokens"` → vLLM accepts both but
     logs a deprecation note under some builds.

Canonical local-vLLM-Qwen compat block (this run uses it verbatim):

```json
"compat": {
  "supportsStore": false,
  "supportsDeveloperRole": false,
  "supportsReasoningEffort": false,
  "maxTokensField": "max_tokens",
  "thinkingFormat": "qwen-chat-template"
}
```

`thinkingFormat: "qwen-chat-template"` is the key piece — it makes pi
send `chat_template_kwargs: {enable_thinking: true|false}` which is
exactly what our `/vllm-patches/unsloth.jinja` template reads (see
[fp8-stack-cutover](./2026-04-19-fp8-stack-cutover.md)). The alternative
`"qwen"` (for DashScope) sends `enable_thinking` at top-level and does
*not* reach vLLM's chat template. Both `supportsStore: false` and
`supportsReasoningEffort: false` silence vLLM "fields present but ignored"
warnings — cosmetic but cleans up `/root/vllm-serve.log`.

## Secondary gotcha — stdin/skills

Running `pi --print ...` with an attached TTY caused pi to hang at
startup for ~75 s on the first invocation with no output and no HTTP
traffic. `ps`/`lsof` showed the node process already exited while the
cursor-wrapped zsh was still holding the shell open. Root cause: pi's
default startup reads skills, extensions, prompt templates, and
context files (AGENTS.md / CLAUDE.md) from the cwd — large for this
repo (llmwiki + roles + playbooks). Fixed by adding
`--no-skills --no-extensions --no-context-files` and piping `""` on
stdin. Documented in pi's `--help`; not a config bug.

Canonical non-interactive invocation (for scripts / CI / smoke tests):

```bash
echo "" | pi --print --no-tools --no-skills --no-extensions \
  --no-context-files --thinking off "Reply with just the word: ACK"
```

Interactive usage (`pi` alone or `pi "prompt"`) picks up AGENTS.md etc
and works normally — the `--no-*` flags are only needed for clean
non-interactive calls.

## Verification

End-to-end ACK test:

```text
$ echo "" | pi --print --no-tools --no-skills --no-extensions \
    --no-context-files --thinking off "Reply with just the word: ACK"
ACK
```

vLLM log line from the same second:

```text
(APIServer pid=1211) INFO:     192.168.1.130:53089 -
    "POST /v1/chat/completions HTTP/1.1" 200 OK
```

- Source IP: `192.168.1.130` (Mac LAN IP) — client-direct, no cloud hop.
- No `store` / `developer` / `reasoning_effort` warnings in vLLM log.
- Round-trip: 1.1 s for 1-token response (warm cache; cold was ~2.2 s).

## `pi --list-models | grep qwen-spark`

```text
qwen-spark      Qwen/Qwen3.5-35B-A3B-FP8  262.1K   16.4K    yes       no
```

Columns: provider, id, contextWindow, maxTokens, thinking (reasoning),
images (vision). All four correct.

## No cluster or repo changes

- No playbook run — Mac-side config only.
- No vLLM restart — the `--served-model-name Qwen/Qwen3.5-35B-A3B-FP8`
  alias was already in place from
  [cursor-ide-vllm-integration](./2026-04-19-cursor-ide-vllm-integration.md).
- `sparks.yml` unchanged.
- No llmwiki entity/concept changes.

## Follow-ups

- [ ] Add a `pi` smoke-test recipe to the top-level `justfile` that uses
      the canonical non-interactive invocation and exits non-zero if the
      endpoint is unreachable. Useful for "is my local Qwen up?" checks
      from the Mac.
- [ ] Propagate `~/.pi/agent/models.json` + `settings.json` via the
      `dev_workstation` role if/when we run pi on ms02 or a future Mac
      Studio. Same config works on any client with the `vllm` alias in
      `/etc/hosts` + LAN path to `192.168.1.104:8000`.
- [ ] Revisit `defaultThinkingLevel: medium` (10,240-token budget) once
      we have a meaningful dataset — the FP8 drag race showed Qwen3.5
      thinking is genuinely cheaper post-FP8 (decode ~2× vs BF16), but
      10 k tokens of thinking on every turn is still a lot of latency.
      "low" (4,096) might be the right default.
- [ ] Document that `--thinking off` + `reasoning: true` + `thinkingFormat
      qwen-chat-template` correctly disables thinking (chat template sees
      `enable_thinking: false`, skips the `<think>` block). Verified in
      this run but worth a dedicated validation against the
      [qwen3-thinking-validation](./2026-04-19-qwen3-thinking-validation.md)
      prompts.

## Cross-refs

- Parallel client: [zed-vllm-integration](./2026-04-20-zed-vllm-integration.md)
- Prior IDE attempt: [cursor-ide-vllm-integration](./2026-04-19-cursor-ide-vllm-integration.md)
- Stack: [fp8-stack-cutover](./2026-04-19-fp8-stack-cutover.md),
  [fp8-drag-race](./2026-04-19-fp8-drag-race.md)
- Thinking behavior (upstream data for follow-up 3):
  [qwen3-thinking-validation](./2026-04-19-qwen3-thinking-validation.md)
