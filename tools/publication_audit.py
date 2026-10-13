#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Publication audit for a local-only development branch.

Scans:
1. final diff from the recorded base;
2. blobs introduced anywhere in branch history, including files later deleted;
3. prohibited tracked paths/types;
4. obvious binary media/data additions.

This is a release gate, not a substitute for human review.
"""
from __future__ import annotations
import argparse, pathlib, re, subprocess, sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import public_repo_guard as guard

BANNED_PUBLIC_SUFFIXES={
    ".jpg",".jpeg",".png",".webp",".gif",".mp4",".mov",".avi",".mkv",".wav",
    ".parquet",".arrow",".npy",".npz",".bag",".mcap"
}
# Explicitly approved public release artifacts can be reviewed separately.
RELEASE_PREFIXES=("releases/","keys/")

def git(*args, binary=False):
    return subprocess.check_output(
        ["git",*args],cwd=ROOT,
        text=not binary,stderr=subprocess.DEVNULL
    )

def read_base(path):
    p=ROOT/path
    if not p.is_file():
        raise SystemExit(f"missing base file: {path}")
    base=p.read_text().strip()
    git("cat-file","-e",f"{base}^{{commit}}")
    return base

def introduced_objects(base):
    raw=git("rev-list","--objects",f"{base}..HEAD")
    rows=[]
    for line in raw.splitlines():
        parts=line.split(" ",1)
        sha=parts[0]
        path=parts[1] if len(parts)>1 else ""
        try: typ=git("cat-file","-t",sha).strip()
        except Exception: continue
        if typ=="blob" and path:
            rows.append((sha,path))
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--base-file",default=".local-dev/base-commit")
    args=ap.parse_args()
    base=read_base(args.base_file)
    errors=[]

    # Publication review must refer to an exact committed tree.
    dirty=git("status","--porcelain","--untracked-files=no").strip()
    if dirty:
        errors.append("tracked working tree/index is not clean; commit or revert reviewed changes before publication audit")

    branch=git("branch","--show-current").strip()
    if branch in {"main","master",""}:
        errors.append("publication must not originate from main/master/detached HEAD")

    # Current branch diff guard.
    changed=git("diff","--name-only","--diff-filter=ACMR",f"{base}...HEAD").splitlines()
    for rel in changed:
        p=ROOT/rel
        if not p.is_file(): continue
        data=p.read_bytes()
        errors += guard.scan_bytes(rel,data)
        suf=pathlib.PurePosixPath(rel).suffix.lower()
        if suf in BANNED_PUBLIC_SUFFIXES and not rel.startswith(RELEASE_PREFIXES):
            errors.append(f"{rel}: raw/media/data binary requires explicit publication exclusion")

    # Scan every introduced historical blob, even if later deleted.
    seen=set()
    for sha,rel in introduced_objects(base):
        key=(sha,rel)
        if key in seen: continue
        seen.add(key)
        data=git("cat-file","-p",sha,binary=True)
        errors += [f"history {sha[:10]} {e}" for e in guard.scan_bytes(rel,data)]
        suf=pathlib.PurePosixPath(rel).suffix.lower()
        if suf in BANNED_PUBLIC_SUFFIXES and not rel.startswith(RELEASE_PREFIXES):
            errors.append(f"history {sha[:10]} {rel}: prohibited media/data blob introduced in branch")

    if errors:
        print("PUBLICATION AUDIT: BLOCKED",file=sys.stderr)
        for e in sorted(set(errors)):
            print(" -",e,file=sys.stderr)
        return 1
    print(f"PUBLICATION AUDIT: PASS (base {base[:12]}, branch {branch})")
    return 0

if __name__=="__main__": raise SystemExit(main())
