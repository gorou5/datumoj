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
`assets/`（fonts.css、scene.js、three.js）——2026-10-25 上传时未随附，
补齐 assets/ 后交互场景即完整。
