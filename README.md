# hf3d

Publish 3D assets to **Hugging Face** as a browsable dataset, with an optional **Sketchfab** mirror for an in-browser 3D viewer.

Point it at a folder of GLB/glTF/OBJ/STL files (with optional same-stem `.jpg` previews and `.json` metadata). It builds a dataset whose viewer shows a thumbnail grid with the metadata as columns, writes a card with download links, pushes it, files it in a collection and then checks the result on the Hub rather than trusting the upload response.

## Skills

- `publish-3d-dataset` - build and push (or update) a 3D asset dataset; `--render` makes missing previews with headless Blender
- `create-viewer-space` - a static Hugging Face Space that browses one or more 3D datasets (or a whole HF collection): thumbnail grid, search, filters, interactive 3D view with `<model-viewer>`, downloads, deep links
- `mirror-to-sketchfab` - upload the same assets to Sketchfab, public and downloadable, into a collection, with a ledger so re-runs are safe; documents the API's undocumented behaviour
- `setup` - default owner, licence, visibility, collection, token source

## CLI

`scripts/hf3d.py` runs on its own with [uv](https://docs.astral.sh/uv/):

```bash
uv run scripts/hf3d.py build  ./models ./out --title "My 3D Assets" --repo you/My-3D-Assets --render
uv run scripts/hf3d.py push   ./out you/My-3D-Assets --public --collection "3D Assets"
uv run scripts/hf3d.py check  you/My-3D-Assets
uv run scripts/hf3d.py space  you/My-3D-Viewer --hf-collection "3D Assets" --public
uv run scripts/hf3d.py sketchfab ./out --collection <uid>     # needs SKETCHFAB_API_TOKEN
```

Live example: the [3D Assets Viewer](https://huggingface.co/spaces/danielrosehill/3D-Assets-Viewer) Space over the dataset [danielrosehill/Modular-Storage-3D-Models](https://huggingface.co/datasets/danielrosehill/Modular-Storage-3D-Models).

## Installation

```bash
claude plugins install hf3d@danielrosehill
```

Requires `uv`, a Hugging Face login (`hf auth login` or `HF_TOKEN`), and optionally Blender for preview renders.

## Licence

MIT
