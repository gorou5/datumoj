// Datumoj breadcrumb injector (Cloudflare edge injection).
// Finished pages across the datumoj family of sites must not be edited;
// instead this Worker rewrites their HTML responses at the edge with
// HTMLRewriter and prepends a breadcrumb pill back to the navi hub:
//   导航 › <site> › <current page>
// Original files stay untouched; disabling this Worker removes the pills.
const STYLE = `<style>
.datumoj-crumbs{position:fixed;bottom:12px;left:10px;z-index:9999;display:flex;gap:.4rem;align-items:center;font:500 12.5px/1.5 -apple-system,"Hiragino Sans","Noto Sans CJK SC","Microsoft YaHei",sans-serif;background:rgba(255,255,255,.9);backdrop-filter:blur(6px);border:1px solid rgba(30,50,80,.18);border-radius:999px;padding:.28rem .8rem;box-shadow:0 2px 10px rgba(20,40,70,.12)}
.datumoj-crumbs a{color:#205f9e;text-decoration:none;white-space:nowrap}
.datumoj-crumbs a:hover{text-decoration:underline}
.datumoj-crumbs .sep{color:#8a97a5}
.datumoj-crumbs .cur{color:#33424f;font-weight:700;white-space:nowrap}
@media (prefers-color-scheme: dark){.datumoj-crumbs{background:rgba(24,32,42,.9);border-color:rgba(255,255,255,.22)}.datumoj-crumbs .cur{color:#dbe4ec}}
</style>`;

function crumbNav(trail) {
  const parts = trail
    .map((c, i) =>
      i === trail.length - 1
        ? `<span class="cur" aria-current="page">${c.label}</span>`
        : `<a href="${c.href}">${c.label}</a><span class="sep">›</span>`
    )
    .join('');
  return `<nav class="datumoj-crumbs" aria-label="面包屑">${parts}</nav>`;
}

const NAVI = 'https://navi.datumoj.com';
const PLAN = 'https://plan.datumoj.com';

// host: exact hostname; test: pathname predicate; trail: root label links
// back to the navi hub (absolute URL when on another subdomain).
const TRAILS = [
  // navi.datumoj.com — hub subpages (same-host, relative links)
  { host: 'navi.datumoj.com', test: (p) => p.startsWith('/intl-sources/review'), trail: [{ label: '导航', href: '/' }, { label: '七源核验', href: '/intl-sources/' }, { label: '独立评审' }] },
  { host: 'navi.datumoj.com', test: (p) => p.startsWith('/intl-sources'), trail: [{ label: '导航', href: '/' }, { label: '七源核验' }] },
  { host: 'navi.datumoj.com', test: (p) => p.startsWith('/nas'), trail: [{ label: '导航', href: '/' }, { label: 'NAS 容量预估' }] },
  { host: 'navi.datumoj.com', test: (p) => p.startsWith('/news'), trail: [{ label: '导航', href: '/' }, { label: '全部动态' }] },
  { host: 'navi.datumoj.com', test: (p) => p.startsWith('/people'), trail: [{ label: '导航', href: '/' }, { label: '协作者贡献' }] },
  { host: 'navi.datumoj.com', test: (p) => p.startsWith('/report'), trail: [{ label: '导航', href: '/' }, { label: '阶段汇报' }] },
  // plan.datumoj.com — plan overview + subpages
  { host: 'plan.datumoj.com', test: (p) => p.startsWith('/orbis'), trail: [{ label: '导航', href: NAVI + '/' }, { label: '计划', href: PLAN + '/' }, { label: '资源目录' }] },
  { host: 'plan.datumoj.com', test: (p) => p.startsWith('/cases'), trail: [{ label: '导航', href: NAVI + '/' }, { label: '计划', href: PLAN + '/' }, { label: '融合分析案例' }] },
  { host: 'plan.datumoj.com', test: (p) => p.startsWith('/verify'), trail: [{ label: '导航', href: NAVI + '/' }, { label: '计划', href: PLAN + '/' }, { label: '失真验证案例' }] },
  { host: 'plan.datumoj.com', test: (p) => p.startsWith('/hf-gov'), trail: [{ label: '导航', href: NAVI + '/' }, { label: '计划', href: PLAN + '/' }, { label: 'HF 数据集调查' }] },
  { host: 'plan.datumoj.com', test: (p) => p === '/' || p === '/index.html' || p === '', trail: [{ label: '导航', href: NAVI + '/' }, { label: '计划总览' }] },
  // task.datumoj.com — task center
  { host: 'task.datumoj.com', test: () => true, trail: [{ label: '导航', href: NAVI + '/' }, { label: '任务中心' }] },
];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const match = TRAILS.find((t) => t.host === url.hostname && t.test(url.pathname));
    const res = await fetch(request);
    if (!match) return res;
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('text/html')) return res;
    const nav = crumbNav(match.trail);
    const rw = new HTMLRewriter();
    if (url.hostname === 'navi.datumoj.com' && url.pathname.startsWith('/nas')) {
      // The NAS page omits <head>/<body> tags entirely (valid HTML5):
      // anchor on <html> and prepend; the parser places style/nav sanely.
      rw.on('html', {
        element(el) {
          el.prepend(STYLE + nav, { html: true });
        },
      });
    } else {
      rw.on('head', {
        element(el) {
          el.append(STYLE, { html: true });
        },
      }).on('body', {
        element(el) {
          el.prepend(nav, { html: true });
        },
      });
    }
    const out = rw.transform(res);
    const headers = new Headers(out.headers);
    headers.delete('content-length');
    return new Response(out.body, { status: out.status, headers });
  },
};
