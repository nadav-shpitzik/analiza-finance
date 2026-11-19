"""Build this service from GitHub `main`, not from the Repl's workspace.

The generic copy (Analiza-Monday backlog 0214, 2026-10-20). It started in
Monday-API-Extension on 2026-09-25, where it was written for that one
service; this copy takes everything that was fixed there as arguments, so the
same file serves every Replit service. Keep the copies byte-identical: a
change here goes to every repo that carries it.

Why it exists: Replit builds a deployment from whatever the workspace holds
when Republish is pressed. Every change landed on GitHub had to be followed by
a pull in the Repl before it could ship, and a publish without one shipped old
code with no warning (three times on the helper by 19.10, and 0178 here).

The deployment build runs this first. It downloads `main`'s copy of the
service from GitHub, writes it over the build's snapshot of the workspace,
and records what it built in build_info.json, which /health serves. A
Republish then ships GitHub `main` with no pull, however far behind the
workspace is.

Work that exists only in the workspace is never dropped: if the service has
uncommitted edits, or local commits that touch it and are not on origin/main,
the build uses the workspace, as before, and says so. Replit's "Published your
App" commits change no files, so they never count.

A service on Replit's Postgres can name its schema files (--schema). When
main's copy of them differs from the workspace's, the build uses the
workspace and says why: the development database follows the workspace, and
a publish aligns production to it, DROP included (Analiza-Monday backlog
0214, discovery §10.4.4). So a schema change reaches production only after
the workspace - and with it the development database - has taken it.

Two failure rules:

1. Anything that goes wrong before the first file is written (no token,
   GitHub unreachable, a refused token, a tree without a required file)
   builds from the workspace - exactly the build there was before this
   script - and build_info.json says so and why. "Built from the workspace"
   must never look like "built from GitHub" (norm 2026-08-12).
2. A failure while writing files exits 1. The build fails and Replit keeps
   the running deployment, instead of shipping a half-written tree.

Standard library only: it runs before any dependency is installed.

Arguments (the per-service part, written in the build script):
  --repo OWNER/NAME   the service repo on GitHub.
  --subtree PATH      the service's directory inside the repo; empty (the
                      default) is the whole repo.
  --require A,B       files that must exist in main's tree, relative to the
                      subtree. A tree without one is not this service.
  --keep A,B          never written, never deleted (default .replit,replit.nix):
                      Replit already read the deployment config from the
                      workspace before this runs, so main's copy could only
                      disagree with the build that is running. When it
                      differs, build_info says so.
  --generated A,B     workspace edits to these are machine output (npm
                      install rewrites package-lock.json), not work: they
                      do not hold the build back, main's copy replaces them,
                      and build_info says so.
  --schema A,B        files or directories that define the database schema
                      (db.py, migrations/). main's copy differing from the
                      workspace's builds from the workspace, with the reason.
                      Default none: a service with no database never sets it.
  --dir PATH          the service directory on disk (default: the current one).

Environment (Replit Secrets):
  GITHUB_DEPLOY_TOKEN  fine-grained token, Contents read-only on the repo.
                       Unset = build from the workspace, as before.
  DEPLOY_SOURCE        "workspace" forces the old behaviour for one publish.
  DEPLOY_REPO          overrides --repo.
  DEPLOY_REF           default main.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Optional, Sequence

DEFAULT_REF = "main"
DEFAULT_KEEP = (".replit", "replit.nix")
BUILD_INFO_NAME = "build_info.json"
# The largest service repo is a few MB gzipped (2026-10-20). A cap keeps a
# runaway response from filling the build disk.
MAX_TARBALL_BYTES = 200 * 1024 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# (url, token, accept) -> response body. Injected so tests never touch GitHub.
Getter = Callable[[str, str, str], bytes]


class SourceError(Exception):
    """A reason to build from the workspace instead. The message is shown on
    /health, so it must never contain the token."""


class Service:
    """The per-service settings, from the command line."""

    def __init__(
        self,
        repo: str,
        subtree: str = "",
        require: Sequence[str] = (),
        keep: Sequence[str] = DEFAULT_KEEP,
        generated: Sequence[str] = (),
        schema: Sequence[str] = (),
    ):
        self.repo = repo
        self.subtree = subtree.strip("/")
        self.require = tuple(require)
        self.keep = frozenset(keep)
        self.generated = frozenset(generated)
        self.schema = tuple(p.strip("/") for p in schema if p.strip("/"))


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str, token: str, accept: str) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", "analiza-deploy-source")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    # Unredirected: the tarball answer is a redirect to codeload.github.com
    # carrying its own short-lived token, and the Authorization header must
    # not follow it to another host.
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read(MAX_TARBALL_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SourceError("GitHub refused the token (401) - expired or revoked") from None
        if exc.code == 404:
            raise SourceError(
                "GitHub answered 404 - the token cannot see the repo, or the ref does not exist"
            ) from None
        if exc.code == 403:
            raise SourceError("GitHub answered 403 - token scope or rate limit") from None
        raise SourceError(f"GitHub answered HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SourceError(f"GitHub unreachable from the build: {exc.reason}") from None
    except TimeoutError:
        raise SourceError("GitHub timed out") from None
    if len(body) > MAX_TARBALL_BYTES:
        raise SourceError("GitHub response larger than the size cap")
    return body


def resolve_sha(repo: str, ref: str, token: str, get: Getter) -> str:
    raw = get(
        f"https://api.github.com/repos/{repo}/commits/{ref}",
        token,
        "application/vnd.github.sha",
    )
    sha = raw.decode("ascii", "replace").strip()
    if not _SHA_RE.match(sha):
        raise SourceError(f"GitHub did not return a commit id for {ref}")
    return sha


def download_tarball(repo: str, sha: str, token: str, get: Getter) -> bytes:
    return get(
        f"https://api.github.com/repos/{repo}/tarball/{sha}",
        token,
        "application/vnd.github+json",
    )


def _safe_relpath(path: str, subtree: str) -> Optional[str]:
    """The path relative to the subtree, or None if it is not a plain path
    under it. Rejects absolute paths, `..`, and anything else that could
    write outside the service directory."""
    if "\\" in path:
        return None
    parts = PurePosixPath(path).parts
    if not parts or parts[0] == "/" or any(p in ("..", ".", "") for p in parts):
        return None
    sub = PurePosixPath(subtree).parts if subtree else ()
    if tuple(parts[: len(sub)]) != sub or len(parts) == len(sub):
        return None
    return "/".join(parts[len(sub):])


def extract_subtree(tar_bytes: bytes, subtree: str) -> dict[str, tuple[bytes, int]]:
    """{relative path: (content, mode)} for every regular file under the
    subtree.

    GitHub wraps the tree in one top-level `<owner>-<repo>-<sha>/` directory,
    which is dropped. Symlinks, devices and directories are skipped: nothing
    is extracted by tarfile itself, only file bytes are read and written by
    apply_tree, so a hostile member can at most be ignored.
    """
    files: dict[str, tuple[bytes, int]] = {}
    try:
        tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, OSError) as exc:
        raise SourceError(f"the GitHub tarball is not a readable tar.gz: {exc}") from None
    with tar:
        for member in tar:
            if not member.isfile():
                continue
            name = member.name.split("/", 1)
            if len(name) != 2:
                continue
            rel = _safe_relpath(name[1], subtree)
            if rel is None:
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            mode = 0o755 if member.mode & 0o111 else 0o644
            files[rel] = (handle.read(), mode)
    return files


def _git(service_dir: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=service_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _pathspec(excluded: frozenset[str]) -> list[str]:
    """`.` minus the excluded paths. Paths are relative to the service
    directory, which is where git runs."""
    return [".", *(f":(exclude){p}" for p in sorted(excluded))]


def tracked_files(service_dir: Path) -> Optional[set[str]]:
    """Files git tracks in this directory, relative to it - the ones main may
    have deleted. None when the build has no git or no .git; then deleted
    files are left in place and build_info says so."""
    out = _git(service_dir, "ls-files", "-z", "--", ".")
    if out is None:
        return None
    return {p for p in out.split("\0") if p}


def _edited(service_dir: Path, excluded: frozenset[str]) -> Optional[list[str]]:
    """Tracked files that differ from HEAD, staged or not, relative to the
    service directory (git status would print them relative to the repo
    root, which a subtree service cannot compare with its own paths)."""
    out = _git(service_dir, "diff", "--name-only", "--relative", "HEAD", "--", *_pathspec(excluded))
    if out is None:
        return None
    return [line for line in out.splitlines() if line.strip()]


def local_work(service_dir: Path, ref: str, svc: Service) -> Optional[str]:
    """What the service holds that GitHub does not, as a sentence for
    /health, or None when there is nothing. Uncommitted edits to tracked
    files count, and so do local commits that touch the service and are not
    on origin/<ref>. Empty commits (Replit's "Published your App") do not,
    and neither do edits to kept or generated files. Without git, or without
    an origin/<ref> to compare with, only what can be checked is checked."""
    ignored = svc.keep | svc.generated
    edited = _edited(service_dir, ignored)
    if edited is None:
        return None
    found = []
    if edited:
        found.append(f"uncommitted edits ({', '.join(edited[:3])}{', ...' if len(edited) > 3 else ''})")
    local = _git(service_dir, "rev-list", f"origin/{ref}..HEAD")
    commits = []
    for sha in (local or "").split():
        touched = _git(
            service_dir, "diff-tree", "--no-commit-id", "--name-only", "-r", sha, "--", *_pathspec(ignored)
        )
        if touched and touched.strip():
            commits.append(sha[:7])
    if commits:
        found.append(f"{len(commits)} local commit(s) not on GitHub ({', '.join(commits[:3])})")
    if not found:
        return None
    return (
        "the workspace has work in this service that is not on GitHub: "
        + "; ".join(found)
        + ". Push it, then Republish to build from GitHub"
    )


def workspace_state(service_dir: Path) -> dict:
    head = _git(service_dir, "rev-parse", "HEAD")
    status = _git(service_dir, "status", "--porcelain", "--untracked-files=no", "--", ".")
    return {
        "sha": head.strip() if head else None,
        "dirty": None if status is None else bool(status.strip()),
    }


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    # Write-then-rename, never in place: the build script is executing while
    # this runs, and bash reads a script incrementally from its open file. A
    # rename leaves the old inode under the running shell untouched.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.deploy-tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def apply_tree(
    files: Mapping[str, tuple[bytes, int]],
    service_dir: Path,
    tracked: Optional[set[str]],
    keep: frozenset[str] = frozenset(),
) -> Optional[int]:
    """Write `files` over service_dir. Delete files git tracked here that
    main no longer has. Files git never tracked (.venv, node_modules, logs,
    local notes) and kept files are left alone. Returns the number deleted,
    or None when there was no git to say which files were tracked."""
    for rel, (data, mode) in sorted(files.items()):
        if rel in keep:
            continue
        _write_atomic(service_dir / rel, data, mode)
    if tracked is None:
        return None
    deleted = 0
    for rel in sorted(tracked - set(files) - keep):
        target = service_dir / rel
        if target.is_file() or target.is_symlink():
            target.unlink()
            deleted += 1
    return deleted


def kept_differences(files: Mapping[str, tuple[bytes, int]], service_dir: Path, keep: frozenset[str]) -> list[str]:
    """Kept files whose copy on main differs from the workspace's: the
    deployment config that is running is not the one on main."""
    out = []
    for rel in sorted(keep):
        on_main = files.get(rel)
        try:
            here = (service_dir / rel).read_bytes()
        except OSError:
            here = None
        if (on_main[0] if on_main else None) != here:
            out.append(rel)
    return out


def _under(rel: str, paths: Sequence[str]) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in paths)


def _files_on_disk(service_dir: Path, paths: Sequence[str]) -> set[str]:
    """Without git: every file under `paths`, bytecode caches aside."""
    out = set()
    for p in paths:
        root = service_dir / p
        if root.is_file():
            out.add(p)
        elif root.is_dir():
            for f in root.rglob("*"):
                rel = f.relative_to(service_dir).as_posix()
                if f.is_file() and "__pycache__" not in f.parts and not rel.endswith(".pyc"):
                    out.add(rel)
    return out


def schema_differences(
    files: Mapping[str, tuple[bytes, int]],
    service_dir: Path,
    schema: Sequence[str],
    tracked: Optional[set[str]],
) -> list[str]:
    """Schema files whose copy on main differs from the workspace's, or that
    exist on one side only. The workspace side is what git tracks there (an
    untracked scratch file is not schema), or every file on disk without git."""
    if not schema:
        return []
    here = {p for p in tracked if _under(p, schema)} if tracked is not None else _files_on_disk(service_dir, schema)
    out = []
    for rel in sorted({p for p in files if _under(p, schema)} | here):
        on_main = files.get(rel)
        try:
            data = (service_dir / rel).read_bytes()
        except OSError:
            data = None
        if (on_main[0] if on_main else None) != data:
            out.append(rel)
    return out


def write_build_info(service_dir: Path, info: dict) -> None:
    _write_atomic(
        service_dir / BUILD_INFO_NAME,
        (json.dumps(info, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        0o644,
    )


def load_build_info(path: Path) -> dict:
    """What a Python service's /health reports under `build`. A missing or
    unreadable file is reported as such, never as an empty success. (Node
    services read the same file in their own health code.)"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "source": None,
            "sha": None,
            "reason": "no build_info.json - a dev run, or built before deploy_source.py existed",
        }
    except (OSError, ValueError) as exc:
        return {"source": None, "sha": None, "reason": f"build_info.json unreadable: {exc}"}
    if not isinstance(data, dict):
        return {"source": None, "sha": None, "reason": "build_info.json is not an object"}
    # One click from /health to the exact commit that is live. Only for a
    # GitHub build - a workspace build has no commit on GitHub to point at,
    # and a link to one would be a lie.
    if data.get("source") == "github" and data.get("sha") and data.get("repo"):
        data = {**data, "commit_url": f"https://github.com/{data['repo']}/commit/{data['sha']}"}
    return data


