---
title: Massive Flat File raw mirror
kind: concept
status: active
tags: [massive, polygon, gcs, pricewhisperer]
updated: 2026-09-15
related:
  - ../entities/ms02.md
---

# Massive Flat File raw mirror

Personal-use archive of Massive (ex-Polygon) Flat Files. Same GCE host as
the Hugging Face downloader (`hf-archive-downloader`, `europe-west1-b`,
project `microscaler`) so the copy does not traverse the home WAN.

## Phases

1. **Raw (now):** copy every accessible object from
   `https://files.massive.com` bucket `flatfiles` to
   `gs://microscaler-massive/raw/…`, preserving Massive keys
   (`us_stocks_sip/day_aggs_v1/…csv.gz`). Skip prefixes that return 403.
2. **Parquet (later):** transform under `gs://microscaler-massive/parquet/`.
   Do not write parquet in the raw job.

## Credentials

Read at runtime from GCP Secret Manager in project `microscaler`.
`gcloud secrets versions access` keeps the payload on stdout only —
never write `/etc/massive-flatfile-mirror.env` or any other secrets file.

Required secret IDs:

- `MASSIVE_S3_ACCESS_KEY`
- `MASSIVE_S3_SECRET_KEY`

Optional (defaults apply if missing): `MASSIVE_S3_ENDPOINT`, `MASSIVE_BUCKET`.
REST `MASSIVE_API_KEY` is unused. The GCE VM service account needs
`roles/secretmanager.secretAccessor`. Sops in PriceWhisperer is the
operator source of truth for creating those GSM secrets, not a runtime path.

## Operator surface

```
just massive-mirror-status
just massive-mirror-list
just massive-mirror-sync
just massive-mirror-logs
```

Implementation: `scripts/massive_flatfile_mirror.py`.
