# TrendRadar runtime databases

This branch is managed by the Get Hot News workflow.

- `output/news/YYYY-MM-DD.db`: daily hot-list data and AI filter cache.
- `output/rss/YYYY-MM-DD.db`: daily RSS data.
- `output/state.db`: cross-day delivery history, pending recommendations, cleanup time.

Each update is a normal Git commit. The workflow restores this branch before
crawling and saves committed SQLite snapshots even if recommendation delivery
fails. Expired files and state are cleaned every 30 days, retaining 30 days of
data. Git commit history is retained for recovery; cleanup does not shrink it.
