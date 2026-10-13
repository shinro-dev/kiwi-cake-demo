#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Click cube floor-contact points on a saved BODY/WRIST frame and record pixel (u, v).

No camera, robot, or network access -- this only opens a local image file.

Usage:
    python3 tools/click_measure.py <image.png> [--out measurements.json] [--cubes 8]

Controls:
    left click     record (u, v) for the current cube, in ORIGINAL image pixel
                   coordinates (not the on-screen scaled ones), then advance
                   to the next undecided cube
    n / space      skip the current cube (occluded / not confidently
                   identifiable) -- records no point for it, advances to the
                   next undecided cube
    1-9            jump straight to cube_<N> to revisit or redo it; your next
                   click or skip applies to that cube, then cycling resumes
    u              undo the last click or skip
    s              save now (also autosaves after every click/skip/undo)
    q / Escape     quit
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import tkinter as tk
    from PIL import Image, ImageTk
except ImportError as e:
    sys.exit(f"click_measure: missing dependency ({e}); needs tkinter and PIL, both normally preinstalled")

MARKER_RADIUS = 4
MAX_DISPLAY_SIDE = 1400  # scale down only if the image is bigger than this on screen


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", type=Path, help="path to the saved BODY or WRIST PNG/JPEG frame")
    p.add_argument("--out", type=Path, default=None, help="output JSON path (default: <image>.clicks.json next to the image)")
    p.add_argument("--cubes", type=int, default=8, help="number of cube IDs to cycle through, cube_1..cube_N (default 8, max 9)")
    return p


class ClickMeasure:
    def __init__(self, root, image_path, out_path, num_cubes):
        self.image_path = image_path
        self.out_path = out_path
        self.cube_ids = [f"cube_{i}" for i in range(1, num_cubes + 1)]
        self.decided = {}  # cube_id -> ("click", (u, v)) or ("skip", None)
        self.history = []  # cube_id decision order, for undo
        self.jump_target = None  # set by a digit key, consumed by the next click/skip

        self.image = Image.open(image_path).convert("RGB")
        self.orig_w, self.orig_h = self.image.size

        self.scale = min(1.0, MAX_DISPLAY_SIDE / max(self.orig_w, self.orig_h))
        disp_w, disp_h = int(self.orig_w * self.scale), int(self.orig_h * self.scale)
        self.display_image = self.image.resize((disp_w, disp_h)) if self.scale != 1.0 else self.image

        self.root = root
        self.root.title(f"click_measure: {image_path.name}")

        self.status = tk.Label(root, anchor="w", font=("monospace", 11))
        self.status.pack(fill="x")

        self.tk_image = ImageTk.PhotoImage(self.display_image)
        self.canvas = tk.Canvas(root, width=disp_w, height=disp_h, cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)

        self.canvas.bind("<Button-1>", self.on_click)
        root.bind("<Key>", self.on_key)

        self.update_status()

    def next_undecided(self):
        for cid in self.cube_ids:
            if cid not in self.decided:
                return cid
        return None

    def current_cube(self):
        if self.jump_target is not None:
            return self.jump_target
        return self.next_undecided()

    def update_status(self):
        cur = self.current_cube()
        done = sum(1 for v in self.decided.values() if v[0] == "click")
        skipped = sum(1 for v in self.decided.values() if v[0] == "skip")
        total = len(self.cube_ids)
        tag = " (revisit)" if self.jump_target else ""
        if cur is None:
            msg = f"all {total} cubes decided ({done} clicked, {skipped} skipped) -- 1-9=revisit, u=undo, s=save, q=quit"
        else:
            msg = f"{cur}{tag}: click its floor-contact point, or 'n'=skip  [{done} clicked, {skipped} skipped, {total - done - skipped} left]"
        self.status.config(text=msg)

    def decide(self, cube_id, action, value):
        if cube_id in self.history:
            self.history.remove(cube_id)  # revisiting: keep at most one entry per cube
        self.decided[cube_id] = (action, value)
        self.history.append(cube_id)
        self.jump_target = None
        self.redraw()
        self.update_status()
        self.save()

    def on_click(self, event):
        cube_id = self.current_cube()
        if cube_id is None:
            return
        u = round(event.x / self.scale, 1)
        v = round(event.y / self.scale, 1)
        self.decide(cube_id, "click", (u, v))

    def skip_current(self):
        cube_id = self.current_cube()
        if cube_id is None:
            return
        self.decide(cube_id, "skip", None)

    def draw_marker(self, dx, dy, label):
        r = MARKER_RADIUS
        self.canvas.create_line(dx - r, dy, dx + r, dy, fill="red", width=2)
        self.canvas.create_line(dx, dy - r, dx, dy + r, fill="red", width=2)
        self.canvas.create_text(dx + r + 3, dy, text=label, fill="red", anchor="w", font=("monospace", 10, "bold"))

    def undo(self):
        if not self.history:
            return
        cube_id = self.history.pop()
        del self.decided[cube_id]
        self.redraw()
        self.update_status()
        self.save()

    def redraw(self):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        for cube_id in self.history:
            action, value = self.decided[cube_id]
            if action == "click":
                u, v = value
                self.draw_marker(u * self.scale, v * self.scale, cube_id)

    def save(self):
        clicks = {}
        skipped = []
        for cid, (action, value) in self.decided.items():
            if action == "click":
                u, v = value
                clicks[cid] = {"u": u, "v": v}
            else:
                skipped.append(cid)
        data = {
            "image": self.image_path.name,
            "image_width_px": self.orig_w,
            "image_height_px": self.orig_h,
            "clicks_px": clicks,
            "skipped": skipped,
        }
        self.out_path.write_text(json.dumps(data, indent=2) + "\n")

    def on_key(self, event):
        if event.keysym in ("q", "Escape"):
            self.root.destroy()
        elif event.keysym == "u":
            self.undo()
        elif event.keysym == "s":
            self.save()
            self.status.config(text=f"saved to {self.out_path.name}")
        elif event.keysym in ("n", "space"):
            self.skip_current()
        elif event.char and event.char.isdigit() and event.char != "0":
            idx = int(event.char)
            cid = f"cube_{idx}"
            if cid in self.cube_ids:
                self.jump_target = cid
                self.update_status()


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if not args.image.exists():
        sys.exit(f"click_measure: no such image: {args.image}")
    out_path = args.out or args.image.with_suffix("").with_name(args.image.stem + ".clicks.json")

    root = tk.Tk()
    app = ClickMeasure(root, args.image, out_path, args.cubes)
    root.mainloop()
    done = sum(1 for v in app.decided.values() if v[0] == "click")
    skipped = sum(1 for v in app.decided.values() if v[0] == "skip")
    print(f"click_measure: saved {done} clicked, {skipped} skipped, of {len(app.cube_ids)} cubes, to {out_path}")


if __name__ == "__main__":
    main()
