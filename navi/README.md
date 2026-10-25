# navi.datumoj.com — Datumoj 导航站

Datumoj 全部对外产出的导航汇总（给人看的页面都从这里进）。

- `/` — 导航首页：最新产出（本站托管）＋全部站点与 plan 子页的外链
- `/report/` — DATUMOJ 阶段汇报（2026-10-25 上传的离线版）
- `/nas/` — DATUMOJ NAS 容量预估（2026-10-25 上传的离线版）
- `/intl-sources/` — 七源核验演示页（2026-10-25）
- `/intl-sources/review/` — 「七源核验」独立评审（APPROVE_WITH_P2）

部署：Cloudflare Pages 项目 `datumoj-navi`（Direct Upload 本目录），
生产部署须带 `--branch main`；自定义域名 navi.datumoj.com。

注意：`/report/` 与 `/nas/` 是离线版页面，引用各自目录下的
`assets/`（fonts.css、scene.js、fonts/）——2026-10-25 已随 zip 补齐，
交互场景完整。

## 最新动态（feed）

- 首页顶部的「最新动态」来自 `navi.datumoj.com/feed.json`，由 `feed-worker/`
  的 Worker 从 KV（NAVI_FEED）读出；每天的定时任务只根据已核实的事件
  （GitHub PR/提交、运维记录、inbox 文件）写入新条目，没有变化就不写。
- 注意：写 KV 必须走 Cloudflare API 的 PUT values 端点；
  `wrangler kv key put` 在本机实测写入后 Worker 读不到（2026-10-25）。

## inbox（协作者同步）

- `inbox/`：协作者把近况文件放这里（格式不限，md/html 优先，协议见 inbox/README.md）。
- 每日任务处理后把文件移入 `inbox/archive/` 归档，并在动态中注明来源。
