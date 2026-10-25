// navi.datumoj.com/feed.json — the Datumoj activity feed.
// Items are written to KV (key "items", a JSON array, newest first) ONLY by
// the daily feed job, and ONLY from verified events (GitHub PR/commit data,
// recorded ops logs, collaborator inbox files). No item is ever invented:
// when nothing changed, the job writes nothing and this endpoint keeps
// serving the existing history.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/feed.json') return new Response('not found', { status: 404 });
    let raw = null;
    let err = null;
    try {
      raw = await env.NAVI_FEED.get('items');
    } catch (e) {
      err = String(e);
    }
    return new Response(raw || '[]', {
      headers: {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'public, max-age=120',
        'access-control-allow-origin': '*',
        'x-feed-raw': raw === null ? 'null' : String(raw.length),
        ...(err ? { 'x-feed-error': err } : {}),
      },
    });
  },
};
