// Cross-account R2 access for the nbfun account's bucket `tel`.
//
// The tel Worker runs in the Datumoj account, so it cannot hold a native
// R2 binding to a bucket owned by the nbfun account; all block pages and
// (since the storage consolidation) number-page snapshots are therefore
// read/written through R2's S3-compatible API with SigV4 signing.
// Credentials arrive as Worker secrets — they are never in the repo:
//   NBFUN_R2_ENDPOINT          e.g. https://<nbfun-account-id>.r2.cloudflarestorage.com
//   NBFUN_R2_ACCESS_KEY_ID
//   NBFUN_R2_SECRET_ACCESS_KEY
// Bucket name is fixed (`tel`); override with NBFUN_R2_BUCKET if ever
// needed for a staging bucket.
//
// Design rule (same as the old binding layer): this module never throws.
// Any misconfiguration, network failure or non-2xx response degrades to
// null/false and the caller falls back to plain SSR / 404 — storage is an
// optimisation layer, never a dependency.

const enc = new TextEncoder();

function hex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function sha256(data) {
  return hex(await crypto.subtle.digest('SHA-256', typeof data === 'string' ? enc.encode(data) : data));
}

async function hmac(key, data) {
  const k = typeof key === 'string' ? enc.encode(key) : key;
  const cryptoKey = await crypto.subtle.importKey('raw', k, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', cryptoKey, enc.encode(data)));
}

function r2Config(env) {
  if (!env || !env.NBFUN_R2_ENDPOINT || !env.NBFUN_R2_ACCESS_KEY_ID || !env.NBFUN_R2_SECRET_ACCESS_KEY) {
    return null;
  }
  return {
    endpoint: String(env.NBFUN_R2_ENDPOINT).replace(/\/+$/, ''),
    accessKey: env.NBFUN_R2_ACCESS_KEY_ID,
    secretKey: env.NBFUN_R2_SECRET_ACCESS_KEY,
    bucket: env.NBFUN_R2_BUCKET || 'tel',
  };
}

// SigV4-signed fetch against the bucket. Returns the Response or null.
async function r2Fetch(env, method, key, { body = null, contentType = null, meta = null, timeoutMs = 4000 } = {}) {
  const cfg = r2Config(env);
  if (!cfg) return null;
  try {
    const url = new URL(`${cfg.endpoint}/${cfg.bucket}/${key.split('/').map(encodeURIComponent).join('/')}`);
    const payloadHash = await sha256(body === null ? '' : body);
    const now = new Date();
    const amzDate = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
    const dateStamp = amzDate.slice(0, 8);
    const headers = {
      host: url.host,
      'x-amz-content-sha256': payloadHash,
      'x-amz-date': amzDate,
    };
    if (contentType) headers['content-type'] = contentType;
    if (meta) {
      for (const [k, v] of Object.entries(meta)) headers[`x-amz-meta-${k}`] = String(v);
    }
    const signedHeaders = Object.keys(headers).sort().join(';');
    const canonicalHeaders = Object.keys(headers)
      .sort()
      .map((k) => `${k}:${headers[k]}\n`)
      .join('');
    const canonicalRequest = [method, url.pathname, '', canonicalHeaders, signedHeaders, payloadHash].join('\n');
    const scope = `${dateStamp}/auto/s3/aws4_request`;
    const stringToSign = ['AWS4-HMAC-SHA256', amzDate, scope, await sha256(canonicalRequest)].join('\n');
    let signingKey = await hmac(`AWS4${cfg.secretKey}`, dateStamp);
    signingKey = await hmac(signingKey, 'auto');
    signingKey = await hmac(signingKey, 's3');
    signingKey = await hmac(signingKey, 'aws4_request');
    const signature = hex(await crypto.subtle.sign(
      'HMAC',
      await crypto.subtle.importKey('raw', signingKey, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']),
      enc.encode(stringToSign)
    ));
    headers.authorization =
      `AWS4-HMAC-SHA256 Credential=${cfg.accessKey}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url.toString(), {
        method,
        headers,
        body: body === null ? undefined : body,
        signal: ctrl.signal,
      });
      return res;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return null;
  }
}

// GET an object as text. Returns { body, meta } where meta maps the
// object's user metadata (x-amz-meta-*) to plain keys, or null on any
// miss/failure — callers treat null as "not there", never as an error.
export async function getR2Object(env, key, opts = {}) {
  const res = await r2Fetch(env, 'GET', key, opts);
  if (!res || !res.ok) return null;
  try {
    const meta = {};
    for (const [k, v] of res.headers.entries()) {
      if (k.startsWith('x-amz-meta-')) meta[k.slice('x-amz-meta-'.length)] = v;
    }
    return { body: await res.text(), meta };
  } catch {
    return null;
  }
}

// PUT an object. Returns true on success, false on any failure.
export async function putR2Object(env, key, body, { contentType = null, meta = null, timeoutMs = 8000 } = {}) {
  const res = await r2Fetch(env, 'PUT', key, { body, contentType, meta, timeoutMs });
  return !!(res && res.ok);
}
