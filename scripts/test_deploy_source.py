"""deploy_source.py - the build step that ships GitHub `main` instead of the
Repl's workspace. The HTTP layer is faked; git runs for real where a test
needs it, since pathspecs and relative paths are the part most easily wrong.

Standard library only (unittest), so it runs in a Node repo's CI with the
system python3: `python3 -m unittest discover -s scripts -p 'test_*.py'`.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_source as ds  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
ROOT = "owner-service-0123456"
TOKEN = "ghp_test_token_value"
ENV = {"GITHUB_DEPLOY_TOKEN": TOKEN}

# A Node service at the repo root - the shape of Analiza-Deal-Breakdown.
NODE = ds.Service(
    "owner/service",
    require=("server.js", "package.json"),
    generated=("package-lock.json",),
)
# A Python service in a subdirectory - the shape of Monday-API-Extension.
PY = ds.Service("owner/helper", subtree="artifacts/api", require=("main.py", "requirements.txt"))

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _tarball(files: dict[str, bytes], extra=()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"{ROOT}/{path}")
            info.size = len(data)
            info.mode = 0o755 if path.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _node_tree(**overrides: bytes) -> dict[str, bytes]:
    tree = {
        "server.js": b"// main on GitHub\n",
        "package.json": b'{"name": "main"}\n',
        "package-lock.json": b'{"lock": "main"}\n',
        "src/new.js": b"// added on main\n",
        "scripts/build.sh": b"#!/bin/bash\n",
        ".replit": b"# main's deployment config\n",
    }
    tree.update(overrides)
    return tree


def _getter(tar_bytes: bytes, calls=None, sha_body: bytes = SHA.encode() + b"\n"):
    def get(url, token, accept):
        if calls is not None:
            calls.append((url, token, accept))
        if "/commits/" in url:
            return sha_body
        if "/tarball/" in url:
            return tar_bytes
        raise AssertionError(f"unexpected url {url}")

    return get


def _never_called(url, token, accept):
    raise AssertionError("GitHub must not be contacted")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True)


def _info(svc_dir: Path) -> dict:
    return json.loads((svc_dir / ds.BUILD_INFO_NAME).read_text())


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def node_service(self) -> Path:
        """A workspace snapshot with no git: the shape of a build image."""
        svc = self.tmp / "svc"
        (svc / "src").mkdir(parents=True)
        (svc / "server.js").write_text("// workspace\n")
        (svc / "package.json").write_text('{"name": "old"}\n')
        (svc / "src" / "old.js").write_text("// deleted on main\n")
        (svc / ".replit").write_text("# the workspace's deployment config\n")
        (svc / "local_notes.txt").write_text("never tracked\n")
        (svc / "node_modules" / "x").mkdir(parents=True)
        (svc / "node_modules" / "x" / "index.js").write_text("dep\n")
        return svc

    def git_workspace(self) -> tuple[Path, callable]:
        """A Repl-like checkout: a GitHub-like origin, a clone, the service at
        the root, committed and pushed."""
        origin = self.tmp / "origin.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        repo = self.tmp / "ws"
        _git(self.tmp, "clone", "-q", str(origin), str(repo))
        _git(repo, "checkout", "-q", "-B", "main")
        (repo / "src").mkdir()
        (repo / "server.js").write_text("// workspace\n")
        (repo / "package.json").write_text('{"name": "old"}\n')
        (repo / "package-lock.json").write_text('{"lock": "old"}\n')
        (repo / "src" / "old.js").write_text("// deleted on main\n")
        (repo / ".replit").write_text("# the workspace's deployment config\n")
        (repo / ".gitignore").write_text("node_modules/\nbuild_info.json\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "push", "-q", "origin", "main")
        return repo, lambda *args: _git(repo, *args)


# -- falling back to the workspace ----------------------------------------------


class Fallback(_Tmp):
    def test_no_token_builds_from_the_workspace_and_says_why(self):
        svc = self.node_service()
        self.assertEqual(ds.run(NODE, env={}, service_dir=svc, get=_never_called), 0)
        info = _info(svc)
        self.assertEqual(info["source"], "workspace")
        self.assertIn("GITHUB_DEPLOY_TOKEN", info["reason"])
        # No `ref` on a fallback: "main" next to a workspace sha would read as main.
        self.assertNotIn("ref", info)
        self.assertEqual(info["repo"], "owner/service")
        self.assertEqual((svc / "server.js").read_text(), "// workspace\n")

    def test_deploy_source_workspace_forces_the_old_build(self):
        svc = self.node_service()
        env = {**ENV, "DEPLOY_SOURCE": "Workspace "}
        self.assertEqual(ds.run(NODE, env=env, service_dir=svc, get=_never_called), 0)
        self.assertEqual(_info(svc)["reason"], "DEPLOY_SOURCE=workspace")

    def test_a_malformed_repo_name_never_reaches_a_url(self):
        svc = self.node_service()
        env = {**ENV, "DEPLOY_REPO": "owner/repo/../x"}
        self.assertEqual(ds.run(NODE, env=env, service_dir=svc, get=_never_called), 0)
        self.assertIn("not a GitHub repo name", _info(svc)["reason"])

    def test_github_error_falls_back_with_the_reason(self):
        svc = self.node_service()

        def refused(url, token, accept):
            raise ds.SourceError("GitHub answered 404 - the token cannot see the repo, or the ref does not exist")

        self.assertEqual(ds.run(NODE, env=ENV, service_dir=svc, get=refused), 0)
        info = _info(svc)
        self.assertEqual(info["source"], "workspace")
        self.assertIn("404", info["reason"])
        self.assertNotIn(TOKEN, json.dumps(info))
        self.assertEqual((svc / "server.js").read_text(), "// workspace\n")

    def test_a_non_sha_answer_falls_back(self):
        svc = self.node_service()
        get = _getter(_tarball(_node_tree()), sha_body=b"<html>login</html>")
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=svc, get=get), 0)
        self.assertIn("did not return a commit id", _info(svc)["reason"])

    def test_a_tree_without_a_required_file_falls_back_untouched(self):
        svc = self.node_service()
        tree = {"package.json": b"{}\n"}
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=svc, get=_getter(_tarball(tree))), 0)
        info = _info(svc)
        self.assertEqual(info["source"], "workspace")
        self.assertIn("has no server.js", info["reason"])
        self.assertEqual((svc / "package.json").read_text(), '{"name": "old"}\n')

    def test_a_crash_before_writing_falls_back(self):
        svc = self.node_service()

        def boom(url, token, accept):
            raise RuntimeError("boom")

        self.assertEqual(ds.run(NODE, env=ENV, service_dir=svc, get=boom), 0)
        self.assertEqual(_info(svc)["reason"], "deploy_source crashed before writing: RuntimeError: boom")


# -- building from GitHub --------------------------------------------------------


class FromGitHub(_Tmp):
    def test_main_replaces_tracked_files_and_keeps_untracked_and_kept_ones(self):
        svc = self.node_service()
        calls: list = []
        tracked = {"server.js", "package.json", "src/old.js", ".replit"}
        with mock.patch.object(ds, "tracked_files", lambda d: tracked):
            rc = ds.run(NODE, env=ENV, service_dir=svc, get=_getter(_tarball(_node_tree()), calls))
        self.assertEqual(rc, 0)
        self.assertEqual((svc / "server.js").read_text(), "// main on GitHub\n")
        self.assertEqual((svc / "package-lock.json").read_text(), '{"lock": "main"}\n')
        self.assertTrue((svc / "src" / "new.js").exists())
        self.assertFalse((svc / "src" / "old.js").exists())
        self.assertTrue((svc / "local_notes.txt").exists())
        self.assertTrue((svc / "node_modules" / "x" / "index.js").exists())
        self.assertTrue(os.access(svc / "scripts" / "build.sh", os.X_OK))
        # The deployment config Replit already read stays as it was.
        self.assertEqual((svc / ".replit").read_text(), "# the workspace's deployment config\n")
        self.assertFalse(list(svc.rglob("*.deploy-tmp")))

        info = _info(svc)
        self.assertEqual(info["source"], "github")
        self.assertEqual(info["sha"], SHA)
        self.assertEqual(info["ref"], "main")
        self.assertEqual(info["repo"], "owner/service")
        self.assertEqual(info["files"], 6)
        self.assertEqual(info["deleted"], 1)
        self.assertIn(".replit on main differs from the workspace", info["note"])
        self.assertEqual(
            [c[0] for c in calls],
            [
                "https://api.github.com/repos/owner/service/commits/main",
                f"https://api.github.com/repos/owner/service/tarball/{SHA}",
            ],
        )
        self.assertTrue(all(c[1] == TOKEN for c in calls))

    def test_a_kept_file_deleted_on_main_is_not_deleted(self):
        svc = self.node_service()
        tree = _node_tree()
        del tree[".replit"]
        with mock.patch.object(ds, "tracked_files", lambda d: {".replit", "server.js"}):
            self.assertEqual(ds.run(NODE, env=ENV, service_dir=svc, get=_getter(_tarball(tree))), 0)
        self.assertTrue((svc / ".replit").exists())
        self.assertEqual(_info(svc)["deleted"], 0)

    def test_a_subtree_service_takes_only_its_subtree(self):
        svc = self.tmp / "svc"
        svc.mkdir()
        (svc / "main.py").write_text("# workspace\n")
        tree = {
            "artifacts/api/main.py": b"# main on GitHub\n",
            "artifacts/api/requirements.txt": b"fastapi\n",
            "artifacts/api/routes/r.py": b"# route\n",
            "README.md": b"repo root, outside the subtree\n",
            "artifacts/other/index.ts": b"// another artifact\n",
        }
        with mock.patch.object(ds, "tracked_files", lambda d: set()):
            self.assertEqual(ds.run(PY, env=ENV, service_dir=svc, get=_getter(_tarball(tree))), 0)
        self.assertEqual((svc / "main.py").read_text(), "# main on GitHub\n")
        self.assertTrue((svc / "routes" / "r.py").exists())
        self.assertFalse((svc / "README.md").exists())
        self.assertFalse((svc / "artifacts").exists())
        self.assertEqual(_info(svc)["files"], 3)

    def test_a_subtree_requirement_names_the_full_path(self):
        svc = self.tmp / "svc"
        svc.mkdir()
        tree = {"artifacts/api/requirements.txt": b"x\n"}
        self.assertEqual(ds.run(PY, env=ENV, service_dir=svc, get=_getter(_tarball(tree))), 0)
        self.assertIn("has no artifacts/api/main.py", _info(svc)["reason"])

    def test_repo_and_ref_can_be_overridden(self):
        svc = self.node_service()
        calls: list = []
        env = {**ENV, "DEPLOY_REPO": "someone/fork", "DEPLOY_REF": "hotfix"}
        with mock.patch.object(ds, "tracked_files", lambda d: None):
            self.assertEqual(ds.run(NODE, env=env, service_dir=svc, get=_getter(_tarball(_node_tree()), calls)), 0)
        self.assertEqual(calls[0][0], "https://api.github.com/repos/someone/fork/commits/hotfix")
        self.assertEqual(_info(svc)["ref"], "hotfix")
        self.assertEqual(_info(svc)["repo"], "someone/fork")

    def test_without_git_deleted_files_stay_and_build_info_says_so(self):
        svc = self.node_service()
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=svc, get=_getter(_tarball(_node_tree()))), 0)
        info = _info(svc)
        self.assertIsNone(info["deleted"])
        self.assertIn("not removed", info["note"])
        self.assertTrue((svc / "src" / "old.js").exists())

    def test_unsafe_members_are_ignored(self):
        svc = self.node_service()
        link = tarfile.TarInfo(f"{ROOT}/passwd")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        escape = tarfile.TarInfo(f"{ROOT}/../../../escaped.js")
        escape.size = 4
        tar = _tarball(_node_tree(), extra=[(link, None), (escape, b"evil")])
        with mock.patch.object(ds, "tracked_files", lambda d: set()):
            self.assertEqual(ds.run(NODE, env=ENV, service_dir=svc, get=_getter(tar)), 0)
        self.assertEqual(_info(svc)["source"], "github")
        self.assertFalse((svc / "passwd").exists() or (svc / "passwd").is_symlink())
        self.assertFalse(list(self.tmp.rglob("escaped.js")))

    def test_a_write_failure_fails_the_build(self):
        svc = self.node_service()

        def broken_write(path, data, mode):
            raise OSError("disk full")

        with mock.patch.object(ds, "tracked_files", lambda d: set()), mock.patch.object(
            ds, "_write_atomic", broken_write
        ):
            rc = ds.run(NODE, env=ENV, service_dir=svc, get=_getter(_tarball(_node_tree())))
        self.assertEqual(rc, 1)
        self.assertFalse((svc / ds.BUILD_INFO_NAME).exists())


# -- work that only the workspace has, with a real git ---------------------------


class WorkspaceWork(_Tmp):
    def test_real_git_deletes_and_keeps(self):
        repo, git = self.git_workspace()
        (repo / "untracked.txt").write_text("local\n")
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=repo, get=_getter(_tarball(_node_tree()))), 0)
        info = _info(repo)
        self.assertEqual(info["source"], "github")
        self.assertFalse((repo / "src" / "old.js").exists())
        # .gitignore is tracked and not on main's tree: deleted like any other file.
        self.assertEqual(info["deleted"], 2)
        self.assertTrue((repo / "untracked.txt").exists())
        self.assertTrue((repo / ".replit").exists())
        self.assertNotIn("unpushed commits were not checked", info.get("note", ""))

    def test_an_uncommitted_edit_keeps_the_workspace(self):
        repo, git = self.git_workspace()
        (repo / "server.js").write_text("// edited in the Repl\n")
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=repo, get=_never_called), 0)
        info = _info(repo)
        self.assertEqual(info["source"], "workspace")
        self.assertIn("uncommitted edits (server.js)", info["reason"])
        self.assertIs(info["dirty"], True)
        self.assertEqual((repo / "server.js").read_text(), "// edited in the Repl\n")

    def test_an_unpushed_commit_keeps_the_workspace(self):
        repo, git = self.git_workspace()
        (repo / "server.js").write_text("// fixed by the Replit Agent\n")
        git("commit", "-q", "-am", "Agent fix")
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=repo, get=_never_called), 0)
        info = _info(repo)
        self.assertEqual(info["source"], "workspace")
        self.assertIn("1 local commit(s) not on GitHub", info["reason"])
        self.assertIs(info["dirty"], False)

    def test_publish_markers_are_not_work(self):
        repo, git = self.git_workspace()
        git("commit", "-q", "--allow-empty", "-m", "Published your App")
        self.assertIsNone(ds.local_work(repo, "main", NODE))
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=repo, get=_getter(_tarball(_node_tree()))), 0)
        self.assertEqual(_info(repo)["source"], "github")

    def test_a_regenerated_lockfile_does_not_hold_the_build_back(self):
        """npm install in the Repl rewrites package-lock.json on every run of
        the Start workflow. That is not work, and main's copy replaces it."""
        repo, git = self.git_workspace()
        (repo / "package-lock.json").write_text('{"lock": "rewritten by npm install"}\n')
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=repo, get=_getter(_tarball(_node_tree()))), 0)
        info = _info(repo)
        self.assertEqual(info["source"], "github")
        self.assertIn("package-lock.json were replaced by main's copy", info["note"])
        self.assertEqual((repo / "package-lock.json").read_text(), '{"lock": "main"}\n')

    def test_a_kept_file_edited_in_the_repl_does_not_hold_the_build_back(self):
        """Replit edits .replit itself (ports, modules). It is never taken from
        GitHub, so its edits are not work this build would drop."""
        repo, git = self.git_workspace()
        (repo / ".replit").write_text("# edited by Replit\n")
        git("commit", "-q", "-am", "Replit config")
        tree = _node_tree(**{".replit": b"# edited by Replit\n"})
        self.assertIsNone(ds.local_work(repo, "main", NODE))
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=repo, get=_getter(_tarball(tree))), 0)
        info = _info(repo)
        self.assertEqual(info["source"], "github")
        self.assertEqual((repo / ".replit").read_text(), "# edited by Replit\n")
        self.assertNotIn("note", info)

    def test_a_subtree_service_ignores_work_outside_it(self):
        repo, git = self.git_workspace()
        svc = repo / "artifacts" / "api"
        svc.mkdir(parents=True)
        (svc / "main.py").write_text("# workspace\n")
        git("add", ".")
        git("commit", "-q", "-m", "add api")
        git("push", "-q", "origin", "main")
        (repo / "server.js").write_text("// edited outside the subtree\n")
        self.assertIsNone(ds.local_work(svc, "main", PY))
        (svc / "main.py").write_text("# edited inside\n")
        self.assertIn("uncommitted edits (main.py)", ds.local_work(svc, "main", PY))


