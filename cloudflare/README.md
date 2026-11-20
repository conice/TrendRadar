# Cloudflare Worker：TrendRadar 定时触发器

```
Cloudflare cron (30 0-14 * * *)
        │
        ▼
Cloudflare Worker ── repository_dispatch ──→ GitHub Actions
   trendradar-run                              crawler.yml
                                                │
                                                └─→ 抓取 / AI / 推送 / 网页
```

Worker 的职责只有一件：cron 到点时向 GitHub 发一个 `repository_dispatch` 信号。
新闻抓取、AI 分析、通知推送、网页生成全部在 GitHub Actions 里完成；
Worker 不接触新闻数据、报告内容、通知密钥，也没有 KV / 数据库。

## 和 daily-digest-bot 触发器有什么区别

| 能力 | daily-digest-bot | 本 Worker |
|---|---|---|
| Telegram 命令 | 有（`/quick`、`/hot` 等） | 未启用 |
| 定时方式 | 5 个 cron：早报、晚报、周报、两次送达检查 | 1 个 cron：`30 0-14 * * *` |
| 送达看门狗 | KV + Actions 回调 + 未送达告警 | 无（TrendRadar 没有 slot/回调语义） |
| 触发事件 | `quick-report` | `trendradar-run` |
| 目标 | 生成一份快报 | 跑完整的抓取/分析/推送流水线 |

所以这里只复用了「Cloudflare cron → GitHub dispatch」这一层，没有搬 Telegram webhook、
KV 状态和看门狗。两者的触发模型不同，硬搬会把 TrendRadar 的「持续采集」变成
「每天只跑两三次」，排名历史会变得很稀。

## 时间与 cron

配置在 `cloudflare/wrangler.toml`：

```toml
[triggers]
crons = ["30 0-14 * * *"]
```

- Cloudflare cron 一律按 **UTC** 解释。
- `30 0-14 * * *` = 北京时间 **08:30-22:30，每小时第 30 分钟**，每天 15 次。
- 最后一次是 UTC 14:30 = 北京时间 22:30；UTC 15:00 = 北京时间 23:00，不再触发。
- 改时间只改 `wrangler.toml`，然后重新 `npx wrangler deploy`；改完不 deploy 不会生效。

常用换算：

| 目标（北京时间） | cron（UTC） | 说明 |
|---|---|---|
| 08:30-22:30 每小时 | `30 0-14 * * *` | 当前配置，15 次/天 |
| 每天 08:30 | `30 0 * * *` | 一天一次 |
| 每天 20:30 | `30 12 * * *` | 一天一次 |
| 08:30-22:30 每半小时 | `*/30 0-14 * * *` | 30 次/天，Actions 消耗翻倍 |
| 全天每小时 | `30 * * * *` | 24 次/天，不推荐 |

> 为什么只用一个表达式，不拆成早/晚几个 slot：
> TrendRadar 的排名变化、增量报告、AI 上下文都依赖持续采集。
> 只在早晚触发会让数据出现大段空白；正确做法是保持每小时采集，
> 由 `config/timeline.yaml` 决定当前时段是否推送。

## 前置条件

- 一个 GitHub 仓库，且 `crawler.yml` 已经在默认分支上。
- 一个 Cloudflare 账户。
- Node.js 20 或更新版本。
- 一个只对目标仓库有 **Contents: Read and write** 权限的 GitHub PAT。

## 部署

### 方式一：命令行（推荐）

下面的步骤都在本机执行，cron 和仓库名可以随仓库版本管理。

#### 1. 创建 GitHub PAT

访问 <https://github.com/settings/personal-access-tokens/new>：

1. **Token name**：随意，例如 `trendradar-trigger`。
2. **Expiration**：按需，例如 90 天或 1 年；过期前要回来更新 Worker Secret。
3. **Repository access** → Only select repositories → 选中你的 TrendRadar 仓库。
4. **Permissions** → Repository permissions → 找到 **Contents** → 设为 **Read and write**。
5. 其它权限不需要，尤其不要给 Actions 写权限。
6. 生成并复制 token（`github_pat_...`）；页面关掉后不会再显示。

