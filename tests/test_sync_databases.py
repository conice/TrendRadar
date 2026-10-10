import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.sync_databases import save_databases, sync_databases


ROOT = Path(__file__).resolve().parents[1]


def create_database(root: Path, database_type: str = "news", date: str = "2026-10-11") -> Path:
    path = root / database_type / f"{date}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        schema = "schema.sql" if database_type == "news" else "rss_schema.sql"
        connection.executescript((ROOT / "trendradar/storage" / schema).read_text())
        if database_type == "news":
            connection.executescript((ROOT / "trendradar/storage/ai_filter_schema.sql").read_text())
        connection.commit()
    return path


def query(path: Path, sql: str):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql).fetchall()


class DatabaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.branch_data = self.root / "data-branch/output"
        self.branch_data.mkdir(parents=True)
        (self.branch_data / ".gitkeep").touch()

    def test_first_restore_removes_old_samples_and_sidecars(self):
        runtime = self.root / "runtime"
        old = create_database(runtime, date="2025-12-21")
        Path(str(old) + "-wal").write_bytes(b"stale WAL")
        Path(str(old) + "-shm").write_bytes(b"stale SHM")

        self.assertEqual(sync_databases(self.branch_data, runtime), 0)
        self.assertEqual(list((runtime / "news").iterdir()), [])
        self.assertEqual(list((runtime / "rss").iterdir()), [])

    def test_two_runs_reuse_execution_state_ai_cache_and_rss(self):
        first = self.root / "first"
        sync_databases(self.branch_data, first)
        news = create_database(first)
        rss = create_database(first, "rss")
        with closing(sqlite3.connect(news)) as connection:
            connection.execute(
                "INSERT INTO period_executions (execution_date, period_key, action) VALUES (?, ?, ?)",
                ("2026-10-11", "morning", "push"),
            )
            connection.execute(
                "INSERT INTO ai_filter_analyzed_news "
                "(news_item_id, source_type, prompt_hash, matched, created_at) VALUES (?, ?, ?, ?, ?)",
                (1, "hotlist", "saved-hash", 1, "2026-10-11 08:00"),
            )
            connection.commit()
        with closing(sqlite3.connect(rss)) as connection:
            connection.execute(
                "INSERT INTO rss_items (title, feed_id, url, first_crawl_time, last_crawl_time) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Saved article", "example", "https://example.com/article", "08:00", "08:00"),
            )
            connection.commit()

        self.assertEqual(sync_databases(first, self.branch_data), 2)
        second = self.root / "second"
        self.assertEqual(sync_databases(self.branch_data, second), 2)
        self.assertEqual(
            query(second / "news/2026-10-11.db", "SELECT period_key, action FROM period_executions"),
            [("morning", "push")],
        )
        self.assertEqual(
            query(second / "news/2026-10-11.db", "SELECT prompt_hash, matched FROM ai_filter_analyzed_news"),
            [("saved-hash", 1)],
        )
        self.assertEqual(
            query(second / "rss/2026-10-11.db", "SELECT url FROM rss_items"),
            [("https://example.com/article",)],
        )

    def test_snapshot_includes_committed_wal_but_not_uncommitted_changes(self):
        runtime = self.root / "runtime"
        news = create_database(runtime)
        with closing(sqlite3.connect(news)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("INSERT INTO crawl_records (crawl_time, total_items) VALUES ('08:00', 1)")
            connection.commit()
            connection.execute("INSERT INTO crawl_records (crawl_time, total_items) VALUES ('09:00', 2)")
            self.assertTrue(Path(str(news) + "-wal").is_file())
            sync_databases(runtime, self.branch_data)

        saved = self.branch_data / "news/2026-10-11.db"
        self.assertEqual(query(saved, "SELECT crawl_time FROM crawl_records"), [("08:00",)])
        self.assertEqual(sorted(path.name for path in saved.parent.iterdir()), [saved.name])

    def test_corrupt_source_keeps_all_previously_saved_databases(self):
        original = create_database(self.branch_data)
        original_bytes = original.read_bytes()
        runtime = self.root / "runtime"
        create_database(runtime)
        (runtime / "rss").mkdir()
        (runtime / "rss/broken.db").write_bytes(b"broken database")

        with self.assertRaises(ValueError):
            sync_databases(runtime, self.branch_data)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertFalse((self.branch_data / "rss/broken.db").exists())

    def test_missing_restore_source_does_not_reset_state(self):
        runtime = self.root / "runtime"
        original = create_database(runtime)
        original_bytes = original.read_bytes()
        with self.assertRaises(FileNotFoundError):
            sync_databases(self.root / "missing", runtime)
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_retention_deletions_are_saved_and_other_outputs_are_excluded(self):
        runtime = self.root / "runtime"
        create_database(self.branch_data, date="2026-10-10")
        create_database(self.branch_data)
        sync_databases(self.branch_data, runtime)
        (runtime / "news/2026-10-10.db").unlink()
        (runtime / "index.html").write_text("report")
        (runtime / "state.db").write_text("obsolete state")
        (runtime / "news/debug.txt").write_text("debug output")

        sync_databases(runtime, self.branch_data)
        self.assertEqual(
            sorted(str(path.relative_to(self.branch_data)) for path in self.branch_data.rglob("*") if path.is_file()),
            [".gitkeep", "news/2026-10-11.db"],
        )

    def test_restore_save_without_changes_produces_identical_database(self):
        runtime = self.root / "runtime"
        create_database(runtime)
        sync_databases(runtime, self.branch_data)
        saved = self.branch_data / "news/2026-10-11.db"
        original = saved.read_bytes()
        sync_databases(self.branch_data, runtime)
        sync_databases(runtime, self.branch_data)
        self.assertEqual(saved.read_bytes(), original)

    def test_cli_uses_configured_local_data_directory(self):
        create_database(self.branch_data)
        config = self.root / "config.yaml"
        config.write_text("storage:\n  local:\n    data_dir: custom-output\n")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/sync_databases.py"), "restore",
             "--branch-dir", str(self.branch_data.parent), "--config", str(config)],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue((self.root / "custom-output/news/2026-10-11.db").is_file())
        self.assertFalse((self.root / "output").exists())


class DatabasePublishingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.branch = self.root / "branch"
        self.git("init", "--bare", "--initial-branch=data", str(self.remote))
        self.git("clone", str(self.remote), str(self.branch))
        (self.branch / "README.md").write_text("Database branch\n")
        (self.branch / "output").mkdir()
        (self.branch / "output/.gitkeep").touch()
        self.git("add", ".", cwd=self.branch)
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-m", "Initialize database branch", cwd=self.branch)
        self.git("push", "origin", "HEAD:refs/heads/data", cwd=self.branch)
        self.runtime = self.root / "runtime"
        sync_databases(self.branch / "output", self.runtime)

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd or self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def test_save_publishes_state_for_next_checkout_and_skips_unchanged_data(self):
        create_database(self.runtime)
        self.assertTrue(save_databases(self.runtime, self.branch))
        saved_head = self.git("rev-parse", "refs/heads/data", cwd=self.remote)
        second = self.root / "second-branch"
        self.git("clone", str(self.remote), str(second))
        next_runtime = self.root / "next-runtime"
        sync_databases(second / "output", next_runtime)
        self.assertTrue((next_runtime / "news/2026-10-11.db").is_file())
        self.assertFalse(save_databases(next_runtime, second))
        self.assertEqual(self.git("rev-parse", "refs/heads/data", cwd=self.remote), saved_head)

    def test_conflicting_push_cannot_overwrite_a_newer_database(self):
        stale = self.root / "stale-branch"
        self.git("clone", str(self.remote), str(stale))
        create_database(self.runtime)
        save_databases(self.runtime, self.branch)
        saved_head = self.git("rev-parse", "refs/heads/data", cwd=self.remote)
        stale_runtime = self.root / "stale-runtime"
        create_database(stale_runtime, date="2026-10-10")

        with self.assertRaises(subprocess.CalledProcessError):
            save_databases(stale_runtime, stale)
        self.assertEqual(self.git("rev-parse", "refs/heads/data", cwd=self.remote), saved_head)
        self.assertEqual(
            self.git("ls-tree", "-r", "--name-only", "data", cwd=self.remote).splitlines(),
            ["README.md", "output/.gitkeep", "output/news/2026-10-11.db"],
        )


if __name__ == "__main__":
    unittest.main()
