# /// script
# requires-python = ">=3.10"
# dependencies = ["huggingface_hub>=0.30", "requests>=2.31"]
# ///
"""hf3d - publish a folder of 3D assets as a Hugging Face dataset, optionally
mirrored to Sketchfab.

    uv run hf3d.py build  SRC OUT --title "My Assets" [--license cc-by-4.0] [--render]
    uv run hf3d.py push   OUT OWNER/NAME [--public] [--collection SLUG_OR_TITLE]
    uv run hf3d.py check  OWNER/NAME
    uv run hf3d.py sketchfab OUT [--collection UID] [--dry-run]
    uv run hf3d.py space  OWNER/NAME --dataset OWNER/DS [--hf-collection "3D Assets"] [--public]

SRC layout (flat or nested, any depth):

    <asset>.glb | .gltf | .obj | .stl | .fbx | .ply | .usdz   the asset
    <asset>.jpg | .png                                       optional preview, same stem
    <asset>.json                                             optional metadata, same stem:
        {"name": "...", "description": "...", "tags": [...], ...any other fields}

OUT layout (what goes to the Hub):

    README.md                  dataset card; rewritten on every build
    metadata.jsonl             one row per asset; `file_name` is the preview image, so
                               the dataset viewer shows a thumbnail grid with the
                               metadata as columns (imagefolder convention)
    assets/<id>/<id>.<ext>     the asset
    assets/<id>/<id>.jpg|png   preview (if one exists or --render made one)
    sketchfab.json             Sketchfab ledger, written by the `sketchfab` command

Config (optional): ${CLAUDE_USER_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-plugins}/hf3d/config.json
(~/.claude-user-data/hf3d/config.json is also read if that folder exists)
    {"default_owner": "...", "default_license": "cc-by-4.0", "default_public": true,
     "collection": "3D Assets", "attribution": "...",
     "sketchfab_collection_uid": "...", "card_footer": "..."}

Secrets: HF token from HF_TOKEN or `hf auth login`; Sketchfab from SKETCHFAB_API_TOKEN.
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

ASSET_EXT = {".glb", ".gltf", ".obj", ".stl", ".fbx", ".ply", ".usdz"}
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
SKETCHFAB_API = "https://api.sketchfab.com/v3"
SKETCHFAB_NAME_LIMIT = 48
SKETCHFAB_DESC_LIMIT = 1024
SKETCHFAB_COLLECTION_BATCH = 24


# --- config -------------------------------------------------------------------

def config_dir() -> Path:
    """$CLAUDE_USER_DATA/hf3d, else ~/.claude-user-data/hf3d if it exists, else XDG."""
    legacy = Path.home() / ".claude-user-data" / "hf3d"
    if not os.environ.get("CLAUDE_USER_DATA") and legacy.is_dir():
        return legacy
    root = os.environ.get("CLAUDE_USER_DATA") or os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "claude-plugins")
    return Path(root) / "hf3d"


def load_config() -> dict:
    p = config_dir() / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}


# --- GLB inspection -------------------------------------------------------------

def glb_stats(path: Path) -> dict:
    """Triangles, vertices and approximate bounding box from the GLB JSON chunk.

    The bbox comes from POSITION accessor min/max (required by the glTF spec) and
    ignores node transforms, so it is exact for assets exported with applied
    transforms and approximate otherwise. Units are glTF metres.
    """
    data = path.read_bytes()
    if data[:4] != b"glTF":
        return {}
    total = struct.unpack("<I", data[8:12])[0]
    off, doc = 12, None
    while off < total:
        clen, ctype = struct.unpack("<II", data[off:off + 8])
        if ctype == 0x4E4F534A:          # "JSON"
            doc = json.loads(data[off + 8:off + 8 + clen].decode("utf-8"))
            break
        off += 8 + clen
    if doc is None:
        return {}
    acc = doc.get("accessors", [])
    tris = verts = 0
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "POSITION" in attrs:
                a = acc[attrs["POSITION"]]
                verts += a["count"]
                if "min" in a and "max" in a:
                    lo = [min(x, y) for x, y in zip(lo, a["min"])]
                    hi = [max(x, y) for x, y in zip(hi, a["max"])]
            if prim.get("mode", 4) != 4:
                continue
            if "indices" in prim:
                tris += acc[prim["indices"]]["count"] // 3
            elif "POSITION" in attrs:
                tris += acc[attrs["POSITION"]]["count"] // 3
    out = {"triangles": tris, "vertices": verts}
    if lo[0] != float("inf"):
        out["bbox_m"] = [round(h - l, 4) for l, h in zip(lo, hi)]
    return out


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "asset"


# --- build ----------------------------------------------------------------------

RENDER_PY = r"""
import bpy, sys, math, mathutils
src, dst = sys.argv[sys.argv.index('--') + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
objs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
pts = [o.matrix_world @ mathutils.Vector(c) for o in objs for c in o.bound_box]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)])
hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
ctr, size = (lo + hi) / 2, max((hi - lo).length, 1e-3)
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
bpy.context.scene.collection.objects.link(cam)
cam.location = ctr + mathutils.Vector((-1.1, -1.4, 0.9)) * size
cam.rotation_euler = (ctr - cam.location).to_track_quat('-Z', 'Y').to_euler()
sun = bpy.data.objects.new('sun', bpy.data.lights.new('sun', 'SUN'))
sun.data.energy = 4.5; sun.rotation_euler = (math.radians(50), 0, math.radians(30))
bpy.context.scene.collection.objects.link(sun)
w = bpy.data.worlds.new('w'); bpy.context.scene.world = w
w.color = (0.85, 0.85, 0.85)
s = bpy.context.scene; s.camera = cam
s.render.engine = 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items] else 'BLENDER_EEVEE'
s.render.resolution_x = s.render.resolution_y = 640
s.render.image_settings.file_format = 'JPEG'
s.render.filepath = dst
bpy.ops.render.render(write_still=True)
"""


def render_preview(asset: Path, dst: Path) -> bool:
    blender = shutil.which("blender")
    if not blender or asset.suffix.lower() not in (".glb", ".gltf"):
        return False
    script = dst.parent / "_render.py"
    script.write_text(RENDER_PY)
    try:
        subprocess.run([blender, "--background", "--python", str(script), "--",
                        str(asset), str(dst)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    except (subprocess.SubprocessError, OSError):
        return False
    finally:
        script.unlink(missing_ok=True)
    return dst.exists()


def cmd_build(a):
    cfg = load_config()
    src, out = Path(a.src).resolve(), Path(a.out).resolve()
    assets = sorted(p for p in src.rglob("*") if p.suffix.lower() in ASSET_EXT
                    and out not in p.parents)
    if not assets:
        sys.exit(f"no 3D assets under {src}")
    keep = {"sketchfab.json"}
    if out.exists():
        for p in out.iterdir():
            if p.name not in keep:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    (out / "assets").mkdir(parents=True, exist_ok=True)

    rows, seen = [], set()
    for asset in assets:
        aid = slug(asset.stem)
        if aid in seen:
            aid = slug(f"{asset.parent.name}-{asset.stem}")
        seen.add(aid)
        dst = out / "assets" / aid
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, dst / f"{aid}{asset.suffix.lower()}")
        if asset.suffix.lower() == ".gltf":           # copy sidecar buffers/textures
            for f in asset.parent.iterdir():
                if f.suffix.lower() in (".bin", ".png", ".jpg", ".jpeg", ".ktx2") \
                        and f.stem != asset.stem:
                    shutil.copy2(f, dst / f.name)
        meta_p = asset.with_suffix(".json")
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}

        preview = None
        for ext in IMG_EXT:
            if asset.with_suffix(ext).exists():
                preview = dst / f"{aid}{ext}"
                shutil.copy2(asset.with_suffix(ext), preview)
                break
        if preview is None and a.render:
            target = dst / f"{aid}.jpg"
            if render_preview(asset, target):
                preview = target

        stats = glb_stats(asset) if asset.suffix.lower() == ".glb" else {}
        row = {"file_name": str(preview.relative_to(out)) if preview else None,
               "id": aid,
               "name": meta.get("name", asset.stem.replace("-", " ").replace("_", " ")),
               "asset": str((dst / f"{aid}{asset.suffix.lower()}").relative_to(out)),
               "format": asset.suffix.lower().lstrip("."),
               "bytes": asset.stat().st_size, **stats}
        row.update({k: v for k, v in meta.items() if k not in row or k == "name"})
        rows.append(row)

    with (out / "metadata.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    write_card(out, rows, a, cfg)
    missing = [r["id"] for r in rows if not r["file_name"]]
    print(f"{len(rows)} assets -> {out}")
    if missing:
        print(f"no preview for {len(missing)}: {', '.join(missing[:10])}"
              f"{' ...' if len(missing) > 10 else ''}  (add <stem>.jpg or use --render; "
              "rows without an image are dropped by the dataset viewer)")


def write_card(out: Path, rows, a, cfg):
    lic = a.license or cfg.get("default_license", "cc-by-4.0")
    tags = ["3d", "3d-models"] + sorted({r["format"] for r in rows}) + (a.tag or [])
    with_img = all(r["file_name"] for r in rows)
    fm = ["---", f"license: {lic}", f"pretty_name: {a.title}", "tags:"]
    fm += [f"  - {t}" for t in dict.fromkeys(tags)]
    fm += ["size_categories:", "  - n<1K" if len(rows) < 1000 else "  - 1K<n<10K"]
    if with_img:
        fm += ["configs:", "  - config_name: default", "    data_files:",
               "      - split: train", "        path:", "          - metadata.jsonl",
               "          - assets/**/*.jpg", "          - assets/**/*.jpeg",
               "          - assets/**/*.png", "          - assets/**/*.webp"]
    fm.append("---")
    body = [f"# {a.title}", ""]
    if a.description:
        body += [a.description, ""]
    body += ["| | Asset | Format | Triangles | Download |", "|---|---|---|---|---|"]
    repo = a.repo or "OWNER/NAME"
    for r in rows:
        img = f"<img src=\"{r['file_name']}\" width=\"96\">" if r["file_name"] else ""
        body.append(f"| {img} | {r['name']} | {r['format']} | {r.get('triangles', '')} | "
                    f"[{Path(r['asset']).name}](https://huggingface.co/datasets/{repo}"
                    f"/resolve/main/{r['asset']}) |")
    body += ["", "## Layout", "",
             "- `assets/<id>/` - the asset and its preview image",
             "- `metadata.jsonl` - one row per asset (the dataset viewer shows previews "
             "with these columns; it does not render 3D files)",
             "", "Download one asset:", "", "```bash",
             f"hf download {repo} {rows[0]['asset']} --repo-type dataset --local-dir .",
             "```", ""]
    attribution = cfg.get("attribution")
    body += ["## Licence", "", f"`{lic}`" + (f". Attribution: {attribution}" if attribution else ""), ""]
    if cfg.get("card_footer"):
        body += [cfg["card_footer"], ""]
    (out / "README.md").write_text("\n".join(fm + [""] + body), encoding="utf-8")


# --- push / check -----------------------------------------------------------------

def cmd_push(a):
    from huggingface_hub import HfApi, add_collection_item, create_collection, list_collections
    cfg = load_config()
    repo = a.repo if "/" in a.repo else f"{cfg.get('default_owner', '')}/{a.repo}"
    public = a.public or (cfg.get("default_public", False) and not a.private)
    api = HfApi()
    api.create_repo(repo, repo_type="dataset", private=not public, exist_ok=True)
    out = Path(a.out)
    card = out / "README.md"
    card.write_text(card.read_text(encoding="utf-8").replace("OWNER/NAME", repo),
                    encoding="utf-8")
    info = api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(out),
                             commit_message=a.message or "Update 3D assets",
                             delete_patterns=["assets/**"] if a.prune else None)
    print(f"pushed: https://huggingface.co/datasets/{repo}  ({info.oid[:10] if hasattr(info, 'oid') else info})")
    coll = a.collection or cfg.get("collection")
    if coll:
        owner = repo.split("/")[0]
        match = None
        for c in list_collections(owner=owner, limit=100):
            if coll in (c.slug, c.title):
                match = c
                break
        if match is None and "/" not in coll:
            match = create_collection(title=coll, namespace=owner, private=not public,
                                      exists_ok=True)
        if match:
            add_collection_item(match.slug, item_id=repo, item_type="dataset", exists_ok=True)
            print(f"collection: https://huggingface.co/collections/{match.slug}")
    cmd_check(argparse.Namespace(repo=repo))


def cmd_check(a):
    """Verify the thing, not the upload response: repo visibility, files on the
    Hub, one asset actually downloadable, and dataset-viewer status."""
    import requests
    from huggingface_hub import HfApi, get_token, hf_hub_url
    tok = get_token()
    auth = {"Authorization": f"Bearer {tok}"} if tok else {}
    info = HfApi().dataset_info(a.repo, files_metadata=False)
    files = [s.rfilename for s in info.siblings]
    assets = [f for f in files if Path(f).suffix.lower() in ASSET_EXT]
    print(f"{a.repo}: private={info.private}  files={len(files)}  assets={len(assets)}")
    if assets:
        r = requests.get(hf_hub_url(a.repo, assets[0], repo_type="dataset"),
                         headers={"Range": "bytes=0-3", **auth}, timeout=60, allow_redirects=True)
        print(f"  download {assets[0]}: HTTP {r.status_code} magic={r.content[:4]!r}")
    v = requests.get("https://datasets-server.huggingface.co/is-valid",
                     params={"dataset": a.repo}, headers=auth, timeout=60).json()
    print(f"  viewer: {v}")


# --- viewer space -------------------------------------------------------------------

def cmd_space(a):
    """Create or update a static HF Space that browses one or more 3D datasets:
    thumbnail grid, search, group filter, and a <model-viewer> 3D view with
    metadata, download and Sketchfab links. It reads metadata.jsonl and the
    assets straight from the dataset repos (HF resolve URLs allow CORS), so the
    Space never needs rebuilding when the datasets change - only when the
    dataset list does. Static SDK: free, no hardware, no sleep."""
    from huggingface_hub import HfApi, add_collection_item, get_collection, list_collections
    cfg = load_config()
    api = HfApi()
    owner = cfg.get("default_owner", "")
    space = a.space if "/" in a.space else f"{owner}/{a.space}"
    datasets = list(a.dataset or [])
    if a.hf_collection:
        slug_ = a.hf_collection
        if "/" not in slug_ or not re.search(r"-[0-9a-f]{24}$", slug_):
            found = [c for c in list_collections(owner=owner, limit=100) if slug_ in (c.title, c.slug)]
            if not found:
                sys.exit(f"collection not found: {slug_}")
            slug_ = found[0].slug
        coll = get_collection(slug_)
        datasets += [i.item_id for i in coll.items if i.item_type == "dataset"]
    datasets = list(dict.fromkeys(datasets))
    if not datasets:
        sys.exit("no datasets: pass --dataset and/or --hf-collection")
    for d in datasets:                     # each must have the hf3d layout
        files = {s.rfilename for s in api.dataset_info(d).siblings}
        if "metadata.jsonl" not in files:
            sys.exit(f"{d}: no metadata.jsonl - publish it with `hf3d build/push` first")
    public = a.public or (cfg.get("default_public", False) and not a.private)
    title = a.title or "3D asset viewer"

    tmp = Path(a.workdir or f"/tmp/hf3d-space-{slug(space)}")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    shutil.copy2(Path(__file__).parent / "space_template" / "index.html", tmp / "index.html")
    (tmp / "config.json").write_text(json.dumps(
        {"title": title, "description": a.description or "", "datasets": datasets,
         "group_by": a.group_by, "camera_orbit": a.camera_orbit}, indent=2, ensure_ascii=False) + "\n")
    short = title if len(title) <= 30 else title[:29] + "…"
    fm = ["---", f"title: {json.dumps(short, ensure_ascii=False)}", f"emoji: {a.emoji}", "colorFrom: blue", "colorTo: gray",
          "sdk: static", "pinned: false",
          f"license: {a.license or cfg.get('default_license', 'cc-by-4.0')}",
          f"short_description: {json.dumps((a.description or 'Browse and view 3D assets')[:60], ensure_ascii=False)}",
          "datasets:"] + [f"  - {d}" for d in datasets] + ["---", ""]
    body = [f"# {title}", "", (a.description or ""), "",
            "Interactive viewer for 3D assets published as Hugging Face datasets: search, filter, "
            "and open any model in a 3D view (`<model-viewer>`, AR on mobile) with its metadata "
            "and a download link. Assets load straight from:", ""]
    body += [f"- [{d}](https://huggingface.co/datasets/{d})" for d in datasets]
    body += ["", "Deep link to one asset: append `#<code or id>` to the Space URL.", ""]
    (tmp / "README.md").write_text("\n".join(fm + body), encoding="utf-8")

    api.create_repo(space, repo_type="space", space_sdk="static", private=not public, exist_ok=True)
    api.upload_folder(repo_id=space, repo_type="space", folder_path=str(tmp),
                      commit_message=a.message or "Update viewer")
    host = space.replace("/", "-").replace("_", "-").replace(".", "-").lower()
    print(f"space: https://huggingface.co/spaces/{space}")
    print(f"app:   https://{host}.static.hf.space")
    coll = a.collection or cfg.get("collection")
    if coll:
        for c in list_collections(owner=space.split("/")[0], limit=100):
            if coll in (c.slug, c.title):
                add_collection_item(c.slug, item_id=space, item_type="space", exists_ok=True)
                print(f"collection: https://huggingface.co/collections/{c.slug}")
                break


# --- sketchfab mirror -------------------------------------------------------------

def sf_headers(tok):
    return {"Authorization": f"Token {tok}"}


def cmd_sketchfab(a):
    """Mirror built assets to Sketchfab. Ledger in OUT/sketchfab.json makes re-runs
    skip anything already uploaded.

    Verified API behaviour (2026-11-18):
    - Auth header is `Token <token>`, not Bearer.
    - The upload POST ignores isDownloadable; models default to NOT downloadable,
      which on a free plan spends one of 10 monthly upload credits. PATCH
      isDownloadable after processing finishes, then read it back with an
      authenticated GET (the anonymous GET is cached). A few models may still
      refuse it; they are reported, not retried forever.
    - Collection adds are capped at 24 models per request.
    """
    import requests
    cfg = load_config()
    tok = os.environ.get("SKETCHFAB_API_TOKEN")
    if not tok and not a.dry_run:
        sys.exit("SKETCHFAB_API_TOKEN is not set")
    out = Path(a.out)
    ledger_p = out / "sketchfab.json"
    ledger = json.loads(ledger_p.read_text()) if ledger_p.exists() else {}
    rows = [json.loads(l) for l in (out / "metadata.jsonl").read_text().splitlines() if l]
    lic = (a.license or cfg.get("default_license", "cc-by-4.0")).lower()
    sf_lic = {"cc-by-4.0": "by", "cc-by-sa-4.0": "by-sa", "cc0-1.0": "cc0",
              "cc-by-nc-4.0": "by-nc", "cc-by-nd-4.0": "by-nd"}.get(lic, "by")
    todo = [r for r in rows if r["id"] not in ledger and r["format"] in ("glb", "gltf", "obj", "fbx", "stl", "ply", "usdz")]
    print(f"{len(todo)} to upload, {len(rows) - len(todo)} already in ledger")
    bad = []
    for r in todo:
        name = r["name"][:SKETCHFAB_NAME_LIMIT]
        desc = (r.get("description") or r["name"])[:SKETCHFAB_DESC_LIMIT]
        tags = [slug(t).lower()[:48] for t in r.get("tags", [])][:42]
        print(f"  {r['id']:28s} {name}")
        if a.dry_run:
            continue
        data = [("name", name), ("description", desc), ("license", sf_lic),
                ("isPublished", "true")] + [("tags", t) for t in tags]
        with (out / r["asset"]).open("rb") as fh:
            resp = requests.post(f"{SKETCHFAB_API}/models", headers=sf_headers(tok),
                                 data=data, files={"modelFile": (Path(r["asset"]).name, fh)},
                                 timeout=300)
        if resp.status_code != 201:
            print(f"    upload failed HTTP {resp.status_code}: {resp.text[:300]}")
            continue
        uid = resp.json()["uid"]
        status = "?"
        for _ in range(60):
            m = requests.get(f"{SKETCHFAB_API}/models/{uid}", headers=sf_headers(tok), timeout=60).json()
            status = (m.get("status") or {}).get("processing", "?")
            if status not in ("PENDING", "PROCESSING"):
                break
            time.sleep(5)
        ok = False
        for _ in range(3):
            requests.patch(f"{SKETCHFAB_API}/models/{uid}", headers=sf_headers(tok),
                           json={"isDownloadable": True}, timeout=60)
            time.sleep(2)
            m = requests.get(f"{SKETCHFAB_API}/models/{uid}", headers=sf_headers(tok), timeout=60).json()
            if m.get("isDownloadable"):
                ok = True
                break
        if not ok:
            bad.append(r["id"])
        ledger[r["id"]] = {"uid": uid, "url": m.get("viewerUrl"), "status": status,
                           "downloadable": ok}
        ledger_p.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        print(f"    -> {m.get('viewerUrl')}  {status}  downloadable={ok}")
    coll = a.collection or cfg.get("sketchfab_collection_uid")
    if coll and not a.dry_run and ledger:
        uids = [v["uid"] for v in ledger.values()]
        for i in range(0, len(uids), SKETCHFAB_COLLECTION_BATCH):
            resp = requests.post(f"{SKETCHFAB_API}/collections/{coll}/models",
                                 headers=sf_headers(tok),
                                 json={"models": uids[i:i + SKETCHFAB_COLLECTION_BATCH]},
                                 timeout=60)
            print(f"  collection batch {i // SKETCHFAB_COLLECTION_BATCH + 1}: HTTP {resp.status_code}")
    if bad:
        print(f"not downloadable on Sketchfab (refused the flag): {', '.join(bad)}")


def main():
    ap = argparse.ArgumentParser(prog="hf3d")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("src"); b.add_argument("out")
    b.add_argument("--title", required=True)
    b.add_argument("--description")
    b.add_argument("--license")
    b.add_argument("--tag", action="append")
    b.add_argument("--repo", help="OWNER/NAME, for download links in the card")
    b.add_argument("--render", action="store_true", help="render missing previews with Blender")
    p = sub.add_parser("push")
    p.add_argument("out"); p.add_argument("repo")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--public", action="store_true"); g.add_argument("--private", action="store_true")
    p.add_argument("--collection"); p.add_argument("--message")
    p.add_argument("--prune", action="store_true", help="delete Hub assets no longer in OUT")
    c = sub.add_parser("check"); c.add_argument("repo")
    s = sub.add_parser("sketchfab")
    s.add_argument("out"); s.add_argument("--collection"); s.add_argument("--license")
    s.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("space")
    v.add_argument("space", help="OWNER/NAME of the Space")
    v.add_argument("--dataset", action="append", help="dataset id; repeatable")
    v.add_argument("--hf-collection", help="include every dataset in this HF collection (slug or title)")
    v.add_argument("--title"); v.add_argument("--description"); v.add_argument("--license")
    v.add_argument("--group-by")
    v.add_argument("--camera-orbit", help='model-viewer start view, e.g. "-50deg 65deg auto"'); v.add_argument("--emoji", default="📦")
    v.add_argument("--collection", help="HF collection to file the Space in")
    v.add_argument("--message"); v.add_argument("--workdir")
    g2 = v.add_mutually_exclusive_group()
    g2.add_argument("--public", action="store_true"); g2.add_argument("--private", action="store_true")
    a = ap.parse_args()
    {"build": cmd_build, "push": cmd_push, "check": cmd_check, "sketchfab": cmd_sketchfab,
     "space": cmd_space}[a.cmd](a)


if __name__ == "__main__":
    main()
