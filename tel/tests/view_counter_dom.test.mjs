import { readFileSync } from 'node:fs';
const src = readFileSync(new URL('../src/lib/render.js', import.meta.url), 'utf-8');
const anchor = src.indexOf('This script sits BEFORE');
const s0 = src.lastIndexOf('<script>', anchor);
const s1 = src.indexOf('</script>', anchor);
const script = src.slice(s0 + 8, s1).split('${f.digits}').join('05018071465');

let parsed = false;
const listeners = {};
const vcEl = { textContent: '13 回' };
const mkEl = () => ({ textContent: '', innerHTML: '', value: '', addEventListener() {}, appendChild() {}, className: '' });
const els = { viewCount: vcEl, cmtList: mkEl(), cmtForm: mkEl(), cmtInput: mkEl(), cmtMsg: mkEl() };
globalThis.document = {
  readyState: 'loading',
  getElementById: (id) => (parsed ? (els[id] ?? null) : null),
  addEventListener: (ev, fn) => { listeners[ev] = fn; },
  createElement: () => mkEl(),
};
globalThis.fetch = (url) =>
  Promise.resolve({ ok: true, json: async () => (url.includes('/view/') ? { views: 15 } : { comments: [] }) });

eval(script);
console.log('before DCL (still parsing):', JSON.stringify(vcEl.textContent));
parsed = true; // parser reaches the facts table; #viewCount now exists
listeners['DOMContentLoaded']();
await new Promise((r) => setTimeout(r, 50));
console.log('after DCL + beacon answer:', JSON.stringify(vcEl.textContent));
console.log(vcEl.textContent === '15 回' ? 'PASS: counter updated live' : 'FAIL');