def run(
    svc: Service,
    env: Mapping[str, str] = os.environ,
    service_dir: Optional[Path] = None,
    get: Getter = http_get,
) -> int:
    service_dir = service_dir or Path.cwd()
    repo = env.get("DEPLOY_REPO", "").strip() or svc.repo
    ref = env.get("DEPLOY_REF", "").strip() or DEFAULT_REF
    info: dict = {"built_at": _utc_now(), "repo": repo}
    writing = False

    def build_from_workspace(reason: str) -> int:
        info.update({"source": "workspace", **workspace_state(service_dir), "reason": reason})
        write_build_info(service_dir, info)
        print(f"deploy_source: building from the WORKSPACE, not GitHub - {reason}")
        return 0

    try:
        if env.get("DEPLOY_SOURCE", "").strip().lower() == "workspace":
            return build_from_workspace("DEPLOY_SOURCE=workspace")
        if not _REPO_RE.match(repo):
            return build_from_workspace(f"not a GitHub repo name: {repo!r}")
        token = env.get("GITHUB_DEPLOY_TOKEN", "").strip()
        if not token:
            return build_from_workspace("no GITHUB_DEPLOY_TOKEN in the build environment")
        unpushed = local_work(service_dir, ref, svc)
        if unpushed:
            return build_from_workspace(unpushed)
        try:
            sha = resolve_sha(repo, ref, token, get)
            files = extract_subtree(download_tarball(repo, sha, token, get), svc.subtree)
            missing = [f for f in svc.require if f not in files]
            if missing:
                where = f"{svc.subtree}/{missing[0]}" if svc.subtree else missing[0]
                raise SourceError(f"{ref} @ {sha[:7]} has no {where}")
        except SourceError as exc:
            return build_from_workspace(str(exc))
        tracked = tracked_files(service_dir)
        schema = schema_differences(files, service_dir, svc.schema, tracked)
        if schema:
            return build_from_workspace(
                f"schema change on {ref} @ {sha[:7]} not in the workspace yet "
                f"({', '.join(schema[:3])}{', ...' if len(schema) > 3 else ''}): the development "
                "database lacks it. Sync from GitHub, run the schema step, then Republish in the UI"
            )
        notes = []
        regenerated = _edited(service_dir, frozenset()) or []
        regenerated = sorted(p for p in regenerated if p in svc.generated)
        if regenerated:
            notes.append(f"workspace edits to {', '.join(regenerated)} were replaced by main's copy")
        differs = kept_differences(files, service_dir, svc.keep)
        if differs:
            notes.append(
                f"{', '.join(differs)} on main differs from the workspace: the deployment config "
                "in use is the workspace's until a Sync from GitHub"
            )
        writing = True
        deleted = apply_tree(files, service_dir, tracked, svc.keep)
        info.update({"source": "github", "ref": ref, "sha": sha, "files": len(files), "deleted": deleted})
        if deleted is None:
            notes.append("no git in the build: files deleted on main were not removed")
        elif _git(service_dir, "rev-parse", "--verify", "--quiet", f"origin/{ref}") is None:
            # local_work could check edits but not commits - say so rather
            # than let "nothing unpushed" and "could not look" read the same.
            notes.append(f"no origin/{ref} in the workspace: unpushed commits were not checked")
        if notes:
            info["note"] = "; ".join(notes)
        write_build_info(service_dir, info)
        print(
            f"deploy_source: building GitHub {repo} {ref} @ {sha[:7]} "
            f"({len(files)} files, {'?' if deleted is None else deleted} deleted)"
        )
        return 0
    except Exception as exc:  # a bug here must not ship a half-written tree
        if writing:
            print(
                f"deploy_source: FAILED while writing files ({type(exc).__name__}: {exc}). "
                "Failing the build so the running deployment stays.",
                file=sys.stderr,
            )
            return 1
        return build_from_workspace(f"deploy_source crashed before writing: {type(exc).__name__}: {exc}")


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Build this service from GitHub main.")
    p.add_argument("--repo", required=True)
    p.add_argument("--subtree", default="")
    p.add_argument("--require", type=_list, default=[])
    p.add_argument("--keep", type=_list, default=list(DEFAULT_KEEP))
    p.add_argument("--generated", type=_list, default=[])
    p.add_argument("--schema", type=_list, default=[])
    p.add_argument("--dir", type=Path, default=None)
    a = p.parse_args(argv)
    svc = Service(a.repo, a.subtree, a.require, a.keep, a.generated, a.schema)
    return run(svc, service_dir=a.dir.resolve() if a.dir else None)


if __name__ == "__main__":
    sys.exit(main())
