#!/usr/bin/env python3
"""Detect which ngargparser version scaffolded a given project.

ngargparser *copies* framework / template files into each scaffolded project, so a
copied file's exact byte-content is a version fingerprint. Git blob SHA-1 is identical
iff content is identical, so we hash every file in the target project and look for the
same blob in each tagged version of the ngargparser tree.

Two detection layers:
  1. scaffold_version stamp  -- projects scaffolded/synced with ngargparser >= 0.2.0
     carry `[tool.ngargparser] scaffold_version` in pyproject.toml (and a README badge).
     When present this is authoritative.
  2. content fingerprint     -- for older (0.1.x) / unstamped projects, intersect the
     tag-sets of every project file that byte-matches a file in the ngargparser tree.
     Only unmodified scaffolded files match, so user-edited / placeholder-substituted
     files drop out on their own and the intersection narrows to the scaffold version.

Run it from (or pointed at) an ngargparser git checkout -- the tags live there.

    python detect_scaffold_version.py [TARGET] [--repo PATH] [--force-fingerprint] [--json]

TARGET defaults to the current directory; --repo defaults to the ngargparser checkout
this script lives in.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# --- reused from ngargparser/cli.py (SCAFFOLD_STAMP_RE) --------------------------------
SCAFFOLD_STAMP_RE = re.compile(
    r'(\[tool\.ngargparser\][^\[]*?scaffold_version\s*=\s*")([^"]*)(")',
    re.DOTALL,
)
# README badge written by upsert_readme_badge(): .../badge/ngargparser-<ver>-<color>.svg
BADGE_RE = re.compile(r"shields\.io/badge/ngargparser-([^-\s)]+)-")

# Directories that never hold scaffolded fingerprints; skip for speed and to avoid noise.
IGNORE_DIRS = {
    ".git", "build", "dist", ".venv", "venv", "env",
    "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", ".tox", ".idea",
}

# Only the ngargparser package subtree gets copied into projects; index just that.
INDEX_PATHSPEC = "ngargparser"

# Distinctive framework filenames users keep verbatim-named (even if they edit contents).
# Their mere presence flags "this is an ngargparser project" even when nothing fingerprints.
FRAMEWORK_BASENAMES = {"NGArgumentParser.py", "NGChildArgumentParser.py", "core_validators.py"}


def git_blob_hash(data: bytes) -> str:
    """SHA-1 a byte string the way `git hash-object` does (blob header + content)."""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def run_git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )
    return out.stdout


def scan_target(target: Path) -> tuple[dict[str, str], list[str]]:
    """Walk target once. Return (blob-hash -> first relpath) and a list of framework
    marker files found. Empty files are skipped -- the empty blob is a coincidence,
    not a scaffold fingerprint."""
    by_hash: dict[str, str] = {}
    markers: list[str] = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.endswith(".egg-info")]
        for name in files:
            abspath = Path(root) / name
            rel = str(abspath.relative_to(target))
            if name in FRAMEWORK_BASENAMES:
                markers.append(rel)
            try:
                data = abspath.read_bytes()
            except OSError:
                continue
            if not data:  # zero-byte file carries no version signal
                continue
            by_hash.setdefault(git_blob_hash(data), rel)
    return by_hash, markers


def list_tags(repo: Path) -> list[str]:
    return [t for t in run_git(repo, "tag", "--sort=v:refname").splitlines() if t.strip()]


def build_index(repo: Path, tags: list[str]) -> dict[str, dict[str, str]]:
    """blob-hash -> {tag: path-in-tag} across every tag's ngargparser/ subtree."""
    index: dict[str, dict[str, str]] = {}
    for tag in tags:
        out = run_git(repo, "ls-tree", "-r", tag, "--", INDEX_PATHSPEC)
        for line in out.splitlines():
            # "<mode> blob <sha>\t<path>"
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) < 3 or parts[1] != "blob":
                continue
            index.setdefault(parts[2], {}).setdefault(tag, path)
    return index


