#!/usr/bin/env python3
"""Restore and save databases and the latest report on a Git branch (stdlib only)."""

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import tempfile


BRANCH_README = """# TrendRadar runtime data

This branch is managed by the Get Hot News workflow.

- `index.html`: latest complete news report, published by GitHub Actions to Pages.
- `output/news/YYYY-MM-DD.db`: daily hot-list data and AI filter cache.
- `output/rss/YYYY-MM-DD.db`: daily RSS data.
- `output/state.db`: cross-day delivery history, pending recommendations, cleanup time.

Each update is a normal Git commit. The workflow restores this branch before
crawling and saves committed SQLite snapshots even if recommendation delivery
fails. If no complete report is generated, the previous homepage is retained.
Only the saved homepage is included in the Pages deployment artifact.
Expired files and state are cleaned every 30 days, retaining 30 days of data.
Git commit history is retained for recovery; cleanup does not shrink it.
"""


def is_complete_report(content: bytes) -> bool:
    """Reject empty or interrupted report writes before replacing the homepage."""
    try:
        html = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return re.search(r"<html\b[^>]*>.*<body\b[^>]*>.*</body>\s*</html>\s*\Z",
                     html, re.IGNORECASE | re.DOTALL) is not None


def is_database_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if parts == ("state.db",):
        return True
    if len(parts) != 2 or parts[0] not in {"news", "rss"}:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.db", parts[1]):
        return False
    try:
        date.fromisoformat(parts[1][:-3])
        return True
    except ValueError:
        return False


def database_files(directory: Path) -> dict:
    candidates = [directory / "state.db"]
    for kind in ("news", "rss"):
        candidates.extend((directory / kind).glob("*.db"))
    files = {}
    for path in candidates:
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"Database must not be a symbolic link: {path}")
        if path.is_file() and is_database_path(relative):
            files[relative] = path
    return files


def validate_database(path: Path) -> None:
    with path.open("rb") as file:
        if file.read(16) != b"SQLite format 3\x00":
            raise RuntimeError(f"Not a SQLite database: {path.name}")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError(f"SQLite integrity check failed: {path.name}")


def snapshot_database(source: Path, target: Path) -> None:
    """SQLite backup includes committed WAL pages without copying sidecar files."""
    target.parent.mkdir(parents=True, exist_ok=True)
    validate_database(source)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    validate_database(target)


