# TrendRadar 定制版

本仓库基于上游项目 [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) v6.10.0。

除下列内容外，项目功能、配置和使用方式均以上游文档为准：

## 与上游的差异

### GitHub Actions 爬虫调度

- 定时任务改为每小时第 `0` 分钟执行：`0 * * * *`。
- 试用期改为 `365` 天（`31536000` 秒），上游为 `7` 天（`604800` 秒）。

对应配置文件：`.github/workflows/crawler.yml`

## 上游文档

- [上游 README](https://github.com/sansan0/TrendRadar#readme)
- [上游项目主页](https://github.com/sansan0/TrendRadar)

