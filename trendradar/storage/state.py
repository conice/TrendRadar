# coding=utf-8
"""跨天推荐状态：稳定文章标识、待发送内容和定期清理。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Callable, Dict, List, Optional, Tuple

from trendradar.utils.url import normalize_url


def article_key(source_type: str, item: Dict) -> str:
    """使用来源 ID 和文章链接去重；没有链接时依次使用 GUID、标题。"""
    source_id = item.get("source_id") or item.get("feed_id") or item.get("source_name", "")
    url = (item.get("url") or item.get("mobileUrl") or item.get("mobile_url") or "").strip()
    if url:
        url = normalize_url(url, source_id if source_type == "hotlist" else "")
        try:
            parts = urlsplit(url)
            query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                     if not key.lower().startswith("utm_")]
            url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                             urlencode(query), ""))
        except ValueError:
            pass
        identity = ["url", url]
    elif item.get("guid"):
        identity = ["guid", item["guid"]]
    else:
        title = unicodedata.normalize("NFKC", item.get("title", ""))
        identity = ["title", " ".join(title.split()).casefold()]
    value = json.dumps([source_type, source_id, identity], ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RecommendationState:
    """每天的 SQLite 之外共享一个 state.db，只确认成功推荐的文章。

    prepare 在返回报告前持久化待发送内容；mark_delivered 仅在发送成功，
    或仅生成网页且生成成功后调用。连接按操作关闭，便于工作流安全备份。
    retention_days=0 表示永久保留。
    """

    def __init__(self, data_dir: str, retention_days: int = 30, cleanup_interval_days: int = 30):
        if retention_days < 0 or cleanup_interval_days <= 0:
            raise ValueError("保留天数不能为负数，清理间隔必须大于 0")
        self.path = Path(data_dir) / "state.db"
        self.retention_days = retention_days
        self.cleanup_interval_days = cleanup_interval_days

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError(f"state.db 版本 {version} 高于当前程序支持的版本")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS delivered (
                    article_key TEXT PRIMARY KEY,
                    delivered_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending (
                    article_key TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    group_json TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    queued_at REAL NOT NULL,
                    PRIMARY KEY (article_key, scope, group_name)
                );
                CREATE INDEX IF NOT EXISTS idx_pending_scope ON pending(scope, queued_at);
                CREATE TABLE IF NOT EXISTS maintenance (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );
                PRAGMA user_version = 1;
            """)
            with conn:
                yield conn
        finally:
            conn.close()

    def _cutoff(self, now: datetime) -> float:
        return now.timestamp() - self.retention_days * 86400 if self.retention_days else 0

    def _delivered_keys(self, conn, now: datetime) -> set:
        return {row[0] for row in conn.execute(
            "SELECT article_key FROM delivered WHERE delivered_at > ?", (self._cutoff(now),)
        )}

    def filter_news(self, results: Dict, now: datetime) -> Dict:
        """在排序和条数裁剪之前过滤已推荐的热榜文章。"""
        with self._connection() as conn:
            delivered = self._delivered_keys(conn, now)
        filtered = {}
        for source_id, titles in results.items():
            remaining = {
                title: item for title, item in titles.items()
                if article_key("hotlist", {**item, "title": title, "source_id": source_id}) not in delivered
            }
            if remaining:
                filtered[source_id] = remaining
        return filtered

    def filter_rss(self, items: List[Dict], now: datetime) -> List[Dict]:
        """在 RSS 关键词筛选和条数裁剪之前过滤已推荐文章。"""
        with self._connection() as conn:
            delivered = self._delivered_keys(conn, now)
        return [item for item in items if article_key("rss", item) not in delivered]

    def prepare(
        self, stats: List[Dict], rss_stats: Optional[List[Dict]], now: datetime,
        scope: str = "default", active_sources: Optional[Dict[str, set]] = None,
    ) -> Tuple[List[Dict], List[Dict]]:
        """合并本次推荐和未成功发送的内容，返回去重后的热榜、RSS 分组。"""
        timestamp, cutoff = now.timestamp(), self._cutoff(now)
        current_metadata = {"hotlist": {}, "rss": {}}
        with self._connection() as conn:
            delivered = self._delivered_keys(conn, now)
            for source_type, groups in (("hotlist", stats), ("rss", rss_stats or [])):
                for group in groups:
                    metadata = {key: value for key, value in group.items() if key != "titles"}
                    current_metadata[source_type][group["word"]] = metadata
                    for item in group.get("titles", []):
                        key = article_key(source_type, item)
                        if key in delivered:
                            continue
                        payload = {**deepcopy(item), "_recommendation_key": key}
                        conn.execute("""
                            INSERT INTO pending VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(article_key, scope, group_name) DO UPDATE SET
                                group_json = excluded.group_json, item_json = excluded.item_json,
                                queued_at = CASE WHEN pending.queued_at <= ?
                                    THEN excluded.queued_at ELSE pending.queued_at END
                        """, (key, source_type, scope, group["word"],
                              json.dumps(metadata, ensure_ascii=False),
                              json.dumps(payload, ensure_ascii=False), timestamp, cutoff))

            grouped = {"hotlist": {}, "rss": {}}
            rows = conn.execute("""
                SELECT * FROM pending WHERE scope = ? AND queued_at > ?
                ORDER BY queued_at, rowid
            """, (scope, cutoff))
            for row in rows:
                if row["article_key"] in delivered:
                    continue
                item = json.loads(row["item_json"])
                source_type = row["source_type"]
                if active_sources is not None and item.get("source_id") not in active_sources.get(source_type, set()):
                    continue
                groups = grouped[source_type]
                name = row["group_name"]
                if name not in groups:
                    metadata = current_metadata[source_type].get(name, json.loads(row["group_json"]))
                    groups[name] = {**metadata, "titles": []}
                groups[name]["titles"].append(item)

        output = []
        for source_type, groups in grouped.items():
            for group in groups.values():
                limit = group.get("_max_count", 0)
                if limit > 0:
                    group["titles"] = group["titles"][:limit]
                group["count"] = len(group["titles"])
            order = list(dict.fromkeys([*current_metadata[source_type], *groups]))
            output.append([groups[name] for name in order if name in groups])
        return output[0], output[1]

    def mark_delivered(self, stats: List[Dict], rss_stats: Optional[List[Dict]], now: datetime) -> None:
        """确认实际展示/发送的条目；队列中被条数限制裁掉的内容仍可重试。"""
        keys = {
            item["_recommendation_key"]
            for groups in (stats, rss_stats or []) for group in groups
            for item in group.get("titles", []) if item.get("_recommendation_key")
        }
        if not keys:
            return
        with self._connection() as conn:
            conn.executemany("""
                INSERT INTO delivered VALUES (?, ?)
                ON CONFLICT(article_key) DO UPDATE SET delivered_at = excluded.delivered_at
            """, [(key, now.timestamp()) for key in keys])
            conn.executemany("DELETE FROM pending WHERE article_key = ?", [(key,) for key in keys])

    def cleanup_if_due(self, now: datetime, cleanup_files: Callable[[int], int]) -> int:
        """首次运行及之后每隔指定天数，清理过期状态与每日数据。"""
        if not self.retention_days:
            return 0
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM maintenance WHERE key = 'last_cleanup'").fetchone()
            if row and now.timestamp() - row[0] < self.cleanup_interval_days * 86400:
                return 0
            cutoff = self._cutoff(now)
            deleted = conn.execute("DELETE FROM delivered WHERE delivered_at <= ?", (cutoff,)).rowcount
            deleted += conn.execute("DELETE FROM pending WHERE queued_at <= ?", (cutoff,)).rowcount
            files_deleted = cleanup_files(self.retention_days)
            conn.execute("""
                INSERT INTO maintenance VALUES ('last_cleanup', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (now.timestamp(),))
        if deleted:
            with self._connection() as conn:
                conn.execute("VACUUM")
        print(f"[跨天去重] 定期清理完成：{deleted} 条过期状态，{files_deleted} 个过期文件/目录")
        return files_deleted
