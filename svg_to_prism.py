#!/usr/bin/env -S uv run --quiet --python 3.12 --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "trimesh",
#     "shapely",
#     "svg.path",
#     "lxml",
#     "numpy",
#     "rtree",
#     "mapbox_earcut",
#     "networkx",
#     "scipy",
#     "pillow",
#     "resvg-py",
# ]
# ///
"""
Extrude an SVG silhouette into a right prism and export a textured OBJ for
Tabletop Simulator.

Dependencies are declared inline above (PEP 723); uv installs them into a
cached, isolated environment on first run, so nothing to set up:

    ./svg_to_prism.py pawn.svg pawn --width 1.18 --depth 0.591

Takes an output BASENAME with no extension (passing one ending in .obj or
.png is an error); the script appends the suffixes itself.
Writes three files:
    pawn.obj  geometry + UVs + normals   -> TTS "Model/Mesh"
    pawn.png  the SVG art as a texture   -> TTS "Diffuse/Image"
    pawn.mtl  material (TTS ignores it; lets Blender etc. preview the texture)

The art is planar-projected onto the front cap and, mirrored by construction,
the back cap. The side walls sample a margin of the texture that is guaranteed
to be the SVG's background color.

--width    : final size of the longest side of the silhouette (TTS units ~ inches)
--depth    : extrusion thickness (TTS units)
--simplify : outline tolerance as a fraction of width (0.002 ~ 0.06 mm at 30 mm)
--texture  : texture size in pixels (square)
--color    : override the background color (any CSS color); default is the fill
             of the first filled shape in the SVG
--keep-holes : punch interior shapes through instead of filling them
"""
import argparse
import re
from io import BytesIO
from pathlib import Path

import numpy as np
import resvg_py
import trimesh
from lxml import etree
from PIL import Image, ImageColor
from shapely.affinity import scale as shp_scale, translate as shp_translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "polygon", "polyline"}
PAD = 32          # texture margin in px, all background color; sides sample it
CREASE_DEG = 35   # side-wall normals are smoothed across angles below this


# --------------------------------------------------------------------------- SVG

def parse_svg(svg_path):
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.parse(svg_path, parser).getroot()


def _fill_of(el):
    """Effective fill of an element, walking up through parent groups."""
    while el is not None and isinstance(el.tag, str):
        m = re.search(r"(?:^|;)\s*fill\s*:\s*([^;]+)", el.get("style", ""))
        fill = m.group(1).strip() if m else el.get("fill")
        if fill:
            return fill
        el = el.getparent()
    return "black"  # SVG default fill


UNPAINTED_CONTAINERS = {"clipPath", "defs", "mask", "symbol", "pattern", "marker"}


def _in_unpainted_container(el):
    """True if el sits inside an element whose children are never drawn directly
    (e.g. the <rect> inside an Affinity/Illustrator <clipPath>)."""
    p = el.getparent()
    while p is not None and isinstance(p.tag, str):
        if etree.QName(p).localname in UNPAINTED_CONTAINERS:
            return True
        p = p.getparent()
    return False


def background_color(root):
    """Fill of the first painted shape in document order (the bottom layer)."""
    for el in root.iter():
        if not isinstance(el.tag, str) or etree.QName(el).localname not in SHAPE_TAGS:
            continue
        if _in_unpainted_container(el):
            continue
        fill = _fill_of(el)
        if fill.lower() in ("none", "transparent") or fill.startswith("url("):
            continue
        return fill
    return None


def viewbox(root):
    vb = root.get("viewBox")
    if vb:
        return [float(x) for x in re.split(r"[\s,]+", vb.strip())]
    w = float(re.sub(r"[a-z%]+$", "", root.get("width", "0")) or 0)
    h = float(re.sub(r"[a-z%]+$", "", root.get("height", "0")) or 0)
    if not (w and h):
        raise SystemExit("SVG has no viewBox and no absolute width/height.")
    return [0.0, 0.0, w, h]


# ----------------------------------------------------------------------- texture

