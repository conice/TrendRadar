"""Restore and publish the crawler's SQLite databases on the data branch."""

import argparse
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


DATABASE_TYPES = ("news", "rss")


def snapshot_database(source: Path, destination: Path) -> None:
    """Include committed WAL transactions in a checked, standalone SQLite file."""
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Not a regular database file: {source}")
    with source.open("rb") as database:
        if database.read(16) != b"SQLite format 3\x00":
            raise ValueError(f"Invalid SQLite database: {source}")

    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer)
            writer.execute("PRAGMA journal_mode=DELETE")
            if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError(f"SQLite integrity check failed: {source}")


def sync_databases(source: Path, destination: Path) -> int:
    """Replace news/RSS databases only after every source snapshot is valid.

    An existing empty source is a fresh installation. A missing source is an
    error, so a failed checkout cannot silently reset the crawler's state.
    Replacing both database directories also removes old samples and sidecars.
    """
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if not source.is_dir():
        raise ValueError(f"Not a database directory: {source}")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination database directories must not overlap")

    for database_type in DATABASE_TYPES:
        for directory in (source / database_type, destination / database_type):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise ValueError(f"Not a regular database directory: {directory}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with TemporaryDirectory(prefix=".database-sync-", dir=destination.parent) as temporary:
        staged = Path(temporary)
        for database_type in DATABASE_TYPES:
            (staged / database_type).mkdir()
            for database in sorted((source / database_type).glob("*.db")):
                snapshot_database(database, staged / database_type / database.name)
                count += 1

        destination.mkdir(parents=True, exist_ok=True)
        for database_type in DATABASE_TYPES:
            target = destination / database_type
            if target.exists():
                shutil.rmtree(target)
            (staged / database_type).replace(target)

    return count


def save_databases(data_dir: Path, branch_dir: Path) -> bool:
    """Publish checked snapshots with a normal push; never merge stale databases."""
    count = sync_databases(data_dir, branch_dir / "output")
    print(f"Prepared {count} database snapshots", flush=True)
    subprocess.run(["git", "add", "--all", "--", "output"], cwd=branch_dir, check=True)
    changes = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=branch_dir)
    if changes.returncode == 0:
        print("Databases unchanged; no commit needed", flush=True)
        return False
    if changes.returncode != 1:
        changes.check_returncode()

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    subprocess.run(
        [
            "git",
            "-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", f"Update databases ({timestamp})",
        ],
        cwd=branch_dir,
        check=True,
    )
    # A competing writer must cause a failure, not a rebase/force-push of SQLite files.
    subprocess.run(["git", "push", "origin", "HEAD:refs/heads/data"], cwd=branch_dir, check=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restore", "save"))
    parser.add_argument("--branch-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    args = parser.parse_args()

    import yaml

    with args.config.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    data_dir = Path(config.get("storage", {}).get("local", {}).get("data_dir", "output"))

    if args.command == "restore":
        count = sync_databases(args.branch_dir / "output", data_dir)
        print(f"Restored {count} databases; crawler state is ready", flush=True)
    else:
        save_databases(data_dir, args.branch_dir)


if __name__ == "__main__":
    main()
