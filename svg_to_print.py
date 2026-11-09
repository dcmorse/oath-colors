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
# ]
# ///
"""
Turn a piece SVG into files for physical printing:

    pawn.stl      the silhouette extruded into a right prism  -> 3D printer
    pawn-ink.svg  just the black markings, at the same scale  -> decal paper

    ./svg_to_print.py pawn.svg pawn --depth 9

Takes an output BASENAME with no extension; the script appends the suffixes.

The physical size comes from the SVG itself. If the root <svg> has an absolute
width (e.g. "30mm", "1.2in", "85pt"), that maps the viewBox to mm. Otherwise
(Affinity writes width="100%") a user unit is a CSS px, 1/96 inch, as the SVG
spec defines it. --width overrides both, for art whose units mean nothing
(e.g. the v02 exports, buried under scaling transforms).

All sizes are millimetres. The STL is in mm (what slicers assume), sits on
z = 0 and is centered on the origin in XY. The ink SVG declares its page size
in mm, and its page is exactly the silhouette's bounding box, so it imports
into Inkscape at true size. Its markings sit where they will land on the
front face of the prism.

The ink is every painted shape whose fill is black; everything else (the
background-colored silhouette) is dropped. The art is copied as vector paths,
not rasterized.

--width    : override: final size of the longest side of the silhouette, mm
--depth    : extrusion thickness, mm (e.g. 9 or 15)
--simplify : outline tolerance, mm (0 disables)
"""
import argparse
import copy
import re
from pathlib import Path

import numpy as np
import trimesh
from lxml import etree
from shapely.affinity import scale as shp_scale, translate as shp_translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}
UNPAINTED_CONTAINERS = {"clipPath", "defs", "mask", "symbol", "pattern", "marker"}
BLACKS = {"black", "#000", "#000000", "rgb(0,0,0)", "rgb(0%,0%,0%)"}
MM_PER = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6,
          "px": 25.4 / 96, "": 25.4 / 96}


# --------------------------------------------------------------------------- SVG

def parse_svg(svg_path):
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.parse(svg_path, parser).getroot()


def svg_mm_per_unit(root):
    """mm per SVG user unit, from the root's absolute width/height and viewBox.
    Returns (mm_per_unit, how) where `how` says where the scale came from."""
    vb = root.get("viewBox")
    vb = [float(x) for x in re.split(r"[\s,]+", vb.strip())] if vb else None
    for attr, i in (("width", 2), ("height", 3)):
        m = re.fullmatch(r"\s*([0-9.eE+-]+)\s*(mm|cm|in|pt|pc|px|)\s*", root.get(attr, ""))
        if not m:
            continue  # missing, or relative like "100%"
        size_mm = float(m.group(1)) * MM_PER[m.group(2)]
        if vb is None:  # no viewBox: user units are the width's own units
            return MM_PER[m.group(2)], f"{attr}={root.get(attr)!r}"
        return size_mm / vb[i], f"{attr}={root.get(attr)!r} over viewBox"
    return 25.4 / 96, "96 dpi (SVG user units, no absolute size given)"


def _fill_of(el):
    """Effective fill of an element, walking up through parent groups."""
    while el is not None and isinstance(el.tag, str):
        m = re.search(r"(?:^|;)\s*fill\s*:\s*([^;]+)", el.get("style", ""))
        fill = m.group(1).strip() if m else el.get("fill")
        if fill:
            return fill
        el = el.getparent()
    return "black"  # SVG default fill


def _in_unpainted_container(el):
    """True if el sits inside an element whose children are never drawn directly
    (e.g. the <rect> inside an Affinity/Illustrator <clipPath>)."""
    p = el.getparent()
    while p is not None and isinstance(p.tag, str):
        if etree.QName(p).localname in UNPAINTED_CONTAINERS:
            return True
        p = p.getparent()
    return False


def drawn_shapes(root):
    """Shapes that render directly (not clip/mask/defs children), with their fill."""
    for el in root.iter():
        if not isinstance(el.tag, str) or etree.QName(el).localname not in SHAPE_TAGS:
            continue
        if _in_unpainted_container(el):
            continue
        yield el, _fill_of(el)


def is_black(fill):
    return re.sub(r"\s+", "", fill.lower()) in BLACKS