def render_texture(root, vb, size, bg_rgb):
    """Render the viewBox, aspect-preserved, centered inside a bg-colored margin.

    Returns the image and the art's pixel rect (ox, oy, aw, ah) so UVs can be
    computed with exactly the same mapping.
    """
    _, _, vbw, vbh = vb
    inner = size - 2 * PAD
    k = inner / max(vbw, vbh)
    aw, ah = round(vbw * k), round(vbh * k)
    ox, oy = (size - aw) // 2, (size - ah) // 2

    root = etree.fromstring(etree.tostring(root))
    root.set("width", str(aw))
    root.set("height", str(ah))
    root.set("viewBox", " ".join(str(x) for x in vb))
    hexbg = "#%02x%02x%02x" % bg_rgb
    png = resvg_py.svg_to_bytes(svg_string=etree.tostring(root, encoding="unicode"),
                                width=aw, height=ah, background=hexbg)
    art = Image.open(BytesIO(bytes(png))).convert("RGB")

    tex = Image.new("RGB", (size, size), bg_rgb)
    tex.paste(art, (ox, oy))
    return tex, (ox, oy, aw, ah)


# -------------------------------------------------------------------------- mesh

def vertex_normals_split(mesh, cap_mask):
    """Per-corner normals: caps flat; side walls smoothed below CREASE_DEG."""
    fn = mesh.face_normals
    cos_crease = np.cos(np.radians(CREASE_DEG))
    side_faces_at = [[] for _ in range(len(mesh.vertices))]
    for f, tri in enumerate(mesh.faces):
        if not cap_mask[f]:
            for v in tri:
                side_faces_at[v].append(f)

    corner_n = np.zeros((len(mesh.faces), 3, 3))
    for f, tri in enumerate(mesh.faces):
        for c, v in enumerate(tri):
            if cap_mask[f]:
                corner_n[f, c] = fn[f]
                continue
            nbrs = [g for g in side_faces_at[v] if fn[g] @ fn[f] >= cos_crease]
            # weight by face area so the two triangles of a wall quad agree
            n = (fn[nbrs] * mesh.area_faces[nbrs, None]).sum(0)
            corner_n[f, c] = n / np.linalg.norm(n)
    return corner_n


def write_obj(path, mesh, cap_mask, cap_uv, side_uv, corner_n, mtl_name, tex_name):
    out = Path(path)
    mtl_path = out.with_suffix(".mtl")
    mtl_path.write_text(f"newmtl {mtl_name}\nKa 1 1 1\nKd 1 1 1\nKs 0 0 0\n"
                        f"d 1\nillum 1\nmap_Kd {tex_name}\n")

    # dedupe normals to keep the file small
    n_round = np.round(corner_n.reshape(-1, 3), 6)
    normals, n_idx = np.unique(n_round, axis=0, return_inverse=True)
    n_idx = n_idx.reshape(-1, 3)

    nv = len(mesh.vertices)
    side_vt = nv + 1  # 1-based index of the single swatch UV, after the per-vertex ones

    lines = [f"mtllib {mtl_path.name}", f"o {out.stem}"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in mesh.vertices]
    lines += [f"vt {u:.6f} {v:.6f}" for u, v in cap_uv]
    lines.append(f"vt {side_uv[0]:.6f} {side_uv[1]:.6f}")
    lines += [f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in normals]
    lines.append(f"usemtl {mtl_name}")
    lines.append("s off")
    for f, tri in enumerate(mesh.faces):
        corners = []
        for c, v in enumerate(tri):
            vt = v + 1 if cap_mask[f] else side_vt
            corners.append(f"{v + 1}/{vt}/{n_idx[f, c] + 1}")
        lines.append("f " + " ".join(corners))
    out.write_text("\n".join(lines) + "\n")
    return mtl_path


