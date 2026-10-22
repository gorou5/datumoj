"""礼貌的 HTTP 客户端（HttpFetcher 的实现）、robots.txt 缓存和测试用的回放 transport。

抓取守则见 PLAN 第 5 节：按 host 限速、遵守 robots.txt、条件请求、指数退避重试、大小上限、重定向逐跳检查。
结果、错误和日志里的 URL 一律去 Key，httpx 自带的请求日志也在本模块导入时加了去 Key 过滤。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import string
import time
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from datumoj.core.contracts import FetchResult, SourceSpec, UrlNotAllowed
from datumoj.core.models import FetchOutcome, redact_url
from datumoj.testing.fakes import UnregisteredUrl

__all__ = [
    "USER_AGENT", "PoliteHttpFetcher", "Recorded", "ReplayTransport", "RobotsCache", "RobotsRules", "UnregisteredUrl",
]

USER_AGENT = "DATUMOJ/0.1 (+https://github.com/gorou5/datumoj)"
ROBOTS_MAX_BYTES = 500 * 1024
"""RFC 9309 要求至少解析 500 KiB；更长的部分丢弃。"""
ROBOTS_MAX_REDIRECTS = 5
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_STEP = 64 * 1024

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 去 Key ----------

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _redact_text(text: str) -> str:
    """把一段文字里出现的每个 URL 都去 Key。"""
    return _URL_RE.sub(lambda m: redact_url(m.group()), text)


class _RedactUrls(logging.Filter):
    """日志记录里的 URL 去 Key（httpx 的 "HTTP Request: GET <url> ..." 会带上完整 URL）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_text(str(a)) if isinstance(a, str | httpx.URL) else a for a in record.args)
        return True


for _name in ("httpx", __name__):
    logging.getLogger(_name).addFilter(_RedactUrls())


# ---------- 地址检查 ----------


def _non_public(text: str) -> bool:
    """IP 字面量是私网、回环、链路本地、保留或组播地址时为 True；不是 IP 返回 False。"""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def _system_resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []  # 解析失败交给请求本身报 network_error
    return [str(info[4][0]) for info in infos]


def _parse_url(url: str) -> httpx.URL:
    try:
        return httpx.URL(url)
    except (httpx.InvalidURL, TypeError):
        raise ValueError(f"URL 不合法：{redact_url(str(url))}") from None


# ---------- 正文读取与 gzip ----------


class _BodyError(Exception):
    """正文无法解码（gzip 损坏、截断、不支持的 Content-Encoding）。"""


def _identity(chunks: Iterable[bytes]) -> Iterator[bytes]:
    yield from chunks