class DataBranchSync:
    def __init__(self, repo: Path, data_dir: Path, manifest: Path,
                 branch: str = "data", remote: str = "origin"):
        self.repo = repo.resolve()
        self.data_dir = data_dir.resolve()
        self.manifest = manifest.resolve()
        self.branch = branch
        self.remote = remote
        self.ref = f"refs/heads/{branch}"
        self.git("check-ref-format", self.ref)
        current = self.git("symbolic-ref", "--quiet", "HEAD", check=False)
        if current.returncode == 0 and current.stdout.decode().strip() == self.ref:
            raise RuntimeError("The database branch must differ from the code branch")

    def git(self, *args, input=None, env=None, check=True):
        result = subprocess.run(
            ["git", *args], cwd=self.repo, input=input, capture_output=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or "Git command failed")
        return result

    def _write_manifest(self, parent) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps({
            "repo": str(self.repo), "data_dir": str(self.data_dir),
            "branch": self.branch, "remote": self.remote, "parent": parent,
            "code_commit": self.git("rev-parse", "HEAD").stdout.decode().strip(),
        }), encoding="utf-8")

    def _read_homepage(self, commit) -> bytes | None:
        if not commit:
            return None
        entry = self.git("ls-tree", "-z", commit, "--", "index.html").stdout
        if not entry:
            return None
        mode, kind, oid = entry.split(b"\t", 1)[0].split()
        if mode != b"100644" or kind != b"blob":
            raise RuntimeError("Unexpected homepage entry on the data branch")
        content = self.git("cat-file", "blob", oid.decode()).stdout
        if not is_complete_report(content):
            raise RuntimeError("The saved homepage is not a complete HTML report")
        return content

    def restore(self) -> None:
        # A failed restore must never leave an earlier run's save authorization.
        self.manifest.unlink(missing_ok=True)
        result = self.git("ls-remote", "--exit-code", "--heads", self.remote, self.ref, check=False)
        if result.returncode == 2:
            for file in database_files(self.data_dir).values():
                validate_database(file)
            self._write_manifest(None)
            print(f"{self.branch} does not exist; existing databases will seed its first commit.")
            return
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or "Cannot read data branch")

        self.git("fetch", "--no-tags", "--depth=1", self.remote, self.ref)
        parent = self.git("rev-parse", "FETCH_HEAD").stdout.decode().strip()
        entries = self.git("ls-tree", "-r", "-z", parent).stdout.split(b"\x00")
        snapshots = {}
        for entry in entries:
            if not entry:
                continue
            attributes, name = entry.split(b"\t", 1)
            path = name.decode("utf-8")
            relative = path.removeprefix("output/")
            if path.startswith("output/") and is_database_path(relative):
                mode, kind, oid = attributes.split()
                if mode != b"100644" or kind != b"blob":
                    raise RuntimeError(f"Unexpected database entry: {path}")
                snapshots[relative] = oid.decode()
        if not snapshots:
            raise RuntimeError("The data branch contains no databases; refusing an empty restore")
        homepage = self._read_homepage(parent)

        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="trendradar-restore-", dir=self.data_dir.parent) as tmp:
            staging = Path(tmp)
            for relative, oid in snapshots.items():
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.git("cat-file", "blob", oid).stdout)
                validate_database(target)

            previous = database_files(self.data_dir)
            for file in previous.values():
                if any(Path(str(file) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                    raise RuntimeError(f"Close SQLite connections before restoring: {file}")
            # Validate every snapshot before changing the local database set.
            for relative in snapshots:
                target = self.data_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / relative, target)
            for relative, file in previous.items():
                if relative not in snapshots:
                    file.unlink()
            # Replace stale local reports too, so a later save cannot publish one
            # over the restored homepage when the crawler generates no report.
            report_path = self.data_dir / "index.html"
            if homepage is None:
                report_path.unlink(missing_ok=True)
            else:
                (staging / "index.html").write_bytes(homepage)
                os.replace(staging / "index.html", report_path)

        self._write_manifest(parent)
        print(f"Restored {len(snapshots)} databases from {self.branch} ({parent[:12]}).")

    def _prepare_local_branch(self, commit: str, parent) -> None:
        previous = self.git("rev-parse", "--verify", self.ref, check=False)
        old = previous.stdout.decode().strip() if previous.returncode == 0 else "0" * 40
        if previous.returncode == 0 and old != parent:
            raise RuntimeError("Local data branch has unpublished changes; refusing to replace it")
        self.git("update-ref", self.ref, commit, old)

    def save(self, local_only: bool = False) -> str:
        if not self.manifest.is_file():
            raise RuntimeError("A successful restore is required before saving")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        expected = {"repo": str(self.repo), "data_dir": str(self.data_dir),
                    "branch": self.branch, "remote": self.remote}
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Restore manifest does not belong to this checkout/data branch")
        files = database_files(self.data_dir)
        if not files:
            raise RuntimeError("No databases to save; refusing to overwrite the data branch")
        parent = manifest.get("parent")
        report_path = self.data_dir / "index.html"
        if report_path.is_symlink():
            raise RuntimeError("HTML report must not be a symbolic link")
        homepage = report_path.read_bytes() if report_path.is_file() else None
        if homepage is not None and not is_complete_report(homepage):
            print("Ignoring incomplete HTML report; retaining the previous homepage.")
            homepage = None
        if homepage is None:
            homepage = self._read_homepage(parent)

        with tempfile.TemporaryDirectory(prefix="trendradar-save-") as tmp:
            staging = Path(tmp)
            for relative, file in files.items():
                snapshot_database(file, staging / "output" / relative)
            (staging / "README.md").write_text(BRANCH_README, encoding="utf-8")
            if homepage is not None:
                (staging / "index.html").write_bytes(homepage)

            env = {"GIT_INDEX_FILE": str(staging / "index")}
            self.git("read-tree", "--empty", env=env)
            names = ["README.md", *(f"output/{relative}" for relative in sorted(files))]
            if homepage is not None:
                names.append("index.html")
            for name in names:
                oid = self.git("hash-object", "-w", "--stdin", input=(staging / name).read_bytes()).stdout.decode().strip()
                self.git("update-index", "--add", "--cacheinfo", f"100644,{oid},{name}", env=env)
            tree = self.git("write-tree", env=env).stdout.decode().strip()
            if parent and tree == self.git("rev-parse", f"{parent}^{{tree}}").stdout.decode().strip():
                if local_only:
                    self._prepare_local_branch(parent, parent)
                print("Runtime data is unchanged; no commit needed.")
                return parent
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            message = f"Update runtime data ({now})\n\nCode: {manifest['code_commit']}\n"
            identity = {
                "GIT_AUTHOR_NAME": "github-actions[bot]",
                "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
                "GIT_COMMITTER_NAME": "github-actions[bot]",
                "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
            }
            args = ["commit-tree", tree, *( ["-p", parent] if parent else [] )]
            commit = self.git(*args, input=message.encode(), env=identity).stdout.decode().strip()
            if local_only:
                self._prepare_local_branch(commit, parent)
                print(f"Prepared {len(files)} databases on local branch {self.branch} ({commit[:12]}).")
                return commit
            # A concurrent writer causes a normal non-fast-forward rejection.
            # Binary databases are never rebased, merged, or force-pushed.
            self.git("push", self.remote, f"{commit}:{self.ref}")
            self.git("update-ref", f"refs/remotes/{self.remote}/{self.branch}", commit)
            self._write_manifest(commit)
            print(f"Saved {len(files)} databases to {self.branch} ({commit[:12]}).")
            return commit

    def export_site(self, commit: str, directory: Path) -> bool:
        """Export only the committed homepage, never databases or working files."""
        homepage = self._read_homepage(commit)
        if homepage is None:
            print("No saved homepage yet; skipping site export.")
            return False
        if directory.is_symlink() or (directory.exists() and
                                     (not directory.is_dir() or any(directory.iterdir()))):
            raise RuntimeError(f"Site directory must be empty: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_bytes(homepage)
        print(f"Exported homepage from {self.branch} ({commit[:12]}).")
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("restore", "save"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--data-dir", type=Path, default=Path("output"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--branch", default="data")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--local-only", action="store_true", help="Prepare a local data branch without pushing")
    parser.add_argument("--site-dir", type=Path, help="Export the saved homepage to an empty directory")
    args = parser.parse_args()
    if args.site_dir is not None and args.action != "save":
        parser.error("--site-dir can only be used with save")
    data_dir = args.data_dir if args.data_dir.is_absolute() else args.repo / args.data_dir
    sync = DataBranchSync(args.repo, data_dir, args.manifest, args.branch, args.remote)
    try:
        if args.action == "restore":
            sync.restore()
        else:
            commit = sync.save(local_only=args.local_only)
            if args.site_dir is not None:
                site_dir = args.site_dir if args.site_dir.is_absolute() else args.repo / args.site_dir
                sync.export_site(commit, site_dir)
    except (RuntimeError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Runtime data sync failed: {error}\n")


if __name__ == "__main__":
    main()