def write_ink(root, out_path, bounds, mm_per_unit):
    """Copy of the SVG keeping only black shapes, its page cropped to `bounds`
    (SVG user units) and sized in mm. Invisible fill:none shapes (e.g. an
    Affinity artboard rect) go too, so the drawing's bounding box in Inkscape
    is just the ink."""
    ink = copy.deepcopy(root)
    kept = 0
    for el, fill in list(drawn_shapes(ink)):
        if is_black(fill):
            kept += 1
        else:
            el.getparent().remove(el)
    if not kept:
        raise SystemExit("No black shapes found in the SVG; nothing to put on the ink.")

    minx, miny, maxx, maxy = bounds
    w, h = maxx - minx, maxy - miny
    ink.set("viewBox", f"{minx:.6f} {miny:.6f} {w:.6f} {h:.6f}")
    ink.set("width", f"{w * mm_per_unit:.4f}mm")
    ink.set("height", f"{h * mm_per_unit:.4f}mm")
    ink.set("preserveAspectRatio", "xMidYMid meet")
    Path(out_path).write_bytes(etree.tostring(ink, xml_declaration=True,
                                              encoding="UTF-8", standalone=False))
    return kept


# -------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("svg")
    ap.add_argument("out", metavar="basename",
                    help="output basename WITHOUT extension; writes basename.stl "
                         "and basename-ink.svg")
    ap.add_argument("--width", type=float,
                    help="override the SVG's own size: longest side of the "
                         "silhouette, mm")
    ap.add_argument("--depth", type=float, required=True,
                    help="extrusion thickness, mm (e.g. 9 or 15)")
    ap.add_argument("--simplify", type=float, default=0.01,
                    help="outline tolerance, mm (default 0.01; 0 disables)")
    args = ap.parse_args()

    if Path(args.out).suffix.lower() in (".stl", ".svg"):
        ap.error(f"output name {args.out!r} must not end in .stl or .svg; "
                 "give a basename only (the script adds the suffixes itself)")
    if (args.width is not None and args.width <= 0) or args.depth <= 0:
        ap.error("--width and --depth must be positive")
    stl_path = Path(args.out + ".stl")
    ink_path = Path(args.out + "-ink.svg")

    root = parse_svg(args.svg)

    # 1. SVG -> closed polygons in SVG user space (y down). Pieces have no holes;
    #    interior rings come from the ink, so keep only exteriors.
    polys = trimesh.load_path(args.svg).polygons_full
    if not polys:
        raise SystemExit("No closed shapes found. Convert strokes/text to filled paths first.")
    shape = unary_union([Polygon(p.exterior) for p in polys])
    bounds = shape.bounds
    minx, miny, maxx, maxy = bounds
    if args.width is not None:
        mm_per_unit = args.width / max(maxx - minx, maxy - miny)
        scale_from = f"--width {args.width:g}"
    else:
        mm_per_unit, scale_from = svg_mm_per_unit(root)

    # 2. Normalize to mm: flip Y (SVG is y-down), center on origin, scale.
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    shape = shp_translate(shape, -cx, -cy)
    shape = shp_scale(shape, xfact=mm_per_unit, yfact=-mm_per_unit, origin=(0, 0))
    if args.simplify > 0:
        shape = shape.simplify(args.simplify, preserve_topology=True)

    # 3. Extrude into a right prism standing on z = 0.
    geoms = getattr(shape, "geoms", [shape])
    mesh = trimesh.util.concatenate(
        [trimesh.creation.extrude_polygon(g, height=args.depth) for g in geoms])
    mesh.fix_normals()
    mesh.export(stl_path)

    # 4. Ink: same bounds, same mm-per-unit, so it matches the prism's face.
    kept = write_ink(root, ink_path, bounds, mm_per_unit)

    w_mm, h_mm = (maxx - minx) * mm_per_unit, (maxy - miny) * mm_per_unit
    print(f"Wrote {stl_path}: {len(mesh.faces)} tris, watertight={mesh.is_watertight}, "
          f"extents={np.round(mesh.extents, 3)} mm")
    print(f"Wrote {ink_path}: {kept} black shape(s), page {w_mm:.3f} x {h_mm:.3f} mm")
    print(f"Scale: {mm_per_unit:.6f} mm per SVG unit, from {scale_from}")


if __name__ == "__main__":
    main()
