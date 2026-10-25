// navi.datumoj.com breadcrumb injector (Cloudflare edge injection).
// The subpages under navi are finished artifacts (offline one-pagers, demo
// pages) that must not be edited; instead this Worker rewrites their HTML
// responses at the edge with HTMLRewriter and prepends a breadcrumb bar:
//   导航 › <parent> › <current page>
// Original files stay untouched; disabling this Worker removes the bars.
const STYLE = `<style>
.datumoj-crumbs{position:fixed;top:10px;left:10px;z-index:9999;display:flex;gap:.4rem;align-items:center;font:500 12.5px/1.5 -apple-system,"Hiragino Sans","Noto Sans CJK SC","Microsoft YaHei",sans-serif;background:rgba(255,255,255,.9);backdrop-filter:blur(6px);border:1px solid rgba(30,50,80,.18);border-radius:999px;padding:.28rem .8rem;box-shadow:0 2px 10px rgba(20,40,70,.12)}
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

const TRAILS = [
  { test: (p) => p.startsWith('/intl-sources/review'), trail: [{ label: '导航', href: '/' }, { label: '七源核验', href: '/intl-sources/' }, { label: '独立评审' }] },
  { test: (p) => p.startsWith('/intl-sources'), trail: [{ label: '导航', href: '/' }, { label: '七源核验' }] },
  { test: (p) => p.startsWith('/nas'), trail: [{ label: '导航', href: '/' }, { label: 'NAS 容量预估' }] },
  { test: (p) => p.startsWith('/report'), trail: [{ label: '导航', href: '/' }, { label: '阶段汇报' }] },
];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const match = TRAILS.find((t) => t.test(url.pathname));
    const res = await fetch(request);
    if (!match) return res;
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('text/html')) return res;
    const nav = crumbNav(match.trail);
    const rw = new HTMLRewriter();
    if (url.pathname.startsWith('/nas')) {
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
