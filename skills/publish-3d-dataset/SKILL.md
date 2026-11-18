---
name: publish-3d-dataset
description: Publish or update a folder of 3D models (GLB, glTF, OBJ, STL) as a Hugging Face dataset with previews, metadata and a card. Use on "upload these 3D models to Hugging Face".
---

# Publish 3D assets to a Hugging Face dataset

One script does the work: `${CLAUDE_PLUGIN_ROOT}/scripts/hf3d.py` (run with `uv run`; it declares its own dependencies).

## 1. Settings

Read `config.json` from `${CLAUDE_USER_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-plugins}/hf3d/` (or `~/.claude-user-data/hf3d/` if that exists). It may set `default_owner`, `default_license`, `default_public`, `collection`, `attribution`, plus free-text `notes` - follow them. No config: ask for the owner, default licence `cc-by-4.0`, and offer the `setup` skill afterwards.

## 2. Prepare the source folder

Any folder, flat or nested. Per asset, same file stem:

- `<stem>.glb` (or `.gltf` + its buffers, `.obj`, `.stl`, `.fbx`, `.ply`, `.usdz`) - required
- `<stem>.jpg` / `.png` - preview, optional
- `<stem>.json` - optional metadata: `name`, `description`, `tags`, and any other fields (dimensions, source, licence notes). Every field becomes a dataset column.

Recommended sidecar fields, so the dataset is filterable (the viewer Space turns low-cardinality text columns into filters):

| Field | Example |
|---|---|
| `id` | unique, stable slug; defaults to the file stem, must not collide |
| `name`, `description` | display name; one short paragraph |
| `category`, `subcategory` | `modular-storage` / `closed-totes` |
| `colour`, `material` | `blue`, `PP` |
| `state` | `open` / `closed` for models with moving parts |
| `dimensions_mm` | `{"length": 600, "width": 400, "height": 230}` |
| `source`, `confidence` | where the dimensions came from, how sure |

Keep values short and consistent (a fixed vocabulary per field), so they work as filters.

If the user has descriptions or specs elsewhere (a spreadsheet, a spec JSON), write the sidecar `.json` files from them rather than inventing text.

## 3. Build, look, push

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts/hf3d.py"
uv run "$S" build <src> <out> --title "<Title>" --repo <owner>/<Name> [--description "..."] [--tag x] [--render]
uv run "$S" push  <out> <owner>/<Name> [--public|--private] [--collection "<title or slug>"] [--message "..."]
```

- `--render` makes missing GLB/glTF previews with headless Blender, if `blender` is on PATH. Rows without an image are dropped by the dataset viewer, so render or supply previews.
- Look at two or three previews in `<out>/assets/*/` before pushing: a blank or black render means a bad asset or camera, not success.
- Repo names: follow the user's naming style if the config or their notes give one; otherwise Title-Case-With-Hyphens.
- Visibility: what the user said, else `default_public`, else private. Asking for public is the approval.
- `--prune` removes Hub assets no longer in `<out>`; use it only when the user is replacing the set.
- Updating an existing dataset: rebuild into the same `<out>` and push again; unchanged files are skipped by hash.

## 4. Verify

`push` ends by running `check`, which reports visibility, file and asset counts, a ranged download of one asset (expects `magic=b'glTF'` for GLB) and the dataset-viewer status. The viewer can take minutes and answer "server is busier than usual"; re-run `uv run "$S" check <owner>/<Name>` later. Report what was confirmed and what is still pending.

## What the dataset looks like

- `assets/<id>/<id>.<ext>` + preview; `metadata.jsonl` with `file_name` -> preview (imagefolder convention), so the Hub viewer shows a thumbnail grid with the metadata as columns. It does not render 3D; link Sketchfab (skill `mirror-to-sketchfab`) for an in-browser 3D viewer.
- GLB rows get `triangles`, `vertices` and `bbox_m` (from accessor min/max, ignoring node transforms).
- The Hub adds `modality:3d` itself when the repo holds 3D files (.glb etc.); the card adds `3d`, `3d-models` and the format tags. Add domain tags with `--tag`.
- `README.md` card: licence, tags, a table with thumbnails and direct download links.
