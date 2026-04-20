# 2026-04-20 — Zed ↔ self-hosted vLLM integration (Cursor pivot)

## Outcome

Wired the Zed editor's Agent Panel directly at the self-hosted Qwen3.5-35B-A3B-FP8 vLLM endpoint, **client-direct over LAN**, no tunnel, no cloud middleman, no model-name masquerade. First message round-tripped:

- prompt: `Anyone home`
- response: assistant content rendered inline in the Agent Panel
- model selector: `Qwen3.5-35B FP8 (Spark)`
- tools: `capabilities.tools=true` — agent-mode features available

This run resolves the blockers that closed out
[2026-04-19 cursor-ide-vllm-integration](./2026-04-19-cursor-ide-vllm-integration.md)
as Ask-mode-only with a public tunnel hack. Zed is now the recommended daily
driver for this vLLM stack; Cursor remains as an Ask-mode fallback via the
`--served-model-name gpt-4o-mini` masquerade alias.

## Why Zed and not Cursor (definitive answer)

Exhaustively researched during the Cursor session — three Cursor constraints
are unfixable on our side and drove the pivot:

1. **Server-side model-name allowlist.** Cursor's validator is a fixed
   registry of known model ids maintained by Cursor, not a prefix pattern.
   There is no `custom-*`, `local-*`, `byok-*`, or `qwen-*` escape hatch.
   `Qwen/Qwen3.5-35B-A3B-FP8` (slash stripped), `Qwen`, and `qwen3` all fail
   the send-time check even though the Add-Model field accepts them at type
   time. Only way through is to masquerade as a real frontier-model id
   (`gpt-4o-mini` confirmed; `gpt-4o`, `claude-3-5-haiku-*` also accepted).
2. **Chat completions route through Cursor's cloud.** Only `GET /v1/models`
   is client-direct — the probe we used to confirm Cursor could see our
   server in the first place. `POST /v1/chat/completions` dispatches from
   Cursor's backend (observed source IP `212.105.157.74`), so LAN-only URLs
   are unreachable. Public tunnel (ngrok) was mandatory.
3. **Agent/Composer refuses BYOK regardless of config.** Cursor routes
   Ask + Plan + Inline Edit through the user's Base URL but forces
   Agent/Composer through its own managed OpenAI pipe. User tests hitting
   the Ask-mode input surface produced `Unauthorized User Openai API key`
   errors with fresh Cursor-generated request IDs (`fb6592ad-830e-4805-…`)
   even with `Override OpenAI Base URL` toggled on and a well-formed
   `sk-*` placeholder key — because Cursor had silently flipped into Agent
   mode.

Zed has none of these:

- **No model-name validator.** Registered the real `Qwen/Qwen3.5-35B-A3B-FP8`
  id verbatim; no alias needed.
- **All provider requests are client-direct.** Zed calls the `api_url` you
  configure from your local machine. LAN URL is fine — no tunnel.
- **Agent Panel works against any OpenAI-compatible endpoint** that
  implements tool-calling. vLLM already ships `--enable-auto-tool-choice`
  + `--tool-call-parser qwen3_coder` (see
  [fp8-stack-cutover](./2026-04-19-fp8-stack-cutover.md)), so full tool use
  is live.
- **`reasoning` field is rendered, not dropped**, so Qwen3's thinking output
  is visible when thinking mode is enabled (vs Cursor's UI eating it
  silently).

## Config shipped — `~/.config/zed/settings.json`

Key details (schema per Zed docs `docs/src/ai/llm-providers.md` +
`agent-settings.md`, Oct 2026):

```jsonc
{
  "language_models": {
    "openai_compatible": {
      "Qwen-Spark": {
        "api_url": "http://192.168.1.104:8000/v1",
        "available_models": [
          {
            "name": "Qwen/Qwen3.5-35B-A3B-FP8",
            "display_name": "Qwen3.5-35B FP8 (Spark)",
            "max_tokens": 262144,
            "max_output_tokens": 16384,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false,
              "chat_completions": true
            }
          }
        ]
      }
    }
  },
  "agent": {
    "default_model": {
      "provider": "Qwen-Spark",
      "model": "Qwen/Qwen3.5-35B-A3B-FP8"
    },
    "favorite_models": [
      { "provider": "Qwen-Spark", "model": "Qwen/Qwen3.5-35B-A3B-FP8" }
    ],
    "tool_permissions": { "default": "allow" }
  }
}
```

### 4 bugs landed during this run

1. **`nvidia1` → `192.168.1.104`.** Zed's GUI process does not inherit
   `~/.ssh/config` hostname resolution. Use the LAN IP directly. Recorded
   alongside [macos-mdns-local-tld-trap](../concepts/macos-mdns-local-tld-trap.md)
   as another Mac-side name-resolution footgun.