# A service on Replit's Postgres: its schema lives in migrations/ and db.js.
PG = ds.Service(
    "owner/service",
    require=("server.js", "package.json"),
    generated=("package-lock.json",),
    schema=("migrations", "db.js"),
)


class Schema(_Tmp):
    """--schema: a schema change on main the workspace has not taken builds from
    the workspace, because the development database follows the workspace and a
    publish aligns production to it, DROP included."""

    def pg_workspace(self):
        repo, git = self.git_workspace()
        (repo / "migrations").mkdir()
        (repo / "migrations" / "001.sql").write_text("CREATE TABLE a (id int);\n")
        (repo / "db.js").write_text("// schema step\n")
        git("add", ".")
        git("commit", "-q", "-m", "schema")
        git("push", "-q", "origin", "main")
        return repo, git

    def pg_tree(self, **overrides: bytes) -> dict[str, bytes]:
        return _node_tree(**{"migrations/001.sql": b"CREATE TABLE a (id int);\n", "db.js": b"// schema step\n", **overrides})

    def run_pg(self, repo, tree):
        return ds.run(PG, env=ENV, service_dir=repo, get=_getter(_tarball(tree)))

    def test_the_same_schema_builds_from_github(self):
        repo, git = self.pg_workspace()
        self.assertEqual(self.run_pg(repo, self.pg_tree()), 0)
        self.assertEqual(_info(repo)["source"], "github")
        self.assertEqual((repo / "server.js").read_text(), "// main on GitHub\n")

    def test_a_new_migration_on_main_keeps_the_workspace_and_names_it(self):
        repo, git = self.pg_workspace()
        tree = self.pg_tree(**{"migrations/002.sql": b"CREATE TABLE b (id int);\n"})
        self.assertEqual(self.run_pg(repo, tree), 0)
        info = _info(repo)
        self.assertEqual(info["source"], "workspace")
        self.assertIn(f"schema change on main @ {SHA[:7]}", info["reason"])
        self.assertIn("migrations/002.sql", info["reason"])
        self.assertIn("Republish in the UI", info["reason"])
        self.assertLessEqual(len(info["reason"]), 300, "Node /health cuts build_info strings at 300")
        self.assertEqual((repo / "server.js").read_text(), "// workspace\n", "nothing written")
        self.assertFalse((repo / "migrations" / "002.sql").exists())

    def test_an_edited_schema_file_on_main_keeps_the_workspace(self):
        repo, git = self.pg_workspace()
        self.assertEqual(self.run_pg(repo, self.pg_tree(**{"db.js": b"// adds a column\n"})), 0)
        info = _info(repo)
        self.assertEqual(info["source"], "workspace")
        self.assertIn("(db.js)", info["reason"])

    def test_a_migration_deleted_on_main_keeps_the_workspace(self):
        repo, git = self.pg_workspace()
        tree = self.pg_tree()
        del tree["migrations/001.sql"]
        self.assertEqual(self.run_pg(repo, tree), 0)
        self.assertIn("migrations/001.sql", _info(repo)["reason"])

    def test_an_untracked_scratch_file_is_not_schema(self):
        repo, git = self.pg_workspace()
        (repo / "migrations" / "scratch.sql").write_text("-- trying something\n")
        self.assertEqual(self.run_pg(repo, self.pg_tree()), 0)
        self.assertEqual(_info(repo)["source"], "github")

    def test_after_a_sync_the_same_change_builds_from_github(self):
        """The intended flow: Sync brings the migration into the workspace (and
        the schema step runs it on the development database); then main builds."""
        repo, git = self.pg_workspace()
        (repo / "migrations" / "002.sql").write_text("CREATE TABLE b (id int);\n")
        git("add", ".")
        git("commit", "-q", "-m", "migration 002")
        git("push", "-q", "origin", "main")
        tree = self.pg_tree(**{"migrations/002.sql": b"CREATE TABLE b (id int);\n"})
        self.assertEqual(self.run_pg(repo, tree), 0)
        self.assertEqual(_info(repo)["source"], "github")

    def test_without_git_the_files_on_disk_are_compared_bytecode_aside(self):
        svc = self.node_service()
        (svc / "schema" / "__pycache__").mkdir(parents=True)
        (svc / "schema" / "v1.sql").write_text("CREATE TABLE a (id int);\n")
        (svc / "schema" / "__pycache__" / "x.pyc").write_bytes(b"\0")
        py = ds.Service("owner/service", require=("server.js",), schema=("schema",))
        same = _node_tree(**{"schema/v1.sql": b"CREATE TABLE a (id int);\n"})
        self.assertEqual(ds.run(py, env=ENV, service_dir=svc, get=_getter(_tarball(same))), 0)
        self.assertEqual(_info(svc)["source"], "github")
        svc2 = self.tmp / "svc2"
        svc2.mkdir()
        (svc2 / "server.js").write_text("// workspace\n")
        (svc2 / "schema").mkdir()
        (svc2 / "schema" / "v1.sql").write_text("CREATE TABLE a (id int);\n")
        changed = _node_tree(**{"schema/v1.sql": b"CREATE TABLE a (id int, b int);\n"})
        self.assertEqual(ds.run(py, env=ENV, service_dir=svc2, get=_getter(_tarball(changed))), 0)
        self.assertEqual(_info(svc2)["source"], "workspace")
        self.assertIn("schema/v1.sql", _info(svc2)["reason"])

    def test_a_subtree_service_names_schema_inside_its_subtree(self):
        repo, git = self.git_workspace()
        svc = repo / "artifacts" / "api"
        (svc / "migrations").mkdir(parents=True)
        (svc / "main.py").write_text("# workspace\n")
        (svc / "requirements.txt").write_text("flask\n")
        (svc / "migrations" / "001.sql").write_text("CREATE TABLE a (id int);\n")
        git("add", ".")
        git("commit", "-q", "-m", "add api")
        git("push", "-q", "origin", "main")
        py = ds.Service("owner/helper", subtree="artifacts/api", require=("main.py", "requirements.txt"),
                        schema=("migrations",))
        tree = {f"artifacts/api/{k}": v for k, v in {
            "main.py": b"# main\n", "requirements.txt": b"flask\n",
            "migrations/001.sql": b"CREATE TABLE a (id int);\n",
            "migrations/002.sql": b"ALTER TABLE a ADD b int;\n"}.items()}
        self.assertEqual(ds.run(py, env=ENV, service_dir=svc, get=_getter(_tarball(tree))), 0)
        info = _info(svc)
        self.assertEqual(info["source"], "workspace")
        self.assertIn("(migrations/002.sql)", info["reason"])

    def test_no_schema_option_changes_nothing(self):
        repo, git = self.pg_workspace()
        tree = self.pg_tree(**{"migrations/002.sql": b"DROP TABLE a;\n"})
        self.assertEqual(ds.run(NODE, env=ENV, service_dir=repo, get=_getter(_tarball(tree))), 0)
        self.assertEqual(_info(repo)["source"], "github", "a service with no database never sets --schema")


