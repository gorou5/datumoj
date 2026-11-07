// navi.datumoj.com/feed.json — the Datumoj activity feed.
// Items are written to KV (key "items", a JSON array, newest first) ONLY by
// the daily feed job, and ONLY from verified events (GitHub PR/commit data,
// recorded ops logs, collaborator inbox files). No item is ever invented:
// when nothing changed, the job writes nothing and this endpoint keeps
// serving the existing history.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const key = url.pathname.endsWith('/feed.json') ? 'items' : url.pathname.endsWith('/people.json') ? 'people' : null;
    if (!key) return new Response('not found', { status: 404 });
    let raw = null;
    try {
      raw = await env.NAVI_FEED.get(key);
    } catch {
      raw = null;
    }
    return new Response(raw || '[]', {
      headers: {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'public, max-age=120',
        'access-control-allow-origin': '*',
      },
    });
  },
};
