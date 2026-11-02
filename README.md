# TrendRadar

个人维护仓库。上游项目见 [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar)。

## 与上游不同

- `config/config.yaml` 的热榜平台为知乎、微博、今日头条、B站、抖音、FreeBuf；RSS 源为 AWS Machine Learning、How-To Geek、IT之家、TechCrunch、NASA、Nature、NYT、The Guardian、WSJ、华尔街见闻。
- `config/frequency_words.txt` 为按中文/英文正则分组的个人关注词表，覆盖公司品牌、地区、科技、社会、财经、安全、生活等方向；`[促销]` 位于全局过滤区。

## GitHub Actions 数据库备份

`Get Hot News` 工作流使用 GitHub Actions Artifact 保存数据库，无需另行配置 S3 云存储或新增 Secrets。运行时强制使用本地存储后端，数据库位于临时运行机器的 `output/news/*.db` 和 `output/rss/*.db`；日志出现 `[本地存储]` 属于正常现象。请保持 `storage.local.data_dir: "output"`，以匹配备份路径。

每次运行会按以下顺序执行：

1. 查找当前分支最近一份未过期的数据库备份，恢复到 `output`。首次运行、签到清理后或全部备份过期后，没有备份时从仓库已有数据库开始；查询或下载失败则停止运行。
2. 运行爬虫，读取已有数据并写入新数据。同分支的任务串行执行，新任务不会取消正在进行的抓取或备份。
3. 爬虫成功退出后上传数据库，再进行可选的 Cloudflare Pages 部署。爬虫失败或被取消时不上传；网页部署失败不影响此前已上传的备份。
4. 新备份上传成功后，仅保留该分支最近 3 份未过期备份，删除更旧的数据库 Artifact，不删除其所属运行记录。

备份名称为 `trendradar-data-<分支完整 ref 的 SHA-256 摘要>-<运行 ID>-<尝试次数>`，不同分支相互隔离，重新运行任务也会生成独立备份。每份备份的有效期为 30 天，实际受仓库或组织的保留策略约束。数据库历史数据沿用 `storage.local.retention_days: 0`（不主动按天清理），所以持续运行时新备份会携带此前积累的数据；停止运行超过有效期后，备份可能全部过期。

### 查看与下载

打开仓库 **Actions → Get Hot News → 某次运行 → Artifacts**，下载以 `trendradar-data-` 开头的备份。压缩包包含 `news/` 和有数据时的 `rss/` 目录；手动恢复到本地时，将这些目录解压到项目的 `output/` 下。恢复情况可查看 **Find database backup / Restore database backup** 步骤，保存情况可查看 **Upload database backup** 步骤。

备份存放在 GitHub Actions 的 Artifact 存储中，不会提交到 Git 仓库，不会增加或改写 commit。仓库原有数据库文件保持不变。Artifact 占用 Actions 存储额度，公开仓库中有读取权限的 GitHub 用户也可以下载。

### 签到与历史数据重置

签到周期为 **30 天**。`Check In` 保留原有的全部运行记录清理行为，关联的数据库 Artifact 也会被删除，即使备份尚未过期。签到后下一次抓取会从仓库已有数据库重新开始，之前仅保存在 Artifact 中的历史数据、当天汇总及增量去重状态不会保留；需要留存时，请在签到前手动下载备份。

爬虫到期后会禁用自身工作流。若已经被禁用，运行 `Check In` 清理记录后，还需在 **Actions → Get Hot News** 页面重新启用工作流。
