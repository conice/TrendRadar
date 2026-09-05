from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("data_branch_sync", ROOT / "scripts/sync_data_branch.py")
sync_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_module)
DataBranchSync = sync_module.DataBranchSync


class DataBranchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "code"
        self.repo.mkdir()
        self.git(self.root, "init", "--bare", "--initial-branch=master", str(self.remote))
        self.git(self.repo, "init", "--initial-branch=master")
        self.git(self.repo, "config", "user.name", "Test")
        self.git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "app.py").write_text("# code\n")
        self.git(self.repo, "add", "app.py")
        self.git(self.repo, "commit", "-m", "Code")
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.git(self.repo, "push", "origin", "master")
        self.code_commit = self.git(self.repo, "rev-parse", "HEAD")
        self.db = self.repo / "output/news/2026-09-05.db"
        self.create_database(self.db)
        self.sync = self.make_sync(self.repo)

    @staticmethod
    def git(repo, *args):
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    @staticmethod
    def create_database(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE items (title TEXT)")
            conn.execute("INSERT INTO items VALUES ('first')")

    @staticmethod
    def append(path, title="second"):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("INSERT INTO items VALUES (?)", (title,))

    def make_sync(self, repo):
        return DataBranchSync(repo, repo / "output", repo / ".git/sync.json")

    def clone_code(self, name):
        target = self.root / name
        self.git(self.root, "clone", "--depth=1", "--branch=master", self.remote.as_uri(), str(target))
        return target

    def seed(self):
        self.sync.restore()
        return self.sync.save()

    @staticmethod
    def write_homepage(repo, title="Latest report"):
        content = f"<!DOCTYPE html><html><body>{title}</body></html>"
        path = repo / "output/index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return content

    def test_bootstrap_is_orphan_and_only_contains_databases(self):
        (self.repo / "output/secret.txt").write_text("not runtime data")
        commit = self.seed()
        names = self.git(self.remote, "ls-tree", "-r", "--name-only", "data").splitlines()
        self.assertEqual(names, ["README.md", "output/news/2026-09-05.db"])
        self.assertEqual(self.git(self.remote, "rev-list", "data").splitlines(), [commit])
        self.assertEqual(self.git(self.remote, "rev-parse", "master"), self.code_commit)
        self.assertEqual(self.git(self.repo, "symbolic-ref", "--short", "HEAD"), "master")
        self.assertEqual(self.git(self.repo, "diff", "--cached", "--name-only"), "")

    def test_homepage_is_saved_with_databases_and_exported_from_the_saved_commit(self):
        content = self.write_homepage(self.repo)
        (self.repo / "index.html").write_text("Old code-branch homepage")
        (self.repo / "output/other.html").write_text("Not part of the site")
        commit = self.seed()
        self.assertEqual(self.git(self.remote, "show", "data:index.html"), content)
        self.assertEqual(self.git(self.remote, "ls-tree", "-r", "--name-only", "data").splitlines(),
                         ["README.md", "index.html", "output/news/2026-09-05.db"])
        self.assertEqual(self.git(self.remote, "rev-parse", "master"), self.code_commit)

        # Deployment must use the saved snapshot, even if local output changes.
        self.write_homepage(self.repo, "Not saved yet")
        site = self.root / "site"
        self.assertTrue(self.sync.export_site(commit, site))
        self.assertEqual((site / "index.html").read_text(), content)
        self.assertEqual([path.name for path in site.iterdir()], ["index.html"])

    def test_homepage_only_update_creates_a_commit(self):
        self.write_homepage(self.repo, "First report")
        first = self.seed()
        content = self.write_homepage(self.repo, "Second report")
        second = self.sync.save()
        self.assertNotEqual(first, second)
        self.assertEqual(self.git(self.remote, "rev-parse", f"{second}^"), first)
        self.assertEqual(self.git(self.remote, "show", "data:index.html"), content)
        self.assertEqual(self.git(self.remote, "rev-parse", f"{first}:output"),
                         self.git(self.remote, "rev-parse", f"{second}:output"))
        self.assertEqual(self.sync.save(), second)

    def test_fresh_runner_restores_homepage_and_preserves_it_without_a_new_report(self):
        content = self.write_homepage(self.repo)
        self.seed()
        fresh = self.clone_code("fresh")
        self.write_homepage(fresh, "Stale local report")
        sync = self.make_sync(fresh)
        sync.restore()
        self.assertEqual((fresh / "output/index.html").read_text(), content)

        # A failed or skipped generation must not remove the published page.
        (fresh / "output/index.html").unlink()
        self.append(fresh / "output/news/2026-09-05.db")
        sync.save()
        self.assertEqual(self.git(self.remote, "show", "data:index.html"), content)
        sync.restore()
        with closing(sqlite3.connect(fresh / "output/news/2026-09-05.db")) as conn:
            self.assertEqual(conn.execute("SELECT title FROM items").fetchall(), [("first",), ("second",)])

    def test_incomplete_report_preserves_homepage_without_blocking_database_save(self):
        content = self.write_homepage(self.repo)
        self.seed()
        (self.repo / "output/index.html").write_text("<!DOCTYPE html><html><body>Interrupted")
        self.append(self.db)
        self.sync.save()
        self.assertEqual(self.git(self.remote, "show", "data:index.html"), content)
        self.sync.restore()
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT title FROM items").fetchall(), [("first",), ("second",)])

    def test_database_only_branch_does_not_export_a_site(self):
        (self.repo / "output/index.html").write_text("Incomplete report")
        commit = self.seed()
        site = self.root / "site"
        self.assertFalse(self.sync.export_site(commit, site))
        self.assertFalse(site.exists())

    def test_restore_without_a_homepage_removes_stale_local_report(self):
        self.seed()
        self.write_homepage(self.repo, "Stale local report")
        self.sync.restore()
        self.assertFalse((self.repo / "output/index.html").exists())

    def test_site_export_refuses_a_directory_containing_other_files(self):
        self.write_homepage(self.repo)
        commit = self.seed()
        with self.assertRaisesRegex(RuntimeError, "empty"):
            self.sync.export_site(commit, self.repo / "output")
        self.assertTrue(self.db.is_file())

    def test_save_cli_exports_the_committed_homepage(self):
        content = self.write_homepage(self.repo)
        self.sync.restore()
        site = self.root / "site"
        subprocess.run([
            sys.executable, str(ROOT / "scripts/sync_data_branch.py"), "save",
            "--repo", str(self.repo), "--manifest", str(self.sync.manifest),
            "--site-dir", str(site),
        ], capture_output=True, text=True, check=True)
        self.assertEqual((site / "index.html").read_text(), content)
        self.assertEqual(self.git(self.remote, "show", "data:index.html"), content)

    def test_local_bootstrap_does_not_push_or_replace_unpublished_data(self):
        self.sync.restore()
        commit = self.sync.save(local_only=True)
        self.assertEqual(self.git(self.repo, "rev-parse", "data"), commit)
        self.assertEqual(self.git(self.remote, "for-each-ref", "refs/heads/data"), "")
        self.append(self.db)
        with self.assertRaisesRegex(RuntimeError, "unpublished changes"):
            self.sync.save(local_only=True)
        self.assertEqual(self.git(self.repo, "rev-parse", "data"), commit)

    def test_local_branch_can_be_prepared_from_unchanged_remote_snapshot(self):
        commit = self.seed()
        self.sync.restore()
        self.assertEqual(self.sync.save(local_only=True), commit)
        self.assertEqual(self.git(self.repo, "rev-parse", "data"), commit)

    def test_fresh_runner_restores_updates_and_keeps_commit_history(self):
        first = self.seed()
        fresh = self.clone_code("fresh")
        obsolete = fresh / "output/news/2025-12-27.db"
        self.create_database(obsolete)
        sync = self.make_sync(fresh)
        sync.restore()
        self.assertFalse(obsolete.exists())
        database = fresh / "output/news/2026-09-05.db"
        self.append(database)
        second = sync.save()
        self.assertEqual(self.git(fresh, "rev-parse", f"{second}^"), first)
        # Restoring the next snapshot includes previous updates and does not
        # manufacture a commit when no data has changed.
        sync.restore()
        with closing(sqlite3.connect(database)) as conn:
            self.assertEqual(conn.execute("SELECT title FROM items").fetchall(), [("first",), ("second",)])
        self.assertEqual(sync.save(), second)

    def test_deleted_old_database_stays_deleted_on_next_runner(self):
        old = self.repo / "output/news/2025-12-27.db"
        self.create_database(old)
        self.seed()
        old.unlink()
        self.sync.save()
        fresh = self.clone_code("fresh")
        self.create_database(fresh / "output/news/2025-12-27.db")
        self.make_sync(fresh).restore()
        self.assertFalse((fresh / "output/news/2025-12-27.db").exists())

    def test_concurrent_writer_is_rejected_without_overwriting_remote(self):
        self.seed()
        fresh = self.clone_code("concurrent")
        other = self.make_sync(fresh)
        other.restore()
        self.append(self.db, "writer one")
        accepted = self.sync.save()
        self.append(fresh / "output/news/2026-09-05.db", "writer two")
        with self.assertRaises(RuntimeError):
            other.save()
        self.assertEqual(self.git(self.remote, "rev-parse", "data"), accepted)

    def test_corrupt_remote_snapshot_cannot_replace_local_data_or_be_saved(self):
        self.seed()
        attacker = self.root / "bad"
        self.git(self.root, "clone", "--branch=data", str(self.remote), str(attacker))
        self.git(attacker, "config", "user.name", "Test")
        self.git(attacker, "config", "user.email", "test@example.invalid")
        (attacker / "output/news/2026-09-05.db").write_text("invalid database")
        self.git(attacker, "commit", "-am", "Corrupt snapshot")
        self.git(attacker, "push", "origin", "data")
        previous = self.db.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Not a SQLite"):
            self.sync.restore()
        self.assertEqual(self.db.read_bytes(), previous)
        with self.assertRaisesRegex(RuntimeError, "successful restore"):
            self.sync.save()

    def test_wal_snapshot_includes_committed_pages_only(self):
        self.sync.restore()
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO items VALUES ('committed WAL')")
        conn.commit()
        conn.execute("INSERT INTO items VALUES ('uncommitted')")
        self.sync.save()
        fresh = self.clone_code("fresh")
        self.make_sync(fresh).restore()
        with closing(sqlite3.connect(fresh / "output/news/2026-09-05.db")) as restored:
            self.assertEqual(restored.execute("SELECT title FROM items").fetchall(), [("first",), ("committed WAL",)])

    def test_unreachable_remote_does_not_bootstrap_an_empty_branch(self):
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "missing.git"))
        with self.assertRaises(RuntimeError):
            self.sync.restore()
        self.assertFalse(self.sync.manifest.exists())
        self.assertTrue(self.db.exists())


if __name__ == "__main__":
    unittest.main()
