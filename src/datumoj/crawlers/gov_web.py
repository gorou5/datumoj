"""Bounded policy-page discovery; HTTP, robots and persistence belong to the runner."""

from __future__ import annotations

import logging
import posixpath
import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from lxml import etree, html

from datumoj.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.core.models import META_PARENT_SHA256, META_PARENT_URL, FetchOutcome, redact_url

log = logging.getLogger(__name__)
_TRACKING = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "yclid"}
_ASSETS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".css", ".js", ".zip",
           ".xlsx", ".xls", ".doc", ".docx", ".ppt", ".pptx", ".csv", ".mp4", ".mp3"}


def normalize_url(url: str, base: str = "") -> str | None:
    """Canonical discovery key. Reject ambiguous authorities and encoded path separators."""
    try:
        if "\\" in url or any(ord(c) < 32 for c in url):
            return None
        parts = urlsplit(urljoin(base, url.strip()))
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return None
        host = parts.hostname.lower().encode("idna").decode("ascii")
        port = parts.port
        authority = f"[{host}]" if ":" in host else host
        if port is not None and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
            authority += f":{port}"
        if re.search(r"%2f|%5c", parts.path, re.I):
            return None

        def unreserved(match: re.Match[str]) -> str:
            char = chr(int(match[0][1:], 16))
            return char if char.isascii() and (char.isalnum() or char in "-._~") else match[0].upper()

        path = re.sub(r"%[0-9a-fA-F]{2}", unreserved, parts.path or "/")
        trailing = path.endswith(("/", "/.", "/.."))
        path = posixpath.normpath("/" + path.lstrip("/"))
        if trailing and path != "/":
            path += "/"
        path = quote(path, safe="/%:@!$&'()*+,;=-._~")
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if not k.lower().startswith("utm_") and k.lower() not in _TRACKING])
        return urlunsplit((parts.scheme, authority, path, query, ""))
    except (ValueError, UnicodeError):
        return None


def _page(data: bytes) -> etree._Element | None:
    # Content wins over headers/filename, including Shift_JIS government pages.
    if data.lstrip().startswith(b"%PDF-"):
        return None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp932", errors="replace")
    if not re.search(r"<(?:!doctype\s+html|html|head|body|div|p|a)\b", text[:4096], re.I):
        return None
    # lxml rejects an encoding declaration on an already decoded Unicode string.
    text = re.sub(r"^\s*<\?xml\b[^>]*\?>", "", text, count=1, flags=re.I)
    try:
        return html.fromstring(text, parser=html.HTMLParser(no_network=True))
    except (etree.ParserError, ValueError):
        return None