def order_tags(tags: list[str], subset: set[str]) -> list[str]:
    return [t for t in tags if t in subset]


def format_range(tags: list[str], subset: set[str]) -> str:
    ordered = order_tags(tags, subset)
    if not ordered:
        return "(none)"
    if len(ordered) == 1:
        return ordered[0]
    # contiguous run in the canonical tag order -> "first-last", else comma list
    idx = [tags.index(t) for t in ordered]
    if idx == list(range(idx[0], idx[-1] + 1)):
        return f"{ordered[0]}–{ordered[-1]}"  # en-dash
    return ", ".join(ordered)


def changed_between(repo: Path, lo: str, hi: str) -> list[str]:
    try:
        out = run_git(repo, "diff", "--name-only", lo, hi, "--", INDEX_PATHSPEC)
        return [p for p in out.splitlines() if p.strip()]
    except subprocess.CalledProcessError:
        return []


def fingerprint(target: Path, repo: Path):
    tags = list_tags(repo)
    if not tags:
        raise SystemExit(f"error: no git tags found in ngargparser repo '{repo}'")
    index = build_index(repo, tags)
    target_hashes, markers = scan_target(target)

    matches = []  # list of dicts: {file, template_path, tags(set)}
    for h, relpath in target_hashes.items():
        if h in index:
            matches.append({
                "file": relpath,
                "template_path": next(iter(index[h].values())),
                "tags": set(index[h].keys()),
            })

    matched_sets = [m["tags"] for m in matches]
    intersection: set[str] = set.intersection(*matched_sets) if matched_sets else set()

    # For a synced/upgraded project the per-file ranges are disjoint (empty intersection).
    # Each matched file's content first appeared at the *start* of its range, so the newest
    # such start approximates the level the project was most recently synced to.
    last_sync = None
    if matches and not intersection:
        starts = [order_tags(tags, m["tags"])[0] for m in matches]
        last_sync = max(starts, key=tags.index)

    return {
        "tags": tags,
        "matches": matches,
        "intersection": intersection,
        "last_sync": last_sync,
        "markers": markers,
        "total_files": len(target_hashes),
    }


def detect(target: Path, repo: Path, force_fingerprint: bool):
    result: dict = {"target": str(target), "ngargparser_repo": str(repo)}

    # Layer 1: authoritative stamp ---------------------------------------------------
    stamp = badge = None
    pyproject = target / "pyproject.toml"
    if pyproject.is_file():
        m = SCAFFOLD_STAMP_RE.search(pyproject.read_text(encoding="utf-8", errors="ignore"))
        if m:
            stamp = m.group(2)
    for readme in (target / "README.md", target / "README"):
        if readme.is_file():
            m = BADGE_RE.search(readme.read_text(encoding="utf-8", errors="ignore"))
            if m:
                badge = m.group(1)
                break
    result["scaffold_version_stamp"] = stamp
    result["readme_badge_version"] = badge

    if stamp and not force_fingerprint:
        result["method"] = "stamp"
        result["estimate"] = stamp
        return result

    # Layer 2: content fingerprint ---------------------------------------------------
    fp = fingerprint(target, repo)
    result["method"] = "fingerprint"
    result["files_scanned"] = fp["total_files"]
    result["framework_markers"] = fp["markers"]
    result["matched_files"] = [
        {"file": m["file"], "matched_template": m["template_path"],
         "tag_range": format_range(fp["tags"], m["tags"])}
        for m in sorted(fp["matches"], key=lambda m: m["file"])
    ]
    inter = fp["intersection"]
    if not fp["matches"]:
        result["estimate"] = None
        result["conflict"] = False
    elif inter:
        result["estimate"] = format_range(fp["tags"], inter)
        result["conflict"] = False
        ordered = order_tags(fp["tags"], inter)
        if len(ordered) > 1:
            result["indistinguishable_changes"] = changed_between(repo, ordered[0], ordered[-1])
    else:
        result["estimate"] = None
        result["conflict"] = True
        result["last_sync_estimate"] = fp["last_sync"]
    if stamp:  # only reachable with --force-fingerprint
        result["note"] = f"stamp says {stamp}; fingerprint shown for cross-check"
    return result


