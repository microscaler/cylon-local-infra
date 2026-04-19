# llmwiki — Agent schema for cylon-local-infra

This wiki is the **persistent, compounding memory** for Cylon's local-infra work on the
ASUS DGX Spark cluster (`nvidia1`, `nvidia2`) and the `ms02` dev host. Any agent working
in this repo **reads, updates, and cross-links the wiki** as part of normal work — the
goal is that we never re-learn the same failure twice.

Pattern: [Karpathy — LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Directory layout

```
llmwiki/
  AGENTS.md          # this file (schema + workflows; agents MUST read first)
  index.md           # content catalog, organized by category
  log.md             # append-only chronological log of ingests/queries/lint passes/runs
  entities/          # named things: hosts, services, containers, systemd units, models, users
  concepts/          # topic pages: NCCL, Ray, vLLM TP, HF offline, IPv6 ASGI hang, etc.
  sources/           # one page per ingested external source (URL, paper, PR, issue, talk)
  runs/              # one page per provisioning / bring-up attempt (success AND failure)
  assets/            # images, diagrams, downloaded HTML-to-MD clips, screenshots
```

The wiki is **owned by the LLM**. Operators curate sources and ask questions; the LLM
reads, summarizes, files, cross-references, and keeps everything current.

## Three layers

1. **Raw sources** — `docs/`, NVIDIA build pages, NGC docs, upstream vLLM/Ray/Transformers
   issues, Gemma model card, Karpathy gist, our own `journalctl` captures. Immutable.
2. **The wiki** — everything under `llmwiki/`. LLM-written, LLM-maintained.
3. **This schema (`AGENTS.md`)** — conventions + workflows. Co-evolves with use.

## Page conventions

Every wiki page starts with YAML frontmatter so Obsidian + Dataview (and `grep`) work:

```yaml
---
title: <short title>
kind: entity | concept | source | run
status: active | stale | superseded | draft
tags: [spark, vllm, ray, nccl, hf, ipv6, gemma, ngc]
updated: 2026-04-18
sources: [sources/nvidia-stacked-sparks.md, docs/vllm-multi-node.md]
related: [entities/nvidia1.md, concepts/ray-tp.md]
---
```

**Filenames**: kebab-case, no spaces. Entity pages are singular (`entities/nvidia1.md`,
`entities/vllm-stacked-service.md`). Concept pages describe one idea
(`concepts/transformers-huggingface-hub-mismatch.md`).

**Links**: always relative markdown links (`[ray-head](../entities/ray-head-service.md)`)
so they work both in Obsidian and on GitHub.

**Evidence > opinion**: every claim that could be wrong carries a citation to a source
page, a commit, or a dated `journalctl`/`runs/<slug>.md` entry.

## Workflows (the LLM follows these)

### Ingest
Triggered when: a new source is added (URL, docs page, upstream issue, chat excerpt),
or an ad-hoc observation surfaces during a session.

1. Read the source. Summarize in your own words at the top of
   `sources/<slug>.md` (≤ 10 bullet points).
2. Extract entities and concepts referenced. If any lack a page, create it.
3. Update existing entity / concept pages. When adding a new claim that **contradicts**
   an existing one, do **not** silently overwrite — add a `## Contradictions` section on
   the affected page, keep both claims with dates + citations, and mark the older one
   `status: superseded` if clearly wrong.
4. Add a one-line entry to `index.md` under the right category.
5. Append to `log.md` with today's date and the consistent prefix:
   `## [YYYY-MM-DD] ingest | <source title>` — one paragraph describing what changed.

### Run (provisioning / bring-up attempt)

Every playbook run, container start, smoke test, etc. gets a page under `runs/`.

1. Create `runs/YYYY-MM-DD-<short-slug>.md` with frontmatter `kind: run`.
2. Record: command(s) issued, host(s), git SHA, wall-clock, outcome
   (**success** / **partial** / **failure**), and a "what worked / what did not" table.
3. If the run revealed a new failure mode, create or update the relevant
   `concepts/<name>.md` page and link from the run page. This is how we stop forgetting.
4. Append a `## [YYYY-MM-DD] run | <slug> | <outcome>` line to `log.md`.

### Query

1. Read `index.md` to find relevant pages.
2. Read those pages; drill into sources if needed.
3. Answer with citations (markdown links back into `llmwiki/`).
4. If the answer is non-trivial and likely to be asked again, **file it back** as a new
   concept page or append to an existing one. Chat history is not memory — the wiki is.

### Lint (periodic health check)

Run when `log.md` has grown by ~20 entries or on operator request:

- Flag pages with `status: active` but `updated` older than 30 days that cover areas
  with recent commits — surface as "stale?".
- Flag orphans: pages with no inbound links. Either link them in or delete.
- Flag concepts referenced in ≥2 pages but lacking their own page — propose creation.
- Surface `contradictions` sections and ask operator to adjudicate.
- Output a markdown report; DO NOT auto-delete pages.

## Conventions for this repo specifically

- **Hosts**: `nvidia1` (leader, `gx10-e1ce`, `169.254.102.149` on QSFP interconnect) and
  `nvidia2` (follower, `gx10-47b5`, `169.254.37.109`). Entity pages in `entities/`.
- **User accounts**: `casibbald` (Ansible operator, full sudo) and `nvidia` (runtime
  user for Ray/vLLM/NCCL). Both have pages in `entities/`.
- **Interconnect interface**: `enp1s0f0np0` (QSFP, link-local 169.254.0.0/16).
- **Dates in EEST** (Sparks are in Helsinki). Use ISO-8601 `YYYY-MM-DD` in filenames
  and log prefixes.
- **No secrets** in the wiki. Keys, tokens, passwords live in `.env` / sops. If a
  journal excerpt contains a token, redact before filing.

## Anti-patterns (do not do)

- Don't write narrative essays. Wiki pages are reference material; keep them dense.
- Don't paraphrase `docs/` when you can link to it. `docs/` is source of truth for
  architecture; the wiki is the **notebook** that records what worked when you ran it.
- Don't delete failed runs from `runs/`. The wiki's job is to remember the failures.
- Don't commit `Co-authored-by: Cursor …` — clients prohibit it.

## Bootstrapping order (new agent, new session)

1. Read this file.
2. Skim `index.md`.
3. `tail -30 log.md` to see what happened recently.
4. Read the entity/concept pages most relevant to the task before acting.