> 为什么是 Contents：触发走的是
> `POST /repos/{owner}/{repo}/dispatches`，GitHub 要求 **Contents: write**。
> 只给 Actions 权限会返回 401/403。
>
> 如果你用的是 classic PAT，对应的是 `repo` scope。

#### 2. 安装并登录 wrangler

```bash
node --version          # 建议 20+
npx wrangler --version  # 第一次会提示安装
npx wrangler login
```

`wrangler login` 会打开浏览器完成 Cloudflare 授权。

没有浏览器 / 在手机上部署时，可以用 API Token：

```bash
export CLOUDFLARE_API_TOKEN="你的 Cloudflare API Token"
export CLOUDFLARE_ACCOUNT_ID="你的 Account ID"
```

Cloudflare API Token 需要 Workers 编辑权限（Dashboard → My Profile → API Tokens →
Create Token → Edit Cloudflare Workers 模板即可）。

#### 3. 确认仓库名

打开 `cloudflare/wrangler.toml`：

```toml
[vars]
GITHUB_REPO = "conice/TrendRadar"
```

改成你自己的 `owner/repo`。必须是 `owner/repo` 形式，不能带 `https://github.com/`，
也不能带结尾斜杠。

#### 4. 写入 GitHub Token

```bash
cd cloudflare
npx wrangler secret put GITHUB_TOKEN
```

粘贴第 1 步的 PAT，回车。Secret 不会出现在 `wrangler.toml` 或仓库里。

#### 5. 部署

```bash
cd cloudflare
npx wrangler deploy
```

成功后会输出 Worker 地址，例如：

```text
https://trendradar-trigger.<你的子域>.workers.dev
```

同时会显示已上传的 cron trigger（不同 wrangler 版本格式略有差异）：

```text
30 0-14 * * *
```

如果这里没有 cron，检查 `wrangler.toml` 的 `[triggers]` 是否在顶层，
而不是误写进 `[vars]`。

> `name` 一旦部署就成为 Worker 的固定标识和默认域名前缀。
> 改名会创建新的 Worker；旧 Worker 的 cron 需要手动删除。

### 方式二：Cloudflare Dashboard（不装 wrangler）

适合只在网页上操作。代价是配置不落盘：改代码、改变量、改 cron 都要在网页里重新保存/Deploy，
不会自动读取 `wrangler.toml`。

1. 先按上面第 1 步创建 PAT。
2. 登录 <https://dash.cloudflare.com> → **Workers & Pages** → **Create** → **Worker**。
3. 命名，例如 `trendradar-trigger`，点 **Deploy**。
4. 点 **Edit code**，用 `cloudflare/trigger.js` 的全部内容覆盖模板，点 **Deploy**。
5. 进入 **Settings** → **Variables and Secrets**，添加：

   | 名称 | 类型 | 值 |
   |---|---|---|
   | `GITHUB_REPO` | Text | `owner/repo`，例如 `conice/TrendRadar` |
   | `GITHUB_TOKEN` | Secret | 第 1 步的 PAT |

   保存后必须再点一次 **Deploy**；只点 Save 时线上运行的仍是旧配置。

6. 进入 **Settings** → **Triggers** → **Cron Triggers** → **Add Cron Trigger**，
   填入 `30 0-14 * * *`。
7. 打开 Worker 地址做健康检查（见下一节）。

> 两种部署方式二选一，不要用同一个 Worker 名重复部署。
> Dashboard 方式下，`GITHUB_REPO` 和 cron 都要在网页里各填一次；
> 仓库里的 `wrangler.toml` 只是给命令行方式用的。

> 新增、修改或删除 Cron Trigger 后，Cloudflare 可能需要几分钟（最多 15 分钟）
> 才把变更推到全球网络；期间可能错过一次触发，属于正常现象。

## 验证

### 1. 健康检查

浏览器打开 Worker 地址，应返回：

```text
trendradar-trigger is running
```