class GovWebCrawler(Crawler):
    """One instance per run; plan resets deduplication and per-seed task budgets."""

    kind = "gov_web"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        scope = ctx.spec.scope
        self._max_depth = scope.get("max_depth", 2)
        self._max_pages = scope.get("max_pages_per_seed", 200)
        self._follow_pdf = scope.get("follow_pdf", True)
        for name, value, minimum in (("max_depth", self._max_depth, 0),
                                     ("max_pages_per_seed", self._max_pages, 1)):
            if type(value) is not int or value < minimum:
                raise ValueError(f"scope.{name} 必须是 >= {minimum} 的整数")
        if type(self._follow_pdf) is not bool:
            raise ValueError("scope.follow_pdf 必须是布尔值")
        seeds = scope.get("seeds")
        if not isinstance(seeds, list) or not seeds:
            raise ValueError("scope.seeds 必须是非空列表")
        self._hosts = {host.lower() for host in ctx.spec.allowed_hosts}
        self._prefixes: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._seen: set[str] = set()
        self._warned: set[str] = set()
        tasks = []
        for seed in seeds:
            if not isinstance(seed, dict) or not isinstance(seed.get("url"), str):
                raise ValueError("种子必须包含 url、label 和可选的 path_prefix")
            url = normalize_url(seed["url"])
            label = seed.get("label")
            if url is None or urlsplit(url).hostname not in self._hosts:
                raise ValueError("种子 URL 必须是 allowed_hosts 内的 http(s) URL")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("种子 label 必须非空")
            prefix = seed.get("path_prefix", urlsplit(url).path.rsplit("/", 1)[0] + "/")
            if not isinstance(prefix, str) or not prefix.startswith("/") or prefix.startswith("//"):
                raise ValueError("path_prefix 必须是绝对目录路径")
            canonical = normalize_url(prefix, url)
            if canonical is None or urlsplit(canonical).query or "#" in prefix or "?" in prefix:
                raise ValueError("path_prefix 不允许查询、片段或含糊的编码路径")
            prefix = urlsplit(canonical).path.rstrip("/") + "/"
            if not urlsplit(url).path.startswith(prefix):
                raise ValueError("种子必须位于 path_prefix 目录内")
            if url in self._seen:
                continue
            seed_key = redact_url(url)
            self._prefixes[seed_key] = prefix
            self._counts[seed_key] = 0
            tasks.append(self._task(url, seed_key, 0, "page", {"label": label}))
        return tasks

    def _task(self, url: str, seed: str, depth: int, role: str, meta: dict) -> FetchTask:
        self._seen.add(url)
        self._counts[seed] += 1
        return FetchTask(url=url, meta={
            **meta, "role": role, "seed": seed, "depth": depth,
            "max_pages_per_seed": self._max_pages,
            "seed_task_number": self._counts[seed],
            "seed_limit_reached": self._counts[seed] == self._max_pages,
        })

    def classify(self, task: FetchTask, result: FetchResult) -> FetchOutcome | None:
        if result.outcome is not FetchOutcome.ok or result.content is None:
            return None
        page = _page(result.content)
        if page is None:
            if result.content.lstrip().startswith(b"%PDF-") or (
                (result.content_type or "").split(";", 1)[0].strip().lower() == "application/pdf"
            ):
                # Metadata is persisted by the runner after classify, before after_fetch.
                task.meta["role"] = "pdf"
            return None
        task.meta["role"] = "page"
        for hidden in page.xpath("//head | //script | //style | //template"):
            hidden.drop_tree()
        if len("".join(page.text_content().split())) < 100:
            return FetchOutcome.spa_shell
        return None

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        if task.meta.get("role") != "page":
            return ()
        if result.outcome is FetchOutcome.ok:
            data, digest = result.content, result.sha256
        elif result.outcome is FetchOutcome.not_modified:
            previous = ctx.manifest.last_success(task.redacted_url)
            if previous is None or previous.sha256 is None:
                return ()
            digest = previous.sha256
            data = ctx.raw.get(digest)
        else:
            return ()
        page = _page(data or b"")
        if page is None or digest is None:
            return ()
        seed = task.meta["seed"]
        prefix = self._prefixes[seed]
        base = normalize_url(result.final_url or task.url)
        if base is None or urlsplit(base).hostname not in self._hosts or not urlsplit(base).path.startswith(prefix):
            return ()
        link_base = base
        base_elements = page.xpath("//base[@href]")
        if base_elements:
            declared_base = normalize_url(base_elements[0].get("href", ""), base)
            if declared_base is not None:
                link_base = declared_base
        tasks = []
        for link in page.xpath("//a[@href] | //area[@href]"):
            url = normalize_url(link.get("href", ""), link_base)
            if url is None or url in self._seen:
                continue
            parts = urlsplit(url)
            if parts.hostname not in self._hosts or not parts.path.startswith(prefix):
                continue
            suffix = posixpath.splitext(parts.path)[1].lower()
            previous = ctx.manifest.last_success(url)
            known_pdf = previous is not None and ctx.manifest.snapshot_meta(previous.snapshot_id).get("role") == "pdf"
            pdf = (suffix == ".pdf" or known_pdf
                   or link.get("type", "").split(";", 1)[0].strip().lower() == "application/pdf")
            depth = task.meta["depth"] + 1
            if (pdf and not self._follow_pdf) or (not pdf and (depth > self._max_depth or suffix in _ASSETS)):
                continue
            if self._counts[seed] >= self._max_pages:
                if seed not in self._warned:
                    log.warning("gov_web seed=%s max_pages_per_seed=%s reached; further tasks omitted",
                                redact_url(seed), self._max_pages)
                    self._warned.add(seed)
                break
            tasks.append(self._task(url, seed, depth, "pdf" if pdf else "page", {
                META_PARENT_URL: redact_url(result.url), META_PARENT_SHA256: digest,
            }))
        return tasks