# -- the HTTP layer --------------------------------------------------------------


class Http(unittest.TestCase):
    def test_the_token_is_not_sent_along_a_redirect(self):
        seen = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout):
            seen["req"] = req
            return _Resp(b"ok")

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(ds.http_get("https://api.github.com/x", TOKEN, "application/vnd.github.sha"), b"ok")
        req = seen["req"]
        self.assertEqual(req.unredirected_hdrs.get("Authorization"), f"Bearer {TOKEN}")
        self.assertNotIn("Authorization", req.headers)

    def test_http_errors_become_reasons_without_the_token(self):
        for code, expected in [(401, "refused the token"), (404, "cannot see the repo"), (403, "403"), (500, "HTTP 500")]:
            with self.subTest(code=code):

                def fake_urlopen(req, timeout, code=code):
                    raise urllib.error.HTTPError(req.full_url, code, "err", {}, None)

                with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
                    with self.assertRaises(ds.SourceError) as err:
                        ds.http_get("https://api.github.com/x", TOKEN, "a")
                self.assertIn(expected, str(err.exception))
                self.assertNotIn(TOKEN, str(err.exception))

    def test_unreachable_github_becomes_a_reason(self):
        def fake_urlopen(req, timeout):
            raise urllib.error.URLError("Name or service not known")

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            with self.assertRaisesRegex(ds.SourceError, "unreachable"):
                ds.http_get("https://api.github.com/x", TOKEN, "a")