2. **`supports_tools: true` → `capabilities.tools: true`.** The flat
   `supports_*` flags are the Ollama / OpenRouter schema. For
   `openai_compatible`, the current Zed docs require the nested
   `capabilities` object (`tools`, `images`, `parallel_tool_calls`,
   `prompt_cache_key`, `chat_completions`).
3. **Removed a duplicate `assistant.default_model` block.** The old v1
   `assistant` schema is ignored by current Zed, but the coexisting
   `agent.default_model` was set to `copilot_chat/gpt-4.1` — source of the
   `An Error Happened: No OAuth token available` error. Copilot Chat needs
   GitHub OAuth; switching `default_model` to the `Qwen-Spark` provider
   removes the OAuth dependency entirely.
4. **Provider id is just `"Qwen-Spark"`, not `"openai_compatible/Qwen-Spark"`.**
   Zed matches provider ids by the display name you put under
   `language_models.openai_compatible`, case-sensitive, no type prefix.

### API key handling

Zed intentionally does **not** store provider API keys in `settings.json`.
Two paths for our case:

- **Env var** — `QWEN_SPARK_API_KEY=anything`, picked up at Zed launch.
  Unreliable on macOS because GUI apps don't inherit login shell env.
- **Agent Panel UI** — command palette → `agent: open settings` → LLM
  Providers → `Qwen-Spark` → `Add Key`. Stored in macOS Keychain. This is
  what we shipped. Any non-empty value works because vLLM wasn't launched
  with `--api-key`; it ignores the `Authorization: Bearer …` header
  contents. We used `sk-local-vllm-any`.

### Picker initially showed "No matches"

Zed hides a provider's models from the model switcher until credentials
exist. Expected UX. Resolved by clicking `Configure` in the picker dropdown
→ entering the placeholder key.

## `sparks.yml` aliases kept

We left the three `--served-model-name` aliases from yesterday's Cursor
work in place:

```yaml
- "--served-model-name"
- "gpt-4o-mini"   # Cursor Ask-mode masquerade
- "qwen3"          # short CLI smoke-test id
- "Qwen/Qwen3.5-35B-A3B-FP8"  # canonical; used by Zed
```

Zero cost at serving time — they are pure `/v1/models` metadata; the
underlying weights are `vllm_default_model`. Kept for optionality.

## Cost of the pivot

- One `ansible-playbook playbooks/cutover_roce.yml -e vllm_stacked_container_recreate=true`
  at ~01:55 EEST (for the `gpt-4o-mini` alias, from the Cursor session) —
  55 s playbook + ~90 s vLLM cold start. Final PLAY RECAP:
  `nvidia1 ok=22 changed=4`, `nvidia2 ok=16 changed=3`.
- Two `pip install fastsafetensors` installs inside the running Ray
  containers (head + worker) — the NGC 26.03 bolt-on from
  [fp8-stack-cutover](./2026-04-19-fp8-stack-cutover.md) working exactly
  as designed.
- Zed config edit — no playbooks, no container bounces, no cluster impact.
  Pure Mac-side.

## Follow-ups

- [ ] Memoize Zed's Keychain key location so the ops page can document
      where to rotate it. (macOS Keychain access search term: "Zed" + the
      provider display name.)
- [ ] Decide whether to drop `--reasoning-parser qwen3` from `sparks.yml`.
      Currently enabled — Zed renders `reasoning` so this is fine for Zed,
      but it still causes empty-bubble UX in Cursor Ask-mode fallback.
      Leaving on; Cursor fallback is not the primary path.
- [ ] Retire `subway-omit-quadrant.ngrok-free.dev` + the ad-hoc ngrok
      agent on nvidia1 once Cursor fallback is confirmed unused for 1
      week. Free-tier endpoint is cheap to keep, but it's load-bearing
      on zero things now.
- [ ] Propagate `~/.config/zed/settings.json` via the `dev_workstation`
      role (if we ever run Zed on ms02 or a future Mac Studio M5). Current
      config is Mac-local only; no role change needed today.

## Cross-refs

- Model + stack: [fp8-stack-cutover](./2026-04-19-fp8-stack-cutover.md),
  [fp8-drag-race](./2026-04-19-fp8-drag-race.md)
- Transport: [roce-cutover](./2026-04-19-roce-cutover.md),
  [nccl-on-spark](../concepts/nccl-on-spark.md)
- Previous attempt: [cursor-ide-vllm-integration](./2026-04-19-cursor-ide-vllm-integration.md)
- Mac name-resolution gotchas:
  [macos-mdns-local-tld-trap](../concepts/macos-mdns-local-tld-trap.md),
  [ssh-alias-convention](../concepts/ssh-alias-convention.md)
