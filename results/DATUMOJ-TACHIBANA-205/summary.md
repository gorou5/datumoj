# DATUMOJ-TACHIBANA-205 完成说明

已实现 `gov_web` 栏目定向采集器；只修改任务 scope 内文件，未提交 git，未修改契约、执行器、依赖、锁文件或录制夹具。

## 变更文件

| 文件 | 用途 |
| --- | --- |
| `src/datumoj/crawlers/gov_web.py` | 插件、范围校验、URL 规范化去重、每种子任务预算、HTML 链接发现、PDF 出处、SPA 分类和 304 快照复用 |
| `sources/jp-gov-web.yaml` | README 中三个已核对规约站点的栏目种子、白名单、日程、默认参数与保守许可 |
| `tests/crawlers/test_gov_web.py` | 契约合格测试、真实录制页面链接测试、离线合成站点和执行器集成测试 |
| `results/DATUMOJ-TACHIBANA-205/module.json` | 按模块描述标准记录版本、调用边界、输入输出、运行环境、权限、许可和错误处理 |
| `results/DATUMOJ-TACHIBANA-205/acceptance.log` | 指定验收命令的输出 |
| `results/DATUMOJ-TACHIBANA-205/summary.md` | 实现、逐项要求核对、验证边界和风险 |

## 实现与边界

沿用 `Crawler` / `FetchTask` / `FetchResult` / `CrawlContext` 契约；生产采集器只 import 契约、模型、标准库和已安装的 lxml。HTTP、robots、条件请求、限速、raw 和 manifest 写入均交给 IVY 执行器，没有另建执行层。HTML 仅用于发现链接和计算可见文字量，不抽取文档正文，不处理 PDF 正文。

`plan` 一次验证全部配置并清空运行状态。种子规范化后产生 `role=page`、`seed`、`depth=0` 的任务。默认前缀取种子目录，目录前缀按完整斜杠边界匹配；HTML 和 PDF 均限定在允许主机与种子目录内。HTML 最大深度默认 2；深度边界页链接的已识别 PDF 仍可下载。非 HTTP(S)、含用户信息、含糊的编码路径分隔符和显然的非 HTML/PDF 静态资源不追加。

URL 主机小写、默认端口及片段移除；相对路径、点段、百分号编码的非保留字符规范化；去掉 `utm_*` 和常见点击跟踪参数，保留业务查询参数。运行内在产出时全局去重；不同种子共享 URL 时只分配给最先发现它的种子，PDF 多处出现保留首次发现的父快照。

每种子预算包括种子、HTML、PDF 和随后失败/robots 拦截的任务，默认总计 200，避免把失败路径当作免费重试。每个任务均带 `max_pages_per_seed`、`seed_task_number`、`seed_limit_reached`；最后一个预算内任务标记上限，发现更多合规链接时每种子只记录一次 warning。上限不终止其他种子。

链接 `.pdf` 或声明 `type=application/pdf` 时直接生成 PDF 任务。无扩展名资源先作为候选页下载，`classify` 根据 PDF 字节特征（以及正文非 HTML 时的 PDF Content-Type）在 manifest 写入前更新 `role=pdf`；全部子任务都预先携带父 URL/正文 SHA256，因此不会丢出处，也不会重复发出同 URL 任务。第二轮从成功快照元数据识别已知 PDF，保留其角色。304 页面从上次成功快照的 raw 继续发现链接，父 SHA256 仍指向真实正文。

编码按内容处理：UTF BOM、UTF-8，失败后 cp932；忽略不可靠的响应 charset。支持含 XML 编码声明的政府 XHTML。HTML 去除 head/script/style/template 后的非空白可见文字少于 100 字时判 `spa_shell`。来源开关不放入 YAML，沿用 `control.json` 对未列明来源默认关闭的规则。

## 逐项核对与测试

| 要求 | 验证 |
| --- | --- |
| 1. kind、三站种子/白名单、默认参数、daily、许可、关闭状态 | YAML 通过 SourceSpec 校验，断言全部参数；新来源控制状态默认关闭；插件可发现 |
| 2. 种子及子任务、HTML 范围/深度、PDF 父子关系 | 本地服务器重放小站点，精确核对请求集合与快照 meta；PDF 字节与父页面 SHA256 均核对；无扩展名/type 提示也覆盖 |
| 3. URL 规范化去重、忽略非 HTTP(S) | 参数化规范化测试；detail-a 三种写法仅请求一次；小站点外部/非 HTTP 链接没有请求 |
| 4. 每种子上限与可见提示 | 上限 1/2/3 的执行器测试核对快照数、最后任务标记和 warning；两种子独立计数，重复链接不耗预算 |
| 5. 少于 100 字的 HTML 为 SPA | 短 HTML 和真实 jGrants 外壳均判 SPA；长正文正常；PDF 不误判 |
| 6. 离线集成、真实种子、两轮条件请求 | detail-c、前缀外、外部域名不抓；private 路径只写 blocked_by_robots 快照，未发 HTTP；第二轮四个正文请求均携带 ETag/Last-Modified 并返回 304；大阪市详情链接和总务省 cp932 链接均抽出 |
| 契约合格测试 | 继承 CrawlerSuite，5 项全部运行通过 |

验收命令：`make check T=tests/crawlers/test_gov_web.py`，结果 **47 passed**（含 5 项 CrawlerSuite 合格测试）。最终输出见 `acceptance.log`。make 按仓库约定自动使用主仓库 `.venv` 的只读解释器，无需安装或变更依赖。

HTTP 测试只使用 ReplayTransport 或 pytest-httpserver。本地集成中通过注入 transport 将测试域名映射到本地服务器，保留生产抓取器的回环地址禁令和 robots 校验；transport 显式断言目标主机，不能连接外部站点。测试构造的新文件只写入 tmp_path，已有 fixtures 只读。

## 未验证、风险和待确认

- 未向政府站点联网验证实时页面、许可或 robots 变化；来源许可维持 `unverified / internal_only`，附件权利仍需逐项核对。
- `follow_pdf=false` 可以跳过扩展名、链接 type 或历史快照已识别的 PDF；首次遇到没有类型提示的未知 URL，必须请求后才能识别为 PDF。不会额外探测 HEAD 或重复下载。
- 深度边界外的无类型提示 URL 无法预知是否 PDF，因此不会突破 HTML 深度上限去探测。目录外的 PDF 同样不跟进，以保留定向范围约束。
- 未执行跨模型家族评审或人工抽样；这些属于调度器后续门禁。任务包未包含“评审中的高置信度质疑”条目。无需修改契约或新增依赖，也没有待批准的外部操作。

跳过：JS、登录、表单、全站采集及 HTML/PDF 正文解析，均按任务不做。未核对实时网站与附件许可；来源保持默认关闭。
