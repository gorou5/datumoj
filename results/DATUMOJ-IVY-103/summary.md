# DATUMOJ-IVY-103 完成说明

## 变更文件
- `src/datumoj/core/http.py`（新建）：`PoliteHttpFetcher`（实现 `HttpFetcher`）、`RobotsRules`、`RobotsCache`、`ReplayTransport`、`Recorded`。
- `tests/core/test_http.py`（新建）：继承 `HttpFetcherSuite`，另加 110 个本实现的测试（合计 122 个，含参数化）。
- `results/DATUMOJ-IVY-103/summary.md`（本文件）。

契约（models.py / contracts.py / testing/）未改动，也不需要改。

## 验收
- `make check T=tests/core/test_http.py`：`122 passed in 0.30s`。
- 另外跑了全量 `.venv/bin/pytest tests -q`：`1041 passed`（只有 index/vector 原有的 DeprecationWarning）。

## 上次打回理由（review-6）的处理
重定向绕过 robots：现在 `fetch` 的重定向循环在**每一跳**请求之前都调用 `_robots_block`（第 0 跳就是初始 URL），
目标被禁止就返回 `blocked_by_robots`（`final_url` 是被拦下的目标），不发请求。跳到另一个 host 时取那个 host 的 robots.txt。
测试：`test_robots_checked_again_on_every_redirect_hop`（审查者的原样复现：`/start` 302 → `/private/data`，
断言结果为 blocked_by_robots，且请求记录只有 robots.txt 和 `/start`）、`test_redirect_to_other_host_uses_that_hosts_robots`。

## 要求 → 测试
1. HttpFetcher 协议：`TestPoliteHttpFetcherSuite`（合格测试全部通过）。
2. 按 host 限速、时钟注入：`test_rate_limit_is_per_host_and_never_really_sleeps`（逐请求时刻，另一个 host 不用等）、`test_rate_comes_from_spec`。
3. 条件请求与重试：`test_conditional_request_headers_and_304`、`test_5xx_retried_with_exponential_backoff`、`test_retries_are_capped`、
   `test_backoff_capped_by_max_wait`、`test_retry_after_seconds_is_respected`、`test_retry_after_http_date_is_respected`、
   `test_retry_after_longer_than_max_wait_gives_up`、`test_network_error_*`、`test_other_4xx_not_retried`。
4. 边下边判：`test_too_large_aborts_while_streaming`（数了从流里取了几块）、`test_too_large_by_content_length_reads_nothing`。
5. 重定向逐跳检查：`test_redirect_hop_rejected`（外部 host、ftp、file、127.0.0.1、10/8、169.254、::1、IPv4 映射、localhost；
   即使写进 allowed_hosts 也拒绝，且目标未被请求）、`test_host_resolving_to_private_address_rejected`、`test_wildcard_allowed_hosts`、
   `test_initial_url_rejected`、`test_too_many_redirects`。
6. identity + gzip：`test_requests_identity_and_decodes_gzip_anyway`。
7. 去 Key：`test_keys_removed_from_results_errors_and_logs`、`test_keys_removed_from_network_errors_and_exceptions`、`test_redirect_rejection_message_redacted`、`test_malformed_url_is_value_error`。
8. RobotsCache：`test_robots_disallow_blocks_without_request_and_is_cached`、`test_robots_cache_expires_after_ttl`、
   `test_unreachable_robots_disallows_everything`、`test_missing_robots_allows_everything`、`test_robots_redirect_followed`。
9. 回放：`test_replay_real_fixture`（stat_dashboard 真实样本）、`test_replay_ignores_keys_and_param_order`、`test_replay_status_codes`、
   `test_replay_unregistered_url_raises_through_fetcher_without_retry`、`test_replay_unregistered_robots_raises`、
   `test_replay_missing_fixture_fails_at_construction`、`test_replay_duplicate_after_redaction_rejected`。
11. 原始路径、按参数名识别 Key：`test_replay_compares_raw_path`（两个方向）、`test_replay_identifies_keys_by_name_not_value`。
12. robots 判定：`test_pattern_matching`（`%70`→p 解码、`%2F` 不解码、十六进制大小写、非 ASCII、`*`、`$`）、
   `test_longest_match_wins_regardless_of_order`、`test_not_robotparser_first_match_semantics`、`test_equal_length_allow_wins`、
   `test_agent_specific_group_overrides_star`、`test_robots_gzip_and_percent_encoded_request_path`、每一跳判定见上。
13. 多段 gzip、解压后总量边解边判、httpx 日志：`test_multi_member_gzip`、`test_multi_member_split_across_chunks_with_padding`、
   `test_gzip_limit_counts_decoded_total_across_members`、`test_gzip_bomb_aborts_while_decoding`（第一段还没解完就停）、
   `test_broken_gzip_is_network_error`、`test_keys_removed_from_results_errors_and_logs`（检查 `httpx` logger 的记录）。

## 设计要点（给 105 / 901 用的人）
- `PoliteHttpFetcher.from_spec(spec, transport=..., ...)`：限速取 `rate_per_sec`，单文件上限默认取 `max_total_bytes`。
- robots.txt 也走同一个 transport。**用 `ReplayTransport` 回放时要登记 `/robots.txt`**（例如 `(None, 404)` 表示不限制），否则抛 `UnregisteredUrl`。
- robots 取不到（网络错误、429、5xx、重定向被拦或超过 5 次）按 RFC 9309 视为全部禁止，结果在 `RobotsCache` 里缓存 ttl（默认 24 小时）。
- 私网检查：IP 字面量和 localhost 一律拒绝；域名只有走真实网络（没注入 transport）时才默认用系统 DNS 解析检查，注入 transport 时需显式传 `resolver`。
- `UnregisteredUrl` 复用 `datumoj.testing.fakes.UnregisteredUrl`（和 FakeHttpFetcher 同一个类型），因此 `core/http.py` import 了 `datumoj.testing.fakes`。
- 只捕获 `httpx.TransportError` 当作网络错误；其他异常（包括 `UnregisteredUrl`）照常抛出，不重试。

## 跳过 / 未核对 / 风险
- 没跑 ruff（Bash 只放开了 pytest 和验收命令）；也没有用真实网络或 pytest-httpserver 跑过默认 transport 和系统 DNS 路径（本地服务器是 127.0.0.1，会被私网检查拦下）。
- 只支持 `Content-Encoding: gzip / x-gzip / identity`，其他编码（如 br、deflate）记为 network_error。
- 域名解析检查与实际连接之间存在 DNS rebinding 的时间窗口，没有做连接级的地址钉死。
