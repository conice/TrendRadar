# 数据分支与跨天去重

`Get Hot News` 每次读取 `data` 分支中的数据库和首页，抓取并合并最新内容，生成增量推荐，然后一起提交数据库快照和最新报告。代码和配置使用代码分支；`data` 是独立历史的数据分支，每次有变化时产生一个普通提交。

数据分支包含：

```text
index.html           # 最近一次完整生成的新闻报告
output/
  news/YYYY-MM-DD.db  # 热榜、抓取记录、当天的 AI 分类缓存
  rss/YYYY-MM-DD.db   # RSS 内容和抓取记录
  state.db           # 已推荐文章、待重试推荐、上次清理时间
```

工作流会在分支不存在时，用现有数据库初始化它。已有分支读取失败、数据库损坏或者没有数据库时会报错，避免将空数据覆盖到远程。SQLite 使用 backup 接口制作快照，包含 WAL 中已提交的内容。并发任务共用 `trendradar-data` 并发组，正在执行的任务不会因新任务启动而取消；写入冲突会报错。

## 首页与 GitHub Pages

报告生成器继续写入本地 `output/index.html` 和根目录 `index.html`，这两个运行产物都不提交到代码分支。同步脚本将 `output/index.html` 保存为 `data` 分支根目录的 `index.html`；即使只有网页变化，也会产生新提交。没有生成新报告或文件写入中断时，保留数据分支中上一份完整报告，数据库仍正常保存。旧数据分支可以暂时只有数据库，第一次生成完整报告后会自动补上首页。

在仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**，然后手动运行一次 **Get Hot News**。已有站点网址保持不变。工作流先保存 `data`，再从保存成功的那个提交中提取首页，上传 Pages artifact，并由独立的部署任务发布。部署包只有 `index.html`，不包含数据库；页面沿用当前配置的报告模式，默认是最近一批增量推荐。

不能仅依赖 `data` 分支的 push 触发 Pages：[使用 `GITHUB_TOKEN` 推送的提交不会触发 Pages 构建](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)。显式部署任务已配置 `pages: write` 和 `id-token: write`，并使用 `github-pages` environment。若该 environment 限制部署分支，应允许运行爬虫工作流的代码分支（本仓库为 `master`）。

数据保存失败或还没有可用首页时，不发布页面。通知发送失败后，只要数据保存和网页 artifact 上传成功，Pages 部署仍可执行。已配置的 Cloudflare Pages 也使用同一份已保存的首页。网页部署失败不会撤销已经保存的数据。

## 去重与重试

默认配置为：

```yaml
report:
  mode: incremental
  cross_day_dedup: true

storage:
  backend: local
  local:
    data_dir: output
    retention_days: 30
    cleanup_interval_days: 30
```

跨天去重用于 `incremental` 模式的热榜和 RSS 推荐。文章通过来源 ID 和规范化链接识别；链接中的追踪参数、微博排名参数以及锚点不影响识别。没有链接时使用 RSS GUID，再回退到规范化标题。更换标题不会让同一链接再次推荐，不同来源分别记录。

抓取过和推荐成功分别记录。待推荐内容在 AI 分析、翻译、网页生成和通知发送之前保存；所有通知渠道都发送失败时，任务会失败，内容留待下次重试，即使文章已经离开榜单或已经跨天。实际发送成功的条目才会从待发送队列移除；被条数限制裁掉的待发送内容继续保留。

沿用现有通知渠道的成功定义：任一渠道报告成功，即确认本次推荐。仅使用网页、未开启或未配置通知时，以网页生成成功作为确认。当前榜单 `current` 和当日汇总 `daily` 保留完整报告语义，不应用这项增量过滤。AI 筛选仍由 `filter.method` 决定，数据分支持久化不会自动开启 AI。

分析或通知失败后，工作流仍会尝试保存已经成功写入的数据库，且保存步骤位于网页部署之前。保存失败时任务报错，并上传保留 7 天的数据库与首页 artifact，供恢复数据使用。推送成功到数据库提交之间的强制终止，仍可能导致下一次重试；外部通知与 Git 提交不构成同一个事务。

## 每 30 天清理

首次运行先清理过期数据，此后根据 `state.db` 记录的时间，每隔 30 天清理一次。每日数据库和本地 HTML/TXT 快照保留最近 30 个自然日（含当天）；推荐记录和待发送内容使用 30 天滚动窗口。数据分支保存最新首页，历史 HTML/TXT 目录不上传。去重记录一旦超过窗口即不再参与过滤，无需等待物理清理。因此两次清理之间，磁盘可能暂时保存超过 30 天的文件。

清理仅删除数据分支当前版本里的过期文件和数据库记录。Git 提交历史继续保存以前的快照，不会因为删除旧文件而自动缩小。2025 年的数据库可从初始化提交恢复；缺失月份的数据需要单独的数据来源才能补齐。

## 工作流与本地读取

工作流需要 `contents: write`，已在工作流中配置。它强制使用 `STORAGE_BACKEND=local` 和 `STORAGE_DATA_DIR=output`，确保读取和保存的是同一套数据。`cross_day_dedup` 使用本地 `state.db`，不能与仅上传每日数据库的 S3 存储混用。

在本地读取数据分支之前，先停止使用本地数据库的程序。以下命令会用数据分支的快照替换本地数据库集合和 `output/index.html`；远端没有首页时，会移除旧的本地 `output/index.html`，避免之后误发布。建议在新检出目录执行：

```sh
python scripts/sync_data_branch.py restore --manifest /tmp/trendradar-data.json
```

本地读取不会切换代码分支。工作流使用同一 manifest 执行 `save`；manifest 绑定恢复时的提交，正常推送会拒绝覆盖他人随后写入的版本。

工作流通过 `--site-dir` 将本次保存的首页导出到一个空目录，供 Pages 部署使用：

```sh
python scripts/sync_data_branch.py save --manifest /tmp/trendradar-data.json --site-dir /tmp/trendradar-pages
```

该目录必须不存在或为空。导出只读取保存成功的 Git 提交，不读取尚未提交的工作区文件；没有首页时跳过导出。

首次准备数据分支但暂时没有推送凭据时，可以在 `restore` 成功后运行：

```sh
python scripts/sync_data_branch.py save --manifest /tmp/trendradar-data.json --local-only
```

这会创建本地 `data` 分支，保留代码工作区和暂存区。配置好 GitHub 认证后，先推送 `data`，再推送工作流所在的代码分支。

验证：

```sh
uv run python -m unittest discover -s tests -v
```