# -- the command line and what /health reports -----------------------------------


class CommandLine(_Tmp):
    def test_arguments_reach_the_service(self):
        seen = {}

        def fake_run(svc, service_dir=None):
            seen["svc"], seen["dir"] = svc, service_dir
            return 0

        with mock.patch.object(ds, "run", fake_run):
            argv = [
                "--repo", "owner/service", "--require", "server.js, package.json",
                "--generated", "package-lock.json", "--schema", "migrations/, db.js",
                "--dir", str(self.tmp),
            ]
            self.assertEqual(ds.main(argv), 0)
        svc = seen["svc"]
        self.assertEqual(svc.repo, "owner/service")
        self.assertEqual(svc.subtree, "")
        self.assertEqual(svc.require, ("server.js", "package.json"))
        self.assertEqual(svc.keep, frozenset(ds.DEFAULT_KEEP))
        self.assertEqual(svc.generated, frozenset({"package-lock.json"}))
        self.assertEqual(svc.schema, ("migrations", "db.js"))
        self.assertEqual(seen["dir"], self.tmp.resolve())

    def test_load_build_info_never_reports_an_empty_success(self):
        path = self.tmp / ds.BUILD_INFO_NAME
        missing = ds.load_build_info(path)
        self.assertIsNone(missing["source"])
        self.assertIn("no build_info.json", missing["reason"])
        path.write_text("{not json")
        self.assertIn("unreadable", ds.load_build_info(path)["reason"])
        path.write_text("[1, 2]")
        self.assertIn("not an object", ds.load_build_info(path)["reason"])
        path.write_text(json.dumps({"source": "github", "sha": SHA, "repo": "owner/service"}))
        self.assertEqual(
            ds.load_build_info(path)["commit_url"], f"https://github.com/owner/service/commit/{SHA}"
        )

    def test_only_a_github_build_links_to_a_commit(self):
        path = self.tmp / ds.BUILD_INFO_NAME
        path.write_text(json.dumps({"source": "workspace", "sha": SHA, "repo": "o/r", "reason": "no token"}))
        self.assertNotIn("commit_url", ds.load_build_info(path))
        path.write_text(json.dumps({"source": "github", "sha": None, "repo": "o/r"}))
        self.assertNotIn("commit_url", ds.load_build_info(path))


if __name__ == "__main__":
    unittest.main()
