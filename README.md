# TrendRadar 数据库

此分支由 GitHub Actions 的 Get Hot News 工作流维护，使用独立的 Git 历史。

- `output/news/YYYY-MM-DD.db`：热榜数据、调度执行记录和 AI 筛选缓存。
- `output/rss/YYYY-MM-DD.db`：RSS 数据和相关状态。
- `output/.gitkeep`：保留空数据目录，首次抓取时初始化数据库。

每次运行先恢复数据库，再抓取和处理新闻。结束后校验 SQLite 快照并提交更新，
无变化时跳过提交。运行目录遵循代码分支的 `storage.local.data_dir` 配置。
数据保留时间遵循 `storage.local.retention_days`，默认永久保留。