这只能证明 Worker 部署成功，不代表 cron 或 PAT 正常。

### 2. 看实时日志

另开一个终端：

```bash
cd cloudflare
npx wrangler tail
```

等到下一个整点第 30 分钟，正常会看到：

```text
[cron] 已触发 conice/TrendRadar (30 0-14 * * *)
```

如果一直没有日志，说明 cron 没生效或当前不在 `0-14` 点范围内。

### 3. 用临时 cron 快速测试

不想等到下一个整点，可以临时改成每分钟：

```toml
[triggers]
crons = ["* * * * *"]
```

然后：

```bash
npx wrangler deploy
npx wrangler tail
```

一分钟内应看到 `[cron] 已触发 ...`。测试完立刻改回：

```toml
crons = ["30 0-14 * * *"]
```

再 `npx wrangler deploy`。

> 不要长期保留每分钟的 cron：会连续触发 Actions，而且 `crawler.yml` 的
> `cancel-in-progress: true` 会让新运行取消正在跑的任务。

### 4. 检查 GitHub Actions

打开 GitHub 仓库 → **Actions** → **Get Hot News**：

- 应出现一条 `repository_dispatch` 触发的运行；
- 点进去看 `Run crawler` 步骤是否成功；
- 如果运行失败，先看该次日志，不要只盯 Worker。

### 5. 端到端验收

一次完整链路应满足：

1. Worker 日志出现 `[cron] 已触发 ...`；
2. Actions 出现 `repository_dispatch` 运行；
3. `Run crawler` 成功；
4. 你的通知渠道收到消息（如果当前时间段的 `timeline` 允许推送）；
5. 仓库或 Cloudflare Pages 的 `index.html` 更新（如果配置了网页部署）。

## GitHub Actions 侧

`.github/workflows/crawler.yml` 现在只有两个入口：

```yaml
on:
  repository_dispatch:
    types: [trendradar-run]
  workflow_dispatch:
```

- `repository_dispatch`：Worker 使用。
- `workflow_dispatch`：GitHub 页面手动运行，便于调试。
- GitHub 原生 `schedule` 已移除，避免和 Worker 重复运行。

注意：

- `repository_dispatch` **只会在默认分支上查找 workflow**。
  如果 `crawler.yml` 只存在于其它分支，Worker 会收到 204 但不会启动运行。
- workflow 被禁用后，`repository_dispatch` 也不会启动。
  30 天 Check Expiration 仍然有效；每 30 天运行一次 Actions 里的 **Check In**。
- `concurrency` 是 `cancel-in-progress: true`：手动运行撞上正在跑的任务会取消旧任务。
- 公开仓库的 Actions 分钟数通常免费；私有仓库会计入账户额度。
  当前配置是 15 次/天，先确认你的账户额度够用。

## 配置项

| 位置 | 名称 | 说明 |
|---|---|---|
| `wrangler.toml` | `name` | Worker 名称，决定默认域名前缀；改名等于新建 Worker |
| `wrangler.toml` | `main` | 入口文件，保持 `trigger.js` |
| `wrangler.toml` | `compatibility_date` | Workers 运行时兼容日期，一般不用改 |
| `wrangler.toml` | `GITHUB_REPO` | `owner/repo`，不是完整 URL |
| `wrangler.toml` | `[triggers].crons` | cron 表达式数组；本方案只放一个 |
| Worker Secret | `GITHUB_TOKEN` | PAT，绝不写进 `wrangler.toml` |
| `trigger.js` | `EVENT_TYPE` | 必须是 `trendradar-run`，与 `crawler.yml` 的 `types` 一致 |

修改 `EVENT_TYPE` 时，必须同时改两处：

```js
// cloudflare/trigger.js
const EVENT_TYPE = 'trendradar-run';
```

```yaml
# .github/workflows/crawler.yml
types: [trendradar-run]
```

只改一边的结果是：Worker 日志正常，但 Actions 永远不启动。

## 限制与成本

### Cloudflare 免费版 cron 配额

Workers Free 每账户最多 **5 个 cron trigger**。本 Worker 占 1 个。