def _gunzip(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """多段（multi-member）gzip 流式解压；每次最多产出 _STEP 字节，调用方随时可以停下。"""
    d, started = zlib.decompressobj(zlib.MAX_WBITS | 16), False
    for data in chunks:
        more = bool(data)
        while more:
            if not started:
                if not data.strip(b"\0"):
                    break  # 段后的零填充，gzip 工具也忽略
                started = True
            out = d.decompress(data, _STEP)
            if out:
                yield out
            if d.eof:
                data, d, started = d.unused_data, zlib.decompressobj(zlib.MAX_WBITS | 16), False
                more = bool(data)
            else:
                data = d.unconsumed_tail
                more = bool(data) or len(out) == _STEP  # 输出满了说明 zlib 里可能还有没吐出的数据
    if started:
        raise _BodyError("gzip 数据不完整（流被截断）")


def _decoder(content_encoding: str | None) -> Callable[[Iterable[bytes]], Iterator[bytes]]:
    codings = [c.strip().lower() for c in (content_encoding or "").split(",")]
    codings = [c for c in codings if c and c != "identity"]
    if not codings:
        return _identity
    if codings in (["gzip"], ["x-gzip"]):
        return _gunzip
    raise _BodyError(f"不支持的 Content-Encoding：{content_encoding}")


def _read_body(response: httpx.Response, limit: int | None, truncate: bool) -> tuple[bytes, bool]:
    """边下边解边判：返回 (正文, 是否超限)。超限时立即停止读取，正文只保留前 limit 字节。

    truncate=False 时，未压缩且 Content-Length 已超限的响应一个字节都不读。
    """
    decode = _decoder(response.headers.get("content-encoding"))
    if limit is not None and not truncate and decode is _identity:
        try:
            declared = int(response.headers.get("content-length", ""))
        except ValueError:
            declared = -1
        if declared > limit:
            return b"", True
    buf = bytearray()
    try:
        for piece in decode(response.iter_raw()):
            if limit is not None and len(buf) + len(piece) > limit:
                buf += piece[: limit - len(buf)]
                return bytes(buf), True
            buf += piece
    except zlib.error as exc:
        raise _BodyError(f"gzip 解码失败：{exc}") from None
    return bytes(buf), False


@dataclass
class _Reply:
    status: int | None
    """None 表示网络错误。"""
    headers: httpx.Headers = field(default_factory=httpx.Headers)
    body: bytes = b""
    """只有 2xx 才读正文。"""
    too_large: bool = False
    error: str | None = None


# ---------- robots.txt ----------

_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")


def _robots_norm(text: str) -> str:
    """非 ASCII 字符按 UTF-8 百分号编码；%XX 里是非保留字符的解码（%70 → p），其余统一成大写十六进制。"""
    text = "".join(ch if ord(ch) < 128 else quote(ch, safe="") for ch in text)

    def fix(m: re.Match[str]) -> str:
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()

    return re.sub(r"%([0-9A-Fa-f]{2})", fix, text)


def _robots_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    return re.compile(".*".join(re.escape(part) for part in body.split("*")) + (r"\Z" if anchored else ""), re.DOTALL)


@dataclass(frozen=True)
class RobotsRules:
    """一个 origin 的 robots.txt 里适用于本爬虫的规则（RFC 9309）。

    rules 是 (规范化后的路径模式, 是否 Allow, 编译好的正则)。unreachable 不为空时全部禁止（robots.txt 取不到）。
    """

    rules: tuple[tuple[str, bool, re.Pattern[str]], ...] = ()
    unreachable: str | None = None

    @classmethod
    def parse(cls, text: str, agent: str) -> RobotsRules:
        """取 user-agent 等于 agent（产品名，不分大小写）的全部组；没有就取 ``*`` 的组。"""
        agent = agent.lower()
        groups: list[tuple[set[str], list[tuple[str, bool]]]] = []
        in_agents = False
        for raw in text.lstrip("﻿").splitlines():
            key, sep, value = raw.split("#", 1)[0].partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not in_agents:
                    groups.append((set(), []))
                    in_agents = True
                groups[-1][0].add(value.split("/", 1)[0].strip().lower())
            elif key in ("allow", "disallow"):
                in_agents = False
                if groups and value:  # 空的 Disallow 等于不限制
                    groups[-1][1].append((value, key == "allow"))
        name = agent if any(agent in names for names, _ in groups) else "*"
        chosen = [rule for names, rules in groups if name in names for rule in rules]
        return cls(rules=tuple((p, allow, _robots_regex(p)) for p, allow in ((_robots_norm(v), a) for v, a in chosen)))

    def blocked_reason(self, target: str) -> str | None:
        """target 是路径加查询串（原始编码）；允许时返回 None。按最长匹配，等长时 Allow 优先。"""
        if self.unreachable is not None:
            return self.unreachable
        path = _robots_norm(target)
        best: tuple[int, bool, str] | None = None
        for pattern, allow, regex in self.rules:
            if regex.match(path) and (best is None or (len(pattern), allow) > best[:2]):
                best = (len(pattern), allow, pattern)
        if best is None or best[1]:
            return None
        return f"robots.txt 禁止（Disallow: {best[2]}）"


class RobotsCache:
    """按 origin（scheme + host + 端口，即每个 robots.txt 的地址）缓存规则，ttl 秒后重新获取。

    RFC 9309 建议缓存不超过 24 小时。取不到 robots.txt（网络错误、429、5xx）的结果也缓存，期间全部禁止。
    """

    def __init__(self, ttl: float = 86400.0, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._entries: dict[str, tuple[float, RobotsRules]] = {}

    def get(self, robots_url: str, load: Callable[[], RobotsRules]) -> RobotsRules:
        now = self._clock()
        hit = self._entries.get(robots_url)
        if hit is None or now - hit[0] >= self.ttl:
            hit = (now, load())
            self._entries[robots_url] = hit
        return hit[1]


# ---------- 客户端 ----------


class PoliteHttpFetcher:
    """基于 httpx 同步客户端的 HttpFetcher。

    - 同一 host 的请求间隔至少 1/rate_per_sec 秒（robots.txt 和重试也算）；clock、sleep、now 可注入。
    - 429、5xx 和网络错误按 backoff·2^n 秒（不超过 max_wait）重试，最多 max_retries 次；有 Retry-After 就按它等，
      要求等待超过 max_wait 时不再重试；其他 4xx 不重试。
    - 请求 ``Accept-Encoding: identity``；服务器仍回 gzip 时自己解压（支持多段），max_bytes 按解压后的字节数边下边判。
    - 不让 httpx 自动跟随重定向，逐跳检查 scheme、allowed_hosts（``*.go.jp`` 匹配子域名）、私网/回环地址和 robots.txt。
    - 私网判断：IP 字面量和 localhost 一律拒绝；域名用 resolver 解析后检查。走真实网络（没给 transport）时默认用
      系统 DNS；注入了 transport（MockTransport、ReplayTransport）时默认不解析，需要时显式传 resolver。
    - robots.txt 也经由同一个 transport 获取：用 ReplayTransport 回放时要登记 ``/robots.txt``（例如 404 表示不限制）。
    """

    def __init__(
        self,
        allowed_hosts: Iterable[str],
        *,
        rate_per_sec: float = 1.0,
        max_bytes: int | None = None,
        max_retries: int = 3,
        backoff: float = 1.0,
        max_wait: float = 60.0,
        max_redirects: int = 10,
        timeout: float = 30.0,
        user_agent: str = USER_AGENT,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[[str], Iterable[str]] | None = None,
        robots: RobotsCache | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utcnow,
    ):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec 必须大于 0")
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes 必须大于 0")
        if max_retries < 0 or max_redirects < 0:
            raise ValueError("max_retries 和 max_redirects 不能小于 0")
        self.allowed_hosts = [h.strip().lower().rstrip(".") for h in allowed_hosts]
        self.max_bytes = max_bytes
        self.max_retries = max_retries
        self.backoff = backoff
        self.max_wait = max_wait
        self.max_redirects = max_redirects
        self.robots = robots if robots is not None else RobotsCache(clock=clock)
        self._interval = 1.0 / rate_per_sec
        self._resolver = resolver if resolver is not None else (_system_resolve if transport is None else None)
        self._agent = re.split(r"[/\s]", user_agent.strip(), maxsplit=1)[0]
        self._clock, self._sleep, self._now = clock, sleep, now
        self._next_at: dict[str, float] = {}
        self._client = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
        )

    @classmethod
    def from_spec(cls, spec: SourceSpec, **kwargs: Any) -> PoliteHttpFetcher:
        """按来源配置建客户端；单个文件的上限默认是该来源的 max_total_bytes。"""
        kwargs.setdefault("max_bytes", spec.max_total_bytes)
        return cls(spec.allowed_hosts, rate_per_sec=spec.rate_per_sec, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteHttpFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(
        self, url: str, etag: str | None = None, last_modified: str | None = None,
        *, size: int | None = None, head_probe: bool = False,
    ) -> FetchResult:
        if head_probe and last_modified is not None and size is not None:
            probe = self._fetch(url, last_modified=last_modified, size=size, method="HEAD")
            if probe.outcome in (FetchOutcome.not_modified, FetchOutcome.blocked_by_robots):
                return probe
        return self._fetch(url, etag=etag, last_modified=last_modified)

    def _fetch(
        self, url: str, etag: str | None = None, last_modified: str | None = None,
        *, size: int | None = None, method: str = "GET",
    ) -> FetchResult:
        red = redact_url(url)
        start = _parse_url(url)
        if (problem := self._url_problem(start)) is not None:
            raise UrlNotAllowed(f"{problem}：{red}")
        headers = {}
        if etag and method == "GET":
            headers["If-None-Match"] = etag
        if last_modified and method == "GET":
            headers["If-Modified-Since"] = last_modified
        current, redirected = start, False

        def result(outcome: FetchOutcome, **kw: Any) -> FetchResult:
            final = redact_url(str(current)) if redirected else None
            return FetchResult(url=red, final_url=final, outcome=outcome, fetched_at=self._now(), **kw)

        for _ in range(self.max_redirects + 1):
            here = redact_url(str(current))
            if (reason := self._robots_block(current)) is not None:
                return result(FetchOutcome.blocked_by_robots, error=f"{reason}，未请求：{here}")
            reply = self._request(current, headers, self.max_bytes, method=method)
            if reply.status is None:
                return result(FetchOutcome.network_error, error=f"{reply.error}：{here}")
            hop = self._next_hop(current, reply)
            if isinstance(hop, str):
                return result(FetchOutcome.network_error, http_status=reply.status, error=hop)
            if hop is not None:
                log.info("重定向 %s → %s", here, redact_url(str(hop)))
                current, redirected = hop, True
                continue
            meta = dict(
                http_status=reply.status, etag=reply.headers.get("etag"),
                last_modified=reply.headers.get("last-modified"),
            )
            if method == "GET" and reply.status == 304:
                return result(FetchOutcome.not_modified, **meta)
            if not 200 <= reply.status < 300:
                note = f"；{reply.error}" if reply.error else ""
                return result(FetchOutcome.http_error, error=f"HTTP {reply.status}：{here}{note}", **meta)
            if reply.error:
                return result(FetchOutcome.network_error, error=f"{reply.error}：{here}", **meta)
            if method == "HEAD" and reply.headers.get("last-modified") == last_modified:
                length = reply.headers.get("content-length", "")
                try:
                    same_size = length.isascii() and length.isdecimal() and int(length) == size
                except ValueError:
                    same_size = False
                if same_size:
                    return result(FetchOutcome.not_modified, **meta)
            if reply.too_large:
                return result(
                    FetchOutcome.too_large, error=f"正文超过上限 {self.max_bytes} 字节，已中止下载：{here}", **meta
                )
            return result(FetchOutcome.ok, content=reply.body, content_type=reply.headers.get("content-type"), **meta)
        return result(FetchOutcome.network_error, error=f"重定向超过 {self.max_redirects} 次：{red}")

    # ----- 地址与重定向 -----

    def _url_problem(self, url: httpx.URL) -> str | None:
        """URL 不允许抓取时返回原因。"""
        if url.scheme not in ("http", "https"):
            return f"只允许 http/https，不允许 {url.scheme or '空 scheme'}"
        host = url.host.lower().rstrip(".")
        if not host:
            return "URL 没有 host"
        if not any(host == p or (p.startswith("*.") and host.endswith(p[1:])) for p in self.allowed_hosts):
            return f"host {host} 不在 allowed_hosts 里"
        if host == "localhost" or host.endswith(".localhost") or _non_public(host):
            return f"{host} 是私网或回环地址"
        for addr in self._resolver(host) if self._resolver is not None else ():
            if _non_public(addr):
                return f"{host} 解析到私网或回环地址 {addr}"
        return None

    def _next_hop(self, current: httpx.URL, reply: _Reply) -> httpx.URL | str | None:
        """3xx 带 Location 时返回下一跳；下一跳不允许抓取时返回说明；不是重定向返回 None。"""
        location = reply.headers.get("location")
        if reply.status not in _REDIRECTS or not location:
            return None
        try:
            target = current.join(location)
        except httpx.InvalidURL:
            return f"重定向被拦下：Location 不合法（{redact_url(location)}）"
        problem = self._url_problem(target)
        return target if problem is None else f"重定向被拦下：{redact_url(str(target))}（{problem}）"

    # ----- robots.txt -----

    def _robots_block(self, url: httpx.URL) -> str | None:
        if url.path == "/robots.txt":
            return None
        robots_url = httpx.URL(f"{url.scheme}://{url.netloc.decode('ascii')}/robots.txt")
        rules = self.robots.get(str(robots_url), lambda: self._load_robots(robots_url))
        return rules.blocked_reason(url.raw_path.decode("ascii"))

    def _load_robots(self, robots_url: httpx.URL) -> RobotsRules:
        """按 RFC 9309 解读：2xx 解析；4xx 视为没有限制；429、5xx、网络错误视为全部禁止；最多跟 5 次重定向。"""
        current = robots_url
        for _ in range(ROBOTS_MAX_REDIRECTS + 1):
            reply = self._request(current, {}, ROBOTS_MAX_BYTES, truncate=True)
            if reply.status is None:
                return RobotsRules(unreachable=f"robots.txt 无法获取（{reply.error}），按 RFC 9309 视为全部禁止")
            hop = self._next_hop(current, reply)
            if isinstance(hop, str):
                return RobotsRules(unreachable=f"robots.txt 的{hop}，视为全部禁止")
            if hop is not None:
                current = hop
                continue
            if 200 <= reply.status < 300:
                if reply.error:
                    return RobotsRules(unreachable=f"robots.txt 无法解码（{reply.error}），视为全部禁止")
                return RobotsRules.parse(reply.body.decode("utf-8", "replace"), self._agent)
            if reply.status == 429 or reply.status >= 500:
                return RobotsRules(unreachable=f"robots.txt 返回 HTTP {reply.status}，按 RFC 9309 视为全部禁止")
            return RobotsRules()
        return RobotsRules(unreachable=f"robots.txt 重定向超过 {ROBOTS_MAX_REDIRECTS} 次，视为全部禁止")

    # ----- 发送、限速、重试 -----

    def _throttle(self, host: str) -> None:
        now = self._clock()
        ready = self._next_at.get(host, now)
        if ready > now:
            self._sleep(ready - now)
            now = max(self._clock(), ready)
        self._next_at[host] = now + self._interval

    def _retry_after(self, value: str | None) -> float | None:
        if not value:
            return None
        value = value.strip()
        if value.isdigit():
            return float(value)
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - self._now()).total_seconds())

    def _request(
        self, url: httpx.URL, headers: Mapping[str, str], limit: int | None,
        *, truncate: bool = False, method: str = "GET",
    ) -> _Reply:
        """GET / HEAD（含重试）。只捕获 httpx 的网络错误；其他异常照常抛出。"""
        for attempt in range(self.max_retries + 1):
            self._throttle(url.host)
            backoff = min(self.backoff * 2**attempt, self.max_wait)
            try:
                reply = self._attempt(url, headers, limit, truncate, method)
            except httpx.TransportError as exc:
                reply = _Reply(None, error=_redact_text(f"{type(exc).__name__}: {exc}"))
                wait = backoff
            else:
                if reply.status != 429 and reply.status < 500:
                    return reply
                retry_after = self._retry_after(reply.headers.get("retry-after"))
                wait = backoff if retry_after is None else retry_after
            if attempt == self.max_retries:
                break
            if wait > self.max_wait:
                reply.error = f"Retry-After 要求等待 {wait:.0f} 秒，超过上限 {self.max_wait:.0f} 秒，不再重试"
                break
            why = reply.error or f"HTTP {reply.status}"
            log.info("%s 失败（%s），%.1f 秒后第 %d 次重试", redact_url(str(url)), why, wait, attempt + 1)
            self._sleep(wait)
        return reply

    def _attempt(
        self, url: httpx.URL, headers: Mapping[str, str], limit: int | None, truncate: bool, method: str,
    ) -> _Reply:
        response = self._client.send(self._client.build_request(method, url, headers=headers), stream=True)
        try:
            reply = _Reply(response.status_code, response.headers)
            if method != "HEAD" and 200 <= response.status_code < 300:
                try:
                    reply.body, reply.too_large = _read_body(response, limit, truncate)
                except _BodyError as exc:
                    reply.error = str(exc)
            return reply
        finally:
            response.close()


