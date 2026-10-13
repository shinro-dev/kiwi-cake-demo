#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Generate a print-ready ChArUco calibration target (PNG + PDF), at exact physical scale.

No camera, robot, or network access.

Default target: 7x5 squares, 30mm squares, 22mm markers, DICT_4X4_50 --
sized to sit centered on an A4 landscape page with a comfortable white quiet
zone. Both outputs embed a 300 DPI resolution so "print at 100% / actual
size" (NOT "fit to page") reproduces the physical square size exactly.

Usage:
    python3 tools/gen_charuco_target.py --out img/charuco-target
    (writes <out>.png and <out>.pdf)
"""

import argparse
import sys
from pathlib import Path

try:
    import cv2
    from PIL import Image
except ImportError as e:
    sys.exit(f"gen_charuco_target: missing dependency ({e})")

MM_PER_INCH = 25.4


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True, help="output path stem (writes <out>.png and <out>.pdf)")
    p.add_argument("--squares-x", type=int, default=7)
    p.add_argument("--squares-y", type=int, default=5)
    p.add_argument("--square-length-mm", type=float, default=30.0)
    p.add_argument("--marker-length-mm", type=float, default=22.0)
    p.add_argument("--dictionary", default="DICT_4X4_50")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--page-width-mm", type=float, default=297.0, help="A4 landscape width")
    p.add_argument("--page-height-mm", type=float, default=210.0, help="A4 landscape height")
    return p


def mm_to_px(mm, dpi):
    return round(mm / MM_PER_INCH * dpi)


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if not hasattr(cv2, "aruco"):
        sys.exit("gen_charuco_target: cv2.aruco is not available in this Python/OpenCV install")

    dict_id = getattr(cv2.aruco, args.dictionary, None)
    if dict_id is None:
        sys.exit(f"gen_charuco_target: unknown dictionary {args.dictionary!r}")
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)

    board = cv2.aruco.CharucoBoard(
        (args.squares_x, args.squares_y),
        args.square_length_mm / 1000.0,
        args.marker_length_mm / 1000.0,
        dictionary,
    )

    board_w_px = mm_to_px(args.squares_x * args.square_length_mm, args.dpi)
    board_h_px = mm_to_px(args.squares_y * args.square_length_mm, args.dpi)
    board_img = board.generateImage((board_w_px, board_h_px), marginSize=0, borderBits=1)

    page_w_px = mm_to_px(args.page_width_mm, args.dpi)
    page_h_px = mm_to_px(args.page_height_mm, args.dpi)
    if board_w_px > page_w_px or board_h_px > page_h_px:
        sys.exit(
            f"gen_charuco_target: board ({args.squares_x * args.square_length_mm}mm x "
            f"{args.squares_y * args.square_length_mm}mm) does not fit the page "
            f"({args.page_width_mm}mm x {args.page_height_mm}mm)"
        )

    page = Image.new("L", (page_w_px, page_h_px), color=255)
    board_pil = Image.fromarray(board_img)
    off_x = (page_w_px - board_w_px) // 2
    off_y = (page_h_px - board_h_px) // 2
    page.paste(board_pil, (off_x, off_y))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    png_path = args.out.with_suffix(".png")
    pdf_path = args.out.with_suffix(".pdf")
    page.save(png_path, dpi=(args.dpi, args.dpi))
    page.save(pdf_path, resolution=float(args.dpi))

    print(f"gen_charuco_target: {args.squares_x}x{args.squares_y} squares, "
          f"{args.square_length_mm}mm square / {args.marker_length_mm}mm marker, {args.dictionary}")
    print(f"gen_charuco_target: board occupies {board_w_px}x{board_h_px}px "
          f"({board_w_px / args.dpi * MM_PER_INCH:.2f}mm x {board_h_px / args.dpi * MM_PER_INCH:.2f}mm) "
          f"centered on a {args.page_width_mm}x{args.page_height_mm}mm page at {args.dpi} DPI")
    print(f"gen_charuco_target: wrote {png_path} and {pdf_path}")
    print("gen_charuco_target: PRINT THE PDF AT 100% / ACTUAL SIZE -- do not use 'fit to page' or 'shrink to fit'")
    print(f"gen_charuco_target: after printing, measure a square with a ruler -- it must read {args.square_length_mm:.1f}mm")


if __name__ == "__main__":
    main()