如果同一 Cloudflare 账户里已经有 5 个（例如 daily-digest-bot 的 Worker 已用满），
第 6 个无法部署，`wrangler deploy` 会报 cron trigger 配额错误。可选：

- 换一个 Cloudflare 账户部署本 Worker；
- 升级 Workers Paid；
- 在另一个项目里合并现有 cron 表达式，腾出一个配额；
- 不用 Worker，回退到 GitHub `schedule`（会重新遇到排队问题）。

### Actions runner 仍然会排队

Worker 解决的是 GitHub `schedule` 的排队问题，不绕过 Actions runner 队列。
如果 GitHub 本身繁忙，运行仍可能延迟；但通常比 `schedule` 快很多。

### 30 天签到

`crawler.yml` 里的 Check Expiration 步骤仍会在 30 天后禁用 workflow。
workflow 被禁用后，`repository_dispatch` 也不会启动运行。

每 30 天在 Actions 里手动运行一次 **Check In**（`.github/workflows/clean-crawler.yml`）。
Worker 不能替代签到。

### 重复运行

正常情况每小时只触发一次，`crawler.yml` 的 job 超时是 15 分钟，不会重叠。

以下情况可能重复：

- 你又在 `crawler.yml` 里启用了 GitHub `schedule`；
- 你在 Actions 页面手动点了 `Run workflow`；
- 临时测试 cron 时保留了每分钟的表达式。

`concurrency` 会取消旧运行，但不会阻止重复触发；测试完记得改回正式 cron。

## 排查

| 现象 | 原因与处理 |
| --- | --- |
| `wrangler deploy` 报 cron 配额错误 | 账户已有 5 个 cron trigger。换账户、升级，或合并现有触发器 |
| Worker 地址打不开 / 502 | 部署失败或代码语法错误。重新 `npx wrangler deploy`，看完整报错 |
| 健康页正常，但 Actions 没有运行 | 先看 `wrangler tail` 有没有 `[cron]`；再看 PAT、`GITHUB_REPO`、事件类型、workflow 是否禁用、是否在默认分支 |
| `wrangler tail` 一直没有 `[cron]` | cron 没生效：确认 `[triggers]` 在顶层、表达式是 UTC、改完执行过 `deploy`、当前时间在 `0-14` 点范围内 |
| Worker 日志 `HTTP 401` | PAT 无效或过期；重新生成，`wrangler secret put GITHUB_TOKEN` |
| Worker 日志 `HTTP 403` | PAT 没有 **Contents: Read and write**，或选错了仓库 |
| Worker 日志 `HTTP 404` | `GITHUB_REPO` 写错，必须是 `owner/repo` |
| Actions 有运行但 `Checkout` 失败 | 仓库权限 / workflow 本身问题，不是 Worker 的问题 |
| Actions 成功但没收到推送 | 看 `config/timeline.yaml` 当前时段是否 `push: true`；看 `config/config.yaml` 通知是否启用；增量模式下没有新内容也会跳过 |
| 时间比预期晚 | Cloudflare cron 是 UTC；北京时间要减 8 小时。Actions runner 也可能排队 |
| 运行 30 天后自动停止 | Check Expiration 禁用了 workflow。运行 **Check In** 续期，再手动启用 workflow |
| 一天跑了很多次 | 检查是否还留着 GitHub `schedule`、临时每分钟 cron，或有人手动触发 |
| `wrangler login` 打不开浏览器 | 用 `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` 环境变量部署 |

### Worker 正常，但 Actions 完全不动

按顺序隔离，不要一上来就改代码：

1. **Worker 活着吗**：浏览器打开 Worker 地址，应返回 `trendradar-trigger is running`。
2. **cron 触发了吗**：`npx wrangler tail`，等到点，看有没有 `[cron] 已触发 ...`。
   - 没有：问题在 Cloudflare cron 配置，不在 GitHub。
   - 有：继续第 3 步。
