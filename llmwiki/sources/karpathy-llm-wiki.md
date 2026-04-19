---
title: Karpathy — LLM Wiki
kind: source
status: active
tags: [wiki, pattern, memex, meta]
updated: 2026-04-18
url: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
---

# Karpathy — LLM Wiki (pattern)

A design pattern for personal / team knowledge bases that the **LLM** maintains, not the
human. Contrasted with plain RAG: instead of rediscovering knowledge on every query, the
LLM builds and updates a persistent, interlinked markdown wiki that sits between the
operator and the raw sources.

## Key takeaways (applied to this repo)

- **Three layers**: raw sources (immutable), the wiki (LLM-owned), and a schema file
  (`AGENTS.md` for this repo) that encodes conventions.
- **Incremental ingestion**: when a new source / observation arrives, update the 10-15
  pages it touches; don't dump everything in one blob.
- **Two special files** — `index.md` (content-oriented catalog) and `log.md` (chronological,
  `grep`-friendly with `## [YYYY-MM-DD] <op> | <slug>` prefixes).
- **Compounding answers**: good query answers get filed back into the wiki as new pages,
  so explorations don't evaporate into chat history.
- **Lint periodically**: flag contradictions, stale claims, orphans, missing concept pages.
- **Git repo of markdown = free version control + branching + collaboration.**

## Why this fits cylon-local-infra

The operator complaint was "we keep forgetting what worked and what did not in the
process." That is the exact failure mode this pattern targets — the bookkeeping cost of
keeping a knowledge base current is what makes humans abandon it; LLMs are happy to
touch 15 pages in one pass. The Spark bring-up work is full of narrow failure modes
(IPv6 black hole, Ray CGraph timeout, transformers vs huggingface_hub drift, NCCL over
sockets vs IB verbs, ...) that have repeatedly re-bitten us — each one now gets its own
concept page the first time it costs us a day.

## Operations we adopt

| Op | Trigger | Artifact |
|---|---|---|
| ingest | new source added to repo or pasted into chat | `sources/<slug>.md` + updates to affected entities/concepts + `index.md` entry + `log.md` line |
| query | operator asks a question | answer with citations; if non-trivial, file back as a concept page |
| run | playbook / container / smoke test | `runs/YYYY-MM-DD-<slug>.md` + linked concept updates |
| lint | every ~20 log entries or on request | markdown health-check report |

## Tips we lifted verbatim

- Obsidian graph view reveals hubs and orphans at a glance.
- YAML frontmatter on every page makes Dataview / `grep` queries trivial.
- Consistent `## [YYYY-MM-DD] …` log prefixes survive plain-text search.

## Original
Copy of the gist at time of ingest:
<https://gist.githubusercontent.com/karpathy/442a6bf555914893e9891c11519de94f/raw>
(Fetched 2026-04-18.)
