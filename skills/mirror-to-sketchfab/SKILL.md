---
name: mirror-to-sketchfab
description: Mirror 3D assets built by hf3d to Sketchfab (public, downloadable, into a collection) for an in-browser 3D viewer. Use on "also put them on Sketchfab".
---

# Mirror to Sketchfab

Run after `publish-3d-dataset` has built `<out>` (it reads `<out>/metadata.jsonl`).

```bash
export SKETCHFAB_API_TOKEN=...      # from the user's secret store; see config notes
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/hf3d.py" sketchfab <out> --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/hf3d.py" sketchfab <out> [--collection <uid>]
```

Test with one asset first if the account is new to this script: copy one asset's row into a scratch `<out>` and run it there.

- Ledger: `<out>/sketchfab.json` (id -> uid, URL, downloadable). Re-runs skip anything in it. Push `<out>` to Hugging Face again afterwards so the ledger is published with the dataset.
- Names are cut to 48 characters and descriptions to 1024 (Sketchfab limits); shorten names in the sidecar JSON rather than letting them truncate.
- Licence maps from the dataset licence (`cc-by-4.0` -> `by`, `cc0-1.0` -> `cc0`, ...).

## Sketchfab API behaviour (verified 2026-11-18)

- Auth header is `Token <token>`, not Bearer.
- The upload POST **ignores** `isDownloadable`; models default to not downloadable, and on a free plan each non-downloadable model spends one of 10 monthly upload credits. The script PATCHes `isDownloadable` after processing and reads it back with an authenticated GET (anonymous GETs are cached and lag).
- A few models can refuse the flag even after a file re-upload (3 of 23 in one batch). The script lists them; tell the user they are viewable but not downloadable on Sketchfab, and that the Hugging Face copy has the file. Do not loop retries.
- Collection adds are capped at 24 models per request; the script batches.
- A processing `error` of `localStorage is not defined` appeared on every upload that day while the models viewed fine: a Sketchfab-side issue, not an asset fault.
