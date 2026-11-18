---
name: create-viewer-space
description: Create or update a Hugging Face Space that browses 3D asset datasets in an interactive 3D viewer (search, filter, model-viewer, downloads). Use after publishing 3D assets.
---

# Viewer Space for 3D asset datasets

Run after `publish-3d-dataset`. Builds a **static** Space (free, no hardware, never sleeps) that reads each dataset's `metadata.jsonl` and assets straight from the Hub, so it stays current when the datasets change. Re-run it only when the *list* of datasets changes.

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts/hf3d.py"
# one or more datasets
uv run "$S" space <owner>/<Space-Name> --dataset <owner>/<Dataset> [--dataset ...] --title "..." --public
# or every dataset in an HF collection (title or slug), e.g. the user's 3D collection
uv run "$S" space <owner>/<Space-Name> --hf-collection "3D Assets" --title "..." --public
```

Options: `--description`, `--camera-orbit "-50deg 65deg auto"` (start view; the default is a three-quarter view from front-left, -X/+Z), `--emoji`, `--collection` (HF collection to file the Space in; defaults to the config `collection`).

If the user's config names a viewer Space for their 3D collection, update that one rather than creating another.

## What it shows

- Thumbnail grid from each row's preview image, a search box over every metadata field, and a filter built from low-cardinality text columns (family, region, format...).
- Click a card: `<model-viewer>` 3D view (orbit, zoom, AR on mobile) with every metadata field, a Download link and a Sketchfab link if the row has `sketchfab_url`.
- Deep link: `https://<owner>-<space>.static.hf.space/#<code or id>` opens that asset.
- Works with any dataset whose `metadata.jsonl` rows carry `asset` (or `glb`) and `file_name` paths; `hf3d build` writes exactly that. Datasets without `metadata.jsonl` are refused.

## Verify

Open the app URL in a real browser (not curl): the grid count should read `N of N` for the expected asset total, and opening one deep link should load the model (`model-viewer.loaded === true`). Look at a screenshot; a model seen from behind or edge-on means the `--camera-orbit` needs changing for this asset set.
