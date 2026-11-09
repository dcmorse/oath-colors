#!/usr/bin/env -S uv run --quiet --python 3.12 --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "trimesh",
#     "numpy",
#     "networkx",
#     "scipy",
#     "lxml",
# ]
# ///
"""
Arrange copies of STL pieces on one build plate, as a single print job.

    ./stl_plate.py orange-plate.stl orange-pawn-v03.stl:1 orange-warband-v03.stl:14

Each PIECE is an STL path with an optional :COUNT (default 1). Pieces keep
their own heights (a 15 mm pawn and 9 mm warbands print together fine), lie
flat on z = 0 as svg_to_print.py made them, and are never rotated.

Layout is shelf packing of axis-aligned bounding boxes, rows filled tallest
first, aiming for a roughly square plate. Boxes are at least --gap apart, so
pieces can never overlap or touch, whatever their outline. The plate is
centered on the origin; slicers then center it on the bed.

The output format follows the extension:
    .stl  one mesh with many islands (slicers can "split to objects")
    .3mf  one named object per copy, so the slicer sees them separately

--gap : clear space between neighbouring pieces' bounding boxes, mm
--bed : WxH in mm; fail if the plate would not fit (e.g. 256x256)
"""
import argparse
import math
import re
from pathlib import Path

import numpy as np
import trimesh


def piece(spec):
    m = re.fullmatch(r"(.+?)(?::(\d+))?", spec)
    path, count = m.group(1), int(m.group(2) or 1)
    if count < 1:
        raise argparse.ArgumentTypeError(f"{spec!r}: count must be at least 1")
    if not Path(path).is_file():
        raise argparse.ArgumentTypeError(f"{path!r}: no such file")
    return path, count


def bed(spec):
    m = re.fullmatch(r"([0-9.]+)[xX]([0-9.]+)", spec)
    if not m:
        raise argparse.ArgumentTypeError(f"{spec!r}: expected WxH, e.g. 256x256")
    return float(m.group(1)), float(m.group(2))


def shelf_pack(sizes, gap):
    """Place (w, h) boxes in rows. Returns lower-left corners and plate size."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    area = sum((w + gap) * (h + gap) for w, h in sizes)
    row_limit = max(math.sqrt(area), max(w for w, _ in sizes))

    corners = [None] * len(sizes)
    x = y = row_h = plate_w = 0.0
    for i in order:
        w, h = sizes[i]
        if x > 0 and x + w > row_limit:  # start a new row
            y += row_h + gap
            x = row_h = 0.0
        corners[i] = (x, y)
        plate_w = max(plate_w, x + w)
        x += w + gap
        row_h = max(row_h, h)
    return corners, (plate_w, y + row_h)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", help="output .stl or .3mf")
    ap.add_argument("pieces", nargs="+", type=piece, metavar="PIECE.stl[:COUNT]")
    ap.add_argument("--gap", type=float, default=5.0,
                    help="clear space between pieces, mm (default 5)")
    ap.add_argument("--bed", type=bed, help="bed size WxH, mm; error if it won't fit")
    args = ap.parse_args()

    out = Path(args.out)
    if out.suffix.lower() not in (".stl", ".3mf"):
        ap.error(f"output {args.out!r} must end in .stl or .3mf")
    if args.gap < 0:
        ap.error("--gap must not be negative")

    copies = []  # (name, mesh) per copy, each with its footprint's min corner at 0,0
    for path, count in args.pieces:
        mesh = trimesh.load(path, force="mesh")
        mesh.apply_translation(-mesh.bounds[0])  # x, y, z all start at 0
        for k in range(count):
            copies.append((f"{Path(path).stem}-{k + 1}", mesh))

    sizes = [tuple(m.extents[:2]) for _, m in copies]
    corners, (pw, ph) = shelf_pack(sizes, args.gap)

    placed = []
    for (name, mesh), (x, y) in zip(copies, corners):
        m = mesh.copy()
        m.apply_translation([x - pw / 2, y - ph / 2, 0])
        placed.append((name, m))

    # Belt and braces: no two bounding boxes closer than the gap.
    boxes = [m.bounds[:, :2] for _, m in placed]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (a0, a1), (b0, b1) = boxes[i], boxes[j]
            sep = np.maximum(b0 - a1, a0 - b1).max()
            if sep < args.gap - 1e-6:
                raise SystemExit(f"internal error: {placed[i][0]} and {placed[j][0]} "
                                 f"are only {sep:.3f} mm apart")

    if args.bed and (pw > args.bed[0] or ph > args.bed[1]):
        raise SystemExit(f"Plate is {pw:.1f} x {ph:.1f} mm; bed is "
                         f"{args.bed[0]:g} x {args.bed[1]:g} mm. Lower --gap or "
                         "split the job.")

    if out.suffix.lower() == ".stl":
        trimesh.util.concatenate([m for _, m in placed]).export(out)
    else:
        scene = trimesh.Scene()
        for name, m in placed:
            scene.add_geometry(m, geom_name=name, node_name=name)
        scene.export(out)

    height = max(m.extents[2] for _, m in placed)
    print(f"Wrote {out}: {len(placed)} pieces, plate {pw:.1f} x {ph:.1f} mm, "
          f"tallest {height:g} mm, gap {args.gap:g} mm")


if __name__ == "__main__":
    main()
