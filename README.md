# TrendRadar 定制版

本仓库基于上游项目 [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) v6.10.0。

除下列内容外，项目功能、配置和使用方式均以上游文档为准：

## 与上游的差异

### GitHub Actions 爬虫调度

- 定时任务改为每小时第 `0` 分钟执行：`0 * * * *`。
- 试用期改为 `365` 天（`31536000` 秒），上游为 `7` 天（`604800` 秒）。

对应配置文件：`.github/workflows/crawler.yml`

### GitHub Actions 数据库持久化

- `data` 是独立历史的数据分支，初始仅包含说明和 `output/.gitkeep`；首次抓取创建数据库。
- 每次抓取前先检出 `data`，校验并恢复数据库，再启动程序读取抓取记录、调度执行状态和 AI 筛选缓存。
- 数据分支保存 `output/news/*.db`、`output/rss/*.db`。运行目录使用 `config/config.yaml` 的 `storage.local.data_dir`；恢复时替换该目录下的 `news`、`rss`，避免混入代码分支自带的旧数据库。
- Actions 固定使用本地 SQLite，关闭 S3 自动拉取与清理。爬虫步骤结束后（包括失败）使用 SQLite 快照保存已提交事务，校验后回存 `data`，再执行网页部署。
- 所有抓取任务串行运行；没有数据变化时不提交。下载或校验失败会阻止抓取，推送冲突直接报错。
- 数据保留时间沿用 `storage.local.retention_days`，默认 `0`（永久保留）；清理过期文件不会删除 Git 提交历史。

同步脚本：`scripts/sync_databases.py`。工作流需要 `contents: write` 权限，且 `data` 分支须已创建。网页报告仍由原有 Cloudflare Pages 步骤部署。

验证数据库恢复、回存和冲突处理：

```sh
python -m unittest discover -s tests -v
```

## 上游文档

- [上游 README](https://github.com/sansan0/TrendRadar#readme)
- [上游项目主页](https://github.com/sansan0/TrendRadar)