# --- pretty printing ------------------------------------------------------------------
C = {"g": "\033[92m", "y": "\033[93m", "r": "\033[91m", "b": "\033[1m", "x": "\033[0m"}


def _c(s: str, color: str) -> str:
    return f"{C[color]}{s}{C['x']}" if sys.stdout.isatty() else s


def print_report(r: dict) -> None:
    print(_c(f"Target: {r['target']}", "b"))
    print(f"ngargparser repo: {r['ngargparser_repo']}")
    print()

    if r["method"] == "stamp":
        print(f"  {_c('scaffold_version stamp', 'b')}: {_c(r['estimate'], 'g')}  (authoritative, >= v0.2.0)")
        if r.get("readme_badge_version"):
            print(f"  README badge: {r['readme_badge_version']}")
        return

    if r.get("scaffold_version_stamp"):
        print(f"  scaffold_version stamp: {_c(r['scaffold_version_stamp'], 'g')} (authoritative)")
        print(f"  {_c('cross-checking via fingerprint (--force-fingerprint)', 'y')}")
        print()

    print(f"Fingerprint ({r['files_scanned']} files scanned, "
          f"{len(r['matched_files'])} byte-identical to the ngargparser tree):")
    for m in r["matched_files"]:
        print(f"    {m['file']:<24} = {m['matched_template']}   [{m['tag_range']}]")
    print()

    est = r.get("estimate")
    if est is None and r.get("conflict"):
        print(_c("  Mixed versions (project was synced/upgraded over time)", "y"))
        print("  Matched files point at disjoint version ranges — see per-file ranges above.")
        last = r.get("last_sync_estimate")
        if last:
            print(f"  {_c('Most recent framework files ≈', 'b')} {_c(last, 'g')}"
                  f"  (likely the last `cli sync` level; older files remain from the original scaffold)")
    elif est is None:
        if r.get("framework_markers"):
            print(_c("  ngargparser project, version indeterminate", "y"))
            print("  Framework files are present (" + ", ".join(r["framework_markers"][:3]) +
                  ("…" if len(r["framework_markers"]) > 3 else "") + ") but every")
            print("  fingerprintable file has been modified, so the version can't be dated.")
        else:
            print(_c("  Estimated version: no match", "y"))
            print("  No file is byte-identical to any ngargparser tag — this project was")
            print("  probably not scaffolded by ngargparser, or has been heavily modified.")
    else:
        print(f"  {_c('Estimated scaffold version:', 'b')} {_c(est, 'g')}")
        changes = r.get("indistinguishable_changes")
        if changes:
            print("  (range not narrowable: those tags differ only in files this project")
            print(f"   doesn't contain — {', '.join(changes)})")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", nargs="?", default=".", help="Project directory (default: cwd)")
    p.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]),
                   help="ngargparser git checkout (default: the repo this script lives in)")
    p.add_argument("--force-fingerprint", action="store_true",
                   help="Run the content fingerprint even when a scaffold_version stamp exists")
    p.add_argument("--json", action="store_true", help="Emit the structured result as JSON")
    args = p.parse_args(argv)

    target = Path(args.target).resolve()
    repo = Path(args.repo).resolve()
    if not target.is_dir():
        print(f"error: target '{target}' is not a directory", file=sys.stderr)
        return 2
    if not (repo / ".git").exists():
        print(f"error: --repo '{repo}' is not a git checkout of ngargparser", file=sys.stderr)
        return 2

    r = detect(target, repo, args.force_fingerprint)
    if args.json:
        # sets aren't JSON-serializable; report already flattened them to strings
        print(json.dumps(r, indent=2, default=list))
    else:
        print_report(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