# -------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("out", metavar="basename",
                    help="output basename WITHOUT extension; writes basename.obj/.mtl/.png")
    ap.add_argument("--width", type=float, default=1.0)
    ap.add_argument("--depth", type=float, default=0.25)
    ap.add_argument("--simplify", type=float, default=0.002)
    ap.add_argument("--texture", type=int, default=1024)
    ap.add_argument("--color", help="background color (default: the SVG's first fill)")
    ap.add_argument("--keep-holes", action="store_true",
                    help="cut interior shapes through the prism (default: solid silhouette)")
    args = ap.parse_args()

    if Path(args.out).suffix.lower() in (".obj", ".png"):
        ap.error(f"output name {args.out!r} must not end in .obj or .png; "
                 "give a basename only (the script adds .obj and .png itself)")
    args.out = args.out + ".obj"

    root = parse_svg(args.svg)
    vb = viewbox(root)
    color = args.color or background_color(root)
    if color is None:
        raise SystemExit("No filled shape found in the SVG; pass --color.")
    bg_rgb = ImageColor.getrgb(color)[:3]

    # 1. SVG -> closed polygons in SVG user space
    polys = trimesh.load_path(args.svg).polygons_full
    if not polys:
        raise SystemExit("No closed shapes found. Convert strokes/text to filled paths first.")
    if not args.keep_holes:
        # Interior detail art (eyes, markings) would otherwise become through-holes
        polys = [Polygon(p.exterior) for p in polys]
    shape = unary_union(polys)

    # 2. Normalize: flip Y (SVG is y-down), center on origin, scale to --width.
    #    Remember the transform so UVs can be mapped back to SVG space.
    shape = shp_scale(shape, xfact=1, yfact=-1, origin=(0, 0))
    minx, miny, maxx, maxy = shape.bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    s = args.width / max(maxx - minx, maxy - miny)
    shape = shp_translate(shape, -cx, -cy)
    shape = shp_scale(shape, xfact=s, yfact=s, origin=(0, 0))

    # 3. Reduce vertex count on curves (TTS likes light meshes)
    if args.simplify > 0:
        shape = shape.simplify(args.simplify * args.width, preserve_topology=True)

    # 4. Extrude into a right prism along Z, centered in thickness
    geoms = getattr(shape, "geoms", [shape])
    mesh = trimesh.util.concatenate(
        [trimesh.creation.extrude_polygon(g, height=args.depth) for g in geoms])
    mesh.apply_translation([0, 0, -args.depth / 2])
    mesh.fix_normals()

    # 5. Texture
    tex, (ox, oy, aw, ah) = render_texture(root, vb, args.texture, bg_rgb)
    png_path = Path(args.out).with_suffix(".png")
    tex.save(png_path)

    # 6. UVs. Model XY -> SVG user space -> texture pixel -> UV (v flipped: image
    #    rows run top-down, OBJ v runs bottom-up). Both caps use the same (x, y)
    #    projection, which is what makes the back face a mirror of the front.
    vbx, vby, vbw, vbh = vb
    X, Y = mesh.vertices[:, 0], mesh.vertices[:, 1]
    sx = X / s + cx
    sy = -(Y / s + cy)
    px = ox + (sx - vbx) / vbw * aw
    py = oy + (sy - vby) / vbh * ah
    T = args.texture
    cap_uv = np.column_stack([px / T, 1 - py / T])
    side_uv = ((PAD / 2) / T, 1 - (PAD / 2) / T)  # center of the top-left margin

    cap_mask = np.abs(mesh.face_normals[:, 2]) > 0.99
    corner_n = vertex_normals_split(mesh, cap_mask)
    mtl = write_obj(args.out, mesh, cap_mask, cap_uv, side_uv, corner_n,
                    mtl_name=Path(args.out).stem, tex_name=png_path.name)

    print(f"Wrote {args.out}: {len(mesh.vertices)} verts, {len(mesh.faces)} tris, "
          f"watertight={mesh.is_watertight}, extents={np.round(mesh.extents, 3)}")
    print(f"Wrote {png_path}: {T}x{T}, background #{'%02x%02x%02x' % bg_rgb}")
    print(f"Wrote {mtl} (for Blender/previews; TTS ignores it)")


if __name__ == "__main__":
    main()
