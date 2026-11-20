/**
 * TrendRadar 定时触发器（Cloudflare Worker）。
 *
 * 职责只有一件事：cron 到点 POST 一次 repository_dispatch，
 * 由 .github/workflows/crawler.yml 的 repository_dispatch 入口启动 Actions。
 *
 * 为什么不用 GitHub 原生 schedule：
 * GitHub 的 schedule 经常排队数十分钟到数小时；dispatch 通常几秒内开始。
 * 报告内容、抓取数据、通知全部在 Actions 里完成，Worker 不接触。
 *
 * 部署见同目录 README.md。
 */

/** 必须与 crawler.yml 的 repository_dispatch.types 一致 */
const EVENT_TYPE = 'trendradar-run';

async function dispatch(env, cron) {
  if (!env.GITHUB_REPO) {
    throw new Error('GITHUB_REPO 未配置');
  }
  if (!env.GITHUB_TOKEN) {
    throw new Error('GITHUB_TOKEN 未配置');
  }

  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: 'application/vnd.github+json',
      'content-type': 'application/json',
      'user-agent': 'trendradar-trigger',
    },
    body: JSON.stringify({
      event_type: EVENT_TYPE,
      client_payload: {
        source: 'cloudflare-cron',
        cron,
      },
    }),
  });

  if (!res.ok) {
    // 204 是成功；401/403/404 必须进日志，否则表现是“Worker 正常但 Actions 不动”
    const body = await res.text().catch(() => '');
    throw new Error(`GitHub dispatch 失败: HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ''}`);
  }

  console.log(`[cron] 已触发 ${env.GITHUB_REPO} (${cron})`);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      dispatch(env, event.cron).catch((err) => {
        console.error(`[cron] ${event.cron} 触发失败: ${err instanceof Error ? err.message : String(err)}`);
        throw err;
      }),
    );
  },

  async fetch() {
    // 浏览器直接打开 Worker 地址即可确认部署成功
    return new Response('trendradar-trigger is running', { status: 200 });
  },
};
