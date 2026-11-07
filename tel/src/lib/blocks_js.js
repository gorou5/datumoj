// Canonical source of blocks.js — the shared delegated script loaded by
// every block page (/tel/<key>). The production copy lives in the
// nbfun bucket `tel` as object `blocks.js` and is served from there; this
// in-repo constant is the fallback the Worker serves when the bucket copy
// cannot be read, and the reference the data-side generator uploads.
//
// Behaviour (progressive enhancement only — the page works without it,
// numbers just are not clickable):
//   * Click delegation: a click on any grid element whose text is a bare
//     phone number (10–11 digits) navigates to /tel/<digits>, where the
//     number is composed, laid down and counted — the "unknown becomes
//     known" activation path. Real links (<a href>, e.g. signal numbers)
//     are left alone.
//   * In-page finder: if the page carries a [data-block-find] input, its
//     value is located in the grid, highlighted and scrolled to; Enter
//     jumps straight to that number's page. If no such input exists, a
//     minimal one is injected at the top of <main>.
//   * Anchor highlight: arriving with a number in the URL hash
//     (#09012345678) highlights and scrolls to that number.
export const BLOCKS_JS = `(function () {
  'use strict';
  function digitsOfText(t) {
    if (!t) return null;
    var d = String(t).replace(/[\\s\\u3000-]/g, '');
    return /^\\d{10,11}$/.test(d) ? d : null;
  }
  function gridItems() {
    return document.querySelectorAll('.grid a, .grid i, .grid span, .grid li');
  }
  function mark(el) {
    var prev = document.querySelector('[data-hl="1"]');
    if (prev) { prev.removeAttribute('data-hl'); prev.style.background = ''; }
    el.setAttribute('data-hl', '1');
    el.style.background = '#ffe58a';
    el.scrollIntoView({ block: 'center' });
  }
  function findNumber(q) {
    var d = digitsOfText(q);
    if (!d) return null;
    var items = gridItems();
    for (var i = 0; i < items.length; i++) {
      if (digitsOfText(items[i].textContent) === d) { mark(items[i]); return items[i]; }
    }
    return null;
  }
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t && t.closest && t.closest('a[href]')) return; // real link: native behaviour
    var d = t ? digitsOfText(t.textContent) : null;
    if (d) location.href = '/tel/' + d;
  });
  function bindFind(input) {
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        var d = digitsOfText(input.value);
        if (d) location.href = '/tel/' + d;
      }
    });
    input.addEventListener('input', function () { findNumber(input.value); });
  }
  var input = document.querySelector('[data-block-find], #block-find');
  if (!input) {
    var main = document.querySelector('main');
    if (main) {
      var wrap = document.createElement('p');
      wrap.className = 'block-find';
      input = document.createElement('input');
      input.type = 'tel';
      input.setAttribute('data-block-find', '');
      input.placeholder = '番号を入力して探す';
      input.setAttribute('aria-label', '番号を入力して探す');
      wrap.appendChild(input);
      main.insertBefore(wrap, main.firstChild);
    }
  }
  if (input) bindFind(input);
  if (location.hash) {
    var hd = digitsOfText(decodeURIComponent(location.hash.slice(1)));
    if (hd) findNumber(hd);
  }
})();
`;