# ---------- 回放 ----------


def _is_key_param(name: str) -> bool:
    """只按参数名判断是不是 Key（和 redact_url 同一套规则），不看参数值。"""
    probe = f"http://h/?{name}=v"
    return redact_url(probe) != probe


def _replay_key(url: httpx.URL) -> tuple[Any, ...]:
    """原始路径（不做百分号解码）+ 去掉 Key 参数后排序的原始参数。"""
    path, _, query = url.raw_path.partition(b"?")
    params = sorted(p for p in query.split(b"&") if p and not _is_key_param(p.partition(b"=")[0].decode("ascii")))
    return (url.scheme, url.host, url.port, path, tuple(params))


@dataclass(frozen=True)
class Recorded:
    """一条录制：fixture 文件（None 表示空正文）、状态码、响应头。"""

    path: str | Path | None = None
    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)


class ReplayTransport(httpx.BaseTransport):
    """按登记表回放录制样本，不访问网络。

    mapping 的值可以是 ``Recorded``、fixture 路径，或 ``(路径, 状态码)``。URL 比较时忽略 Key 参数（按参数名判断）、
    参数顺序无关，路径按原始形式比较（``/a%2Fb`` 和 ``/a/b`` 不同）。未登记的 URL 抛 UnregisteredUrl。
    fixture 在构造时就读入，文件不存在立即报错。``requests`` 记录每次请求（去 Key 后）。
    """

    def __init__(self, mapping: Mapping[str, Recorded | str | Path | tuple[str | Path | None, int]]):
        self._routes: dict[tuple[Any, ...], tuple[Recorded, bytes]] = {}
        for url, entry in mapping.items():
            if isinstance(entry, tuple):
                entry = Recorded(*entry)
            elif not isinstance(entry, Recorded):
                entry = Recorded(entry)
            key = _replay_key(_parse_url(url))
            if key in self._routes:
                raise ValueError(f"回放重复登记（去 Key 后相同）：{redact_url(url)}")
            self._routes[key] = (entry, b"" if entry.path is None else Path(entry.path).read_bytes())
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        red = redact_url(str(request.url))
        self.requests.append(red)
        hit = self._routes.get(_replay_key(request.url))
        if hit is None:
            raise UnregisteredUrl(f"回放没有登记这个 URL：{red}")
        recorded, body = hit
        # 用 stream= 而不是 content=：后者会让 httpx 立即按 Content-Encoding 解码，回放就不再是原始字节
        return httpx.Response(recorded.status, headers=dict(recorded.headers), stream=httpx.ByteStream(body))
