DATUMOJ-TACHIBANA-104 已完成实现和自动验收，等待任务要求的人工评审；未 git commit。

实现与逐条核对：

1. HttpFetcher.fetch 仅新增两个可选关键字参数 size=None、head_probe=False，保留原有位置参数；未改其他协议或模型。假实现同步参数及比较语义，原有 HttpFetcherSuite 增补四个合格测试，真实实现与假实现均通过。
2. 开关开启且 last_modified、size 均非 None 时，先发不带条件头的 HEAD。仅 HEAD 2xx、Last-Modified 字符串相同、合法非负 Content-Length 数值与 size 相同时返回 not_modified；保留 HEAD 状态码、ETag、Last-Modified 和重定向后的 URL，不读正文、不写 raw。ETag 变化不妨碍判为未变化。
3. HEAD 信息缺失、不同、非法，或状态错误、网络错误、重试耗尽时，从原 URL 回退到原有条件 GET，保留旧 ETag / Last-Modified 条件头。HEAD 304 也不能直接判为未变化。robots 禁止时返回 blocked_by_robots，不请求被禁止的资源。
4. HEAD 和 GET 共用逐跳地址白名单、私网检查、robots、按 host 限速和重试路径；HEAD 后的 GET 再占一个请求间隔。沿用现有处理路径，未另建绕过保护的 HEAD 客户端。
5. 执行器严格校验 scope.head_probe 是 bool，缺省 False；传入 manifest.last_success 的 size 和原有验证头。HEAD 未变化结果沿用 304 的 max_files 计数、报告 unchanged、manifest not_modified 记录和无 raw 写入路径，保留原成功快照供后续比较。
6. 未修改 sources、抓取周期、依赖、锁文件、Makefile、既有测试或 fixtures；新增测试只使用内存数据和 pytest-httpserver，本地转发 transport 保留生产 URL 校验。未访问真实网站。

变更文件及目的：

- src/datumoj/core/contracts.py：向后兼容的两个参数及 HEAD 行为说明。
- src/datumoj/core/http.py：复用请求流程的 HEAD 探测、保守比较、条件 GET 回退。
- src/datumoj/core/runner.py：读取并校验开关，传递上次成功快照大小。
- src/datumoj/testing/fakes.py：假实现支持新参数与匹配语义。
- src/datumoj/testing/contract_suites.py：补充默认行为、匹配、回退和关键字参数约束。
- tests/core/test_head_probe.py：38 个离线用例，覆盖匹配、零大小、不同/缺失/非法头、405/501/其他错误、缺参数、关闭开关、robots、限速、HTTP/网络重试、重定向保护，以及执行器传参、计数和存储。
- results/DATUMOJ-TACHIBANA-104/：本说明、完整变更清单及测试/复核日志。

验收结果（命令按 acceptance.automated 原文执行）：

- make check T=tests/core/test_head_probe.py：退出码 0，38 passed，0.84 秒；见 head-probe.log。
- make check T=tests：退出码 0，2141 passed，37 warnings，112.77 秒；见 tests.log。
- git diff --check 通过；AST 核对协议、真实实现、假实现的原位置参数及两个新关键字参数的默认值通过；见 review.log。
- make 自动回退使用主仓库已有 .venv，这是 Makefile 规定的沙箱兼容路径；未安装或修改依赖。
- 初轮专项测试的夹具错误已修正：测试服务器重写 Content-Length、请求匹配顺序及历史快照时间排序。初轮输出保留在 head-probe-initial.log；最终两项验收均通过。全量警告来自 fork 和 table_names() 的弃用提示，无测试失败。

人工评审仍需按任务 checklist 确认；未提交、合并或打开任何来源开关。所有变更均在 scope 内，完整清单见 changed-files.txt。

未验证/跳过：真实内阁府服务、实际流量节省和任务 208 的周期复查/来源启用，均按范围未执行。
风险：判断依赖服务器 Last-Modified 和 Content-Length 的真实性；内容变化但两者不变时无法识别，开关默认关闭。未引入额外权限、依赖或许可变更。

