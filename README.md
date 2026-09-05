# TrendRadar runtime data

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
