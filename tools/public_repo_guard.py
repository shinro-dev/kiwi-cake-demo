#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Local guard for files that may become public.

Scans staged files or the branch diff. It intentionally does not scan raw
ignored experiment directories unless they are accidentally tracked.
"""
from __future__ import annotations
import argparse, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

BANNED_DIRS = {
    "datasets","recordings","captures","checkpoints","outputs","wandb",
    "runs","local-data",".local-dev"
}
BANNED_SUFFIXES = {
    ".pem",".key",".p12",".pfx",".jks",".keystore",".kdbx",
    ".safetensors",".ckpt",".pth",".pt",".onnx",".engine",
    ".sqlite",".sqlite3"
}
BANNED_NAMES = {".env","credentials.json","secrets.json","id_rsa","id_ed25519"}
DEV_SIGNATURE_SUFFIXES = {".asc",".sig"}
PUBLIC_RELEASE_PREFIXES = ("releases/","keys/")

PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("OpenAI-style secret", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("credential assignment", re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|secret[_-]?key|"
        r"hf_token|github_token|openai_api_key)\s*[:=]\s*[\"']?[^\s\"']{8,}"
    )),
    ("local home path", re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/(?!demo\b)[A-Za-z0-9._-]+")),
    ("MAC address", re.compile(r"(?i)\b(?!00:00:5e:00:53:00\b)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")),
]
IPV4 = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")

def git(*args):
    return subprocess.check_output(["git",*args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL)

def documentation_ip(ip):
    try: a,b,c,d = map(int, ip.split("."))
    except Exception: return True
    if max(a,b,c,d) > 255: return True
    return (
        a == 127 or (a,b,c,d)==(0,0,0,0)
        or (a,b,c)==(192,0,2)
        or (a,b,c)==(198,51,100)
        or (a,b,c)==(203,0,113)
    )

def file_list(args):
    if args.staged:
        s=git("diff","--cached","--name-only","--diff-filter=ACMR")
    else:
        base=args.diff_base
        s=git("diff","--name-only","--diff-filter=ACMR",f"{base}...HEAD")
    return [x for x in s.splitlines() if x]

def scan_bytes(rel, data):
    errs=[]
    p=pathlib.PurePosixPath(rel)
    parts=set(p.parts)
    if p.name in BANNED_NAMES: errs.append(f"{rel}: prohibited filename")
    if p.suffix.lower() in BANNED_SUFFIXES: errs.append(f"{rel}: prohibited sensitive/model type")
    if parts & BANNED_DIRS: errs.append(f"{rel}: local/private directory is tracked")
    if p.suffix.lower() in DEV_SIGNATURE_SUFFIXES and not rel.startswith(PUBLIC_RELEASE_PREFIXES):
        errs.append(f"{rel}: development signature file is prohibited outside approved release/key paths")
    if b"\0" in data[:8192]:
        # Binary is permitted only if independently reviewed at publication.
        return errs
    try: text=data.decode("utf-8")
    except UnicodeDecodeError: return errs
    for n,line in enumerate(text.splitlines(),1):
        for label,pat in PATTERNS:
            if pat.search(line): errs.append(f"{rel}:{n}: possible {label}")
        for m in IPV4.finditer(line):
            if not documentation_ip(m.group(0)):
                errs.append(f"{rel}:{n}: non-documentation IPv4 address")
    return errs

def main():
    ap=argparse.ArgumentParser()
    g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged",action="store_true")
    g.add_argument("--diff-base")
    args=ap.parse_args()
    errors=[]
    for rel in sorted(set(file_list(args))):
        p=ROOT/rel
        if p.is_file():
            errors += scan_bytes(rel,p.read_bytes())
    if errors:
        print("PUBLIC REPOSITORY GUARD: BLOCKED",file=sys.stderr)
        for e in errors: print(" -",e,file=sys.stderr)
        return 1
    print("PUBLIC REPOSITORY GUARD: PASS")
    return 0
if __name__=="__main__": raise SystemExit(main())
