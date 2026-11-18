---
name: setup
description: Set up hf3d - default Hugging Face owner, licence, visibility, collection and Sketchfab token source for publishing 3D assets.
---

# hf3d setup

Write `config.json` in `${CLAUDE_USER_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-plugins}/hf3d/` (use `~/.claude-user-data/hf3d/` if the user keeps their tool data there). Ask only for what is missing:

```json
{
  "default_owner": "<hf user or org>",
  "default_license": "cc-by-4.0",
  "default_public": true,
  "collection": "3D Assets",
  "attribution": "by <Name>, CC BY 4.0",
  "sketchfab_collection_uid": "<optional>",
  "sketchfab_token": "<where the token lives, e.g. a password-manager reference - never the token itself>",
  "notes": "<free text the skills should follow>"
}
```

Then check: `hf auth whoami` (Hugging Face login; `hf auth login` if not), `which blender` (only needed for `--render`), and `uv --version`.
