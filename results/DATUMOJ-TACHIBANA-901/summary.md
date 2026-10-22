# DATUMOJ-TACHIBANA-901 完成说明

自动化实现与验收完成；人工验收待进行。未执行 git commit、真实政府网站抓取或打 tag。

## 变更与方案

- `src/datumoj/wiring.py`：增加薄的正文分派入口，PDF 魔数选择既有 PdfParser，其余交给既有 HtmlPageParser；精确媒体类型与通配入口统一接线。入库和 `cite` 使用相同解析器映射，错误 Content-Type、未知媒体类型和误导扩展名均不影响节/页引用还原。保留自定义 parsers 注入。
- `tests/integration/test_gov_web_e2e.py`：新增 12 项离线测试（含 ParserSuite 合格测试）。使用真实抓取器、执行器、解析器、raw/manifest/chunk 存储、关键词索引、检索器和工具层；只注入本地 HTTP transport、来源 URL、时钟并关闭可选向量检索。所有 HTTP socket 请求限定到 pytest-httpserver。
- 本目录：保存两条验收命令的输出日志，以及显式指定证据目录时生成的 CLI 输出和请求记录；完整文件清单见 `changed-files.txt`。

复用已有实现，不修改各开发线模块，也不复制解析逻辑。未采用仅按响应头选择解析器的方案，因为会使 HTML/PDF 误分派；同时给 `cite` 接线，保证同一原始快照可以重现引用。

`sources/jp-gov-web.yaml` 已能通过真实注册表加载，kind 与 GovWebCrawler 匹配，缺失来源开关默认关闭；`cli.py` 已正确返回抓取/解析/索引失败退出码，因此这两个文件无需修改。

## 要求核对

1. 注册表与默认开关：真实 build 加载 sources，验证 `gov_web` 对应 GovWebCrawler、新来源默认关闭。
2. 端到端：原样回放内阁府政策页及其链接的 36 页 PDF，经 CLI crawl → parse → index，再经 `search_docs` 和 CLI search 检索；断言完整节 locator 和 `page=1`，从 raw 快照恢复正文并核对时间口径、抓取时间和许可范围。PDF metadata 同时保留父页面 URL 与父快照 SHA256。
3. 失败和边界：SPA、空响应和 robots 禁止分别记录失败，crawl 退出 1；被 robots 禁止、超深度、前缀外及白名单外链接均没有实际 HTTP 请求。去掉导航后正文不足 100 字的合成详情页记为解析失败，parse/index 退出 1，不进入文档库。
4. 重复运行：三个成功资源第二次均携带 ETag 和 Last-Modified 条件头并返回 304、正文传输为零；解析成功数为零，SQLite 全部文档版本记录及 raw 文件修改时间保持一致。manifest 仍保存第二次的观察记录。
5. 写入位置：数据库、raw 和默认测试证据均写入 tmp_path；只有设置 `DATUMOJ_EVIDENCE_DIR` 才输出证据到指定位置。全量测试使用默认临时证据目录；另一次专项运行显式设置该变量，保存本目录的 `evidence/`。没有修改任何已有测试或 fixtures。
6. 回归：全部 2015 项测试通过。

PDF 的第 25 页按已有解析器的最少文字规则标为 `needs_ocr`：文档记录共 36 页、35 个可检索页块，页码保持原始编号，不把无足够文字的页补成内容。带日期的合成网页引用使用 2025-04-01；没有结构化数据日期的内阁府网页和 PDF 使用 `no_data_date`，不拿抓取日替代。

## 验收与证据

| 命令 | 结果 | 日志 |
| --- | --- | --- |
| `make check T=tests/integration/test_gov_web_e2e.py` | 12 passed，1.81 秒 | `gov-web-e2e.log` |
| `make check T=tests` | 2015 passed，37 warnings，65.04 秒 | `tests.log` |

两次均由 Makefile 自动回退到主仓库只读 .venv（uv 在沙箱中不可用），使用当前 worktree 的 src。未安装、升级依赖或修改锁文件。全量警告为 LanceDB table_names 接口弃用，以及既有调度测试在多线程进程里 fork 的弃用提示，详情保留在日志中。`git diff --check` 通过。

`evidence/` 分别保留节/页检索、条件请求重跑、失败边界三组 commands.json 和 requests.json。回放主机为 `gov-web.test`，原始来源和许可说明见只读的 `tests/fixtures/gov_web/README.md`；合成网页仍作为虚构样本处理，不当作官方事实。

## 人工验收与限制

未访问线上种子，未重新核对实时 robots、利用规约、真实页面变化和所有站点的正文抽取质量；许可证与附件权利仍沿用来源清单的 unknown/unverified/internal_only 状态，没有扩大再分发权限。

需人工进行机器上的真实抓取、节/页引用抽查并记录验收结果；验收文档和 tag 属于后续人工流程。本次未修改 docs、契约、各开发线模块、fixtures、依赖配置或 scope 外文件。

跳过：线上 H 步骤、人工验收及 tag；未核对线上可用性和许可变化。风险：离线样本不能证明线上页面结构持续兼容，来源继续默认关闭。