3. **PAT 和仓库对吗**：直接调 GitHub API：

   ```bash
   curl -i -X POST \
     -H "Authorization: Bearer $GITHUB_TOKEN" \
     -H "Accept: application/vnd.github+json" \
     -H "Content-Type: application/json" \
     https://api.github.com/repos/OWNER/REPO/dispatches \
     -d '{"event_type":"trendradar-run"}'
   ```

   期望 `HTTP 204`。
   - 401：token 无效 / 过期；
   - 403：权限不足；
   - 404：仓库名错，或 token 没有该仓库权限。

4. **workflow 在默认分支吗**：打开仓库默认分支的
   `.github/workflows/crawler.yml`，确认存在：

   ```yaml
   on:
     repository_dispatch:
       types: [trendradar-run]
   ```

5. **workflow 被禁用了吗**：Actions 页面如果提示 workflow disabled，
   先启用，再运行 **Check In**。

### cron 不触发

1. 确认 `wrangler.toml` 是：

   ```toml
   [triggers]
   crons = ["30 0-14 * * *"]
   ```

   不是 `[vars]` 下面。

2. 确认改完后执行过：

   ```bash
   npx wrangler deploy
   ```

3. 确认当前 UTC 时间在 `00:30-14:30` 之间。
   北京时间 23:00 = UTC 15:00，不在范围内，本来就不会触发。

4. 用 `npx wrangler tail` 看 Workers Logs；也可以在 Cloudflare 后台 →
   Workers & Pages → 你的 Worker → Logs 查看。

## 安全

- Worker 地址是公开的，但 `fetch` 只返回健康检查文本，
  **没有任何公网接口能触发 Actions**；只有 Cloudflare cron 能调用 `scheduled()`。
- `GITHUB_TOKEN` 存为 Worker Secret，不出现在仓库和 `wrangler.toml` 里。
- PAT 只授一个仓库的 Contents 写权限，泄漏面最小。
- Worker 不存任何报告内容，也没有 KV / 数据库。
- 建议给 Cloudflare 账户开启 2FA；PAT 设置合理过期时间，过期前更新 Secret。

## 暂停、卸载与回退

### 暂停定时

删除或清空 `wrangler.toml` 的 triggers，然后重新 deploy：

```toml
[triggers]
crons = []
```

或者直接删掉整个 `[triggers]` 段再 deploy。Worker 本身还在，健康页仍可访问。

### 完全删除

```bash
cd cloudflare
npx wrangler delete
```

这会删除 Worker 和它的 cron。GitHub 仓库里的 `crawler.yml` 不受影响，
仍然可以用 `workflow_dispatch` 手动运行。

### 回退到 GitHub 原生 schedule

如果暂时不想用 Worker，在 `crawler.yml` 的 `on:` 下加回：

```yaml
on:
  schedule:
    - cron: "30 0-14 * * *"
  workflow_dispatch:
```

提交后 GitHub 会按自己的调度运行。注意：

- 先停掉 Worker 的 cron，否则两边会重复触发；
- GitHub `schedule` 仍可能排队数十分钟到数小时。

## 文件说明

| 文件 | 作用 |
|---|---|
| `cloudflare/trigger.js` | Worker 入口：cron → GitHub `repository_dispatch` |
| `cloudflare/wrangler.toml` | Worker 名称、仓库、cron 配置 |
| `cloudflare/README.md` | 本文档 |
| `.github/workflows/crawler.yml` | Actions 侧触发入口与抓取流程 |
| `.github/workflows/clean-crawler.yml` | Check In：重置 30 天计时 |

## 验收清单

- [ ] `npx wrangler deploy` 成功，且部署输出或 Dashboard 的 Triggers 里能看到 `30 0-14 * * *`
- [ ] 浏览器打开 Worker 地址返回 `trendradar-trigger is running`
- [ ] `npx wrangler tail` 在整点第 30 分钟看到 `[cron] 已触发 ...`
- [ ] GitHub Actions 出现 `repository_dispatch` 运行
- [ ] `Run crawler` 成功
- [ ] 当前时间段允许推送时，通知渠道收到消息
- [ ] 每 30 天运行一次 **Check In**
