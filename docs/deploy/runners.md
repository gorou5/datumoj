# Rikolto 运行位置与状态存储

运行位置、状态后端和 raw 后端分别配置。未设置新变量时，状态仍使用 S3；
当前部署是 GitHub Actions + Cloudflare R2。`rikolto cloud check` 检查所选后端的
条件写入能力；`rikolto cloud run` 获取写入租约，从状态存储恢复到空工作目录，
抓取、解析后发布代次。常驻机器也每次使用空工作目录。
S3 上的 cloud run 用 8 个线程传输不可变副本与 raw，代次清单和当前指针仍串行条件写入；
其他 StateManager 调用默认单线程，线程安全的存储可显式设置 `transfer_workers`。
结果 JSON 与 `publish_end` 进度行中的 `publish_timings`、`restore_timings` 只含白名单阶段名、秒数与副本传输字节计数，
其中 `total` 是完整调用耗时，`cleanup` 是临时目录清理，`post_publish` 包含发布后对象列表核对、出处抽样与保留观测副本的时间（细项带 `post_publish_` 前缀）。

`cloud run --max-minutes 40 --reserve-minutes 25` 的前 40 分钟是从本轮开始计算的
发布前工作预算（含恢复、抓取、解析与索引），另外预留 25 分钟给发布、清理、周报和释放租约。
清理要在 75 分钟前留足 10 分钟窗口才启动，所以发布最晚在第 65 分钟结束；余量太小时，用满预算的轮次会一直跳过清理。
每个来源的抓取上限取来源自身上限（默认 900 秒）与本轮剩余秒数的较小值；剩余不足
1 分钟就不再开始新来源。云端单次下载总时长取来源开始抓取时的剩余秒数与 300 秒的较小值（抓取期间不再更新，所以接近截止时开始的请求可能越过截止，越过的时长不超过这个值，即最多 5 分钟），包含连接、等响应和读取正文；本机默认不限。
解析与嵌入的截止时刻是整轮预算结束加 600 秒宽限，在快照或嵌入批次之间检查，当前快照完整写完，剩余快照和向量留待下轮。
作业时限应覆盖 `max_minutes + 10 分钟宽限 + reserve_minutes + 单次下载可能越过截止的时长（最多 5 分钟）+ 单个快照或嵌入批次的最长耗时`，并按实际发布耗时设置足够的 `reserve_minutes`。该参数声明发布余量，不会替部署平台修改作业时限，也不截断发布。
被 deadline 截止的来源记为未完成，下一轮 cloud run 仍会重试；每个来源、发布和整轮
结束的安全 JSON 进度行写入 stderr，新增 `crawl_end`、`parse_end` 显示来源内阶段结束的计数、耗时与截止原因，最终结果 JSON 仍写入 stdout。
来源依次执行：从没跑过的、常规到期的、仅因上次 deadline 而补入重试的；后两档按上次尝试的开始时间从旧到新，同档同时间按 source_id 排序（从没跑过的也按 source_id 排序）。
上次因 deadline 截止但已常规到期的来源仍算常规到期，来源是否到期的规则不变。

`--max-embed 1000` 是整轮提交嵌入的文本块额度：来源处理、发布恢复和补嵌共用，失败的
尝试也占额度。达到额度的块记为 `vector_limit` 待补，不算来源失败。到期来源处理结束后，
如果还剩至少 2 分钟和嵌入额度，会直接从已有文本块补嵌所有来源的积压，不重新解析；
每批最多 32 块，每批前检查剩余时间，不足 2 分钟便停止。`backfill_start` / `backfill_end`
标示补嵌阶段，结果 `backfill_embedded` 是该阶段成功提交的块数。
单篇嵌入出错先记 `embedding_deferred`，留到下一轮重试；同一篇累计连续失败达到 3 次的
那一轮才记 `embedding_failed`，来源按下述规则判定；独立补嵌仍沿用原有退出规则。达到上限的记录保留但不再自动重试，
可由人工检查后显式运行向量重建。旧状态没有失败计数时按 0 处理。
`vector_pending` 是本轮补嵌结束后的待补块总数，包含额度延后、暂时出错和已达重试上限的
块；它大于 0 不一定表示本轮失败。持续增加时先检查额度是否足够，再查看安全状态文件中
的 `failures` 计数；补嵌与错误不会撤销已发布的解析、关键词或其他数据。

`cloud run` 的结果新增 `source_health` 列表，包含本轮执行的来源，以及现有
`cloud_run_missing` 规则触发的缺跑来源。每项只含 `source_id`、`status`
（`ok` / `degraded` / `failed`，另保留调度状态 `deadline` / `missing`）、`phase`
（`crawl` / `parse_index` / `embedding` / `schedule` / null）、`categories` 类别计数、
`consecutive_failures` 和 `first_failure_at`（连续失败中最早开始时间，无则 null）。
抓取类别来自 `FetchOutcome`；解析只提取 `ParseError: <code>:` 中满足
`[a-z_]{1,40}` 的代码，其他异常只取类名，无法识别的记 `other`。
缺少解析器记 `other`，达到嵌入失败上限记 `embedding_failed`。
同时发生多阶段错误时汇总类别，`phase` 取首个失败阶段。触发缺跑告警且本轮没有失败时
`status` / `phase` 为 `missing` / `schedule`；若该来源上一次运行失败，`phase` 和类别取那次的
阶段和失败计数。补抓中的来源会超过调度周期但仍在轮转，所以距上一次开始运行（从没运行过的
按告警起算时间）不足 24 小时不算缺跑。
`control.json` 的每个有效开启来源保存 `enabled_since`（带时区的 ISO 时间），
随状态代次保存和恢复；从未运行过的来源按这个时间起算，不随新代次重置。
重复开启不重置时间；来源开关或总开关关闭后再开启，重新计算宽限期，旧运行仍保留在账本中。
旧版开关文件在下一次抓取轮次发布前补齐：已有运行记录时采用最早已知尝试时间，
没有记录时从首次观察到有效开启时起算，不推测更早的启用时间，也不伪造运行记录。
旧版只读副本在拉取新版状态前保留原有回退规则；状态查询本身不会写入开关文件。
连续失败按账本最终记录的开始时间倒序计数，忽略开始记录和重复的最终记录；
`ok`、`degraded` 或无失败的 deadline 都停止计数，deadline 本身不算失败，也不作为恢复成功。

来源判坏规则（139，替代 138 的混合阶段比例）：

- `ok`：没有单项失败，来源结果 `status=completed`、退出码 0。
- `failed`：抓取或解析阶段有异常逃到来源边界的 `except Exception`；或抓取报告中有
  `plan_error` / `after_fetch_error`（明确名单，直接视为抓取中止，成功项和旧失败抵扣均不能抵消）；
  或本轮有失败且 `new + unchanged == 0`、`parsed == 0`；或下面任一文件失败比例严格超过一半。
  来源退出码 1，计入连续失败；达到 issue 阈值才开 issue。
- `degraded`：其余存在单项失败的运行，包括恰好 50%。来源结果与 `source_health`
  都标为 degraded，退出码 0，不计连续失败，作为恢复关闭已有 issue，且计入 `last_successful_runs`。
  `failures` 和 `categories` 的数量完整保留；只因时间耗尽而没有失败时仍为 deadline，
  账本记 incomplete、不计成功运行，下一轮重试，原有截止时刻和宽限不变。

抓取与解析分别按文件计算，绝不把同一新文件的抓取和解析加进同一个分母：

- 抓取：`首次抓取失败数 / (new + unchanged + 首次抓取失败数) > 50%`；
- 解析：`首次解析失败数 / (parsed + 首次解析失败数) > 50%`。

零分母不触发比例条件，恰好一半仍为 degraded。实现等价地比较各阶段首次失败数是否大于
该阶段成功数。`http_error`、`network_error` 是单项失败，沿用比例规则，不按 `_error` 后缀判中止。
抓取按来源内 URL 的历史失败快照识别旧失败；解析按同 SHA、解析器及版本、上下文的失败记录
识别重试。旧失败不计入比例的分子或分母，仍失败照常留在结果里并继续沿用既有重试频率；
嵌入到第三次才上报的失败已经是重试，不算首次失败。没有成功的来源仍然判 failed。
例如 `new=1, parsed=1, after_fetch_error=1` 直接 failed；3 个新 URL 中 2 个死链 failed、
1 个死链 degraded；`jp-egov-data` 的 119 个新增、50 个 http_error 为 degraded。

`budget_exceeded:duration_seconds` 后续轮次可重试；同内容同上下文一轮最多尝试一次，
在快照之间沿用 deadline 检查，单篇仍受原有解析预算约束。其他 `budget_exceeded:*`
继续缓存，但后续轮次不再重复计入来源失败。为兼容现有解析调用方和 scope 外的预算测试，
`ParseSummary.failed` 保留这类缓存诊断，`cached_failures` 明确列出未重试的项，云端汇总时排除；
不改解析 CLI 的原有诊断接口或范围外测试。`ParseError: empty_content:` 表示正文不足，
首次和后续轮次均不计失败；流水线单列 `empty_content`，保留原解析记录以免反复解析。

scope 边界的最小兼容：状态发布器不在本任务允许修改的范围，它仅接受账本中
`completed`、退出码 0、`failures={}` 的最终行作为成功运行。因此云端将 degraded 的
账本行映射为该格式，用同一行的 `source_status=degraded`、`item_failures` 保存真实状态和
完整单项计数；结果 JSON 的 `status`、`failures` 不变。连续失败读取这份映射时明确停止计数。
带失败的 deadline 仍记 incomplete，不进入成功运行。没有改契约、发布器、调度频率或工作流。

旧断言兼容调整：cloud run 中“任一抓取报告/单篇解析/索引/嵌入失败就判来源 failed”的断言
改查 degraded 与保留计数；聚合类别测试剔除 empty_content；gov-web 集成测试中短正文的
parse/index 退出码改为 0，其余抓取边界、解析记录和索引内容断言保留。

139 的旧公式断言调整：`test_audit_138.py` 中积压解析成功抵消全部首次抓取失败的断言改为 failed；
`test_cloud_run.py` 中首次解析/索引全部失败的三种情形同样改为 failed，发布和账本校验保留。
抓取和解析如今各自判定，其他旧失败、deadline、缓存和连续失败断言保留。

Federal Register（139）：先重试最早未完成窗口，再继续搜索从已确认覆盖日期减 7 天（最早
2026-10-01）到本轮日期的新窗口；永久 404 或 robots 拒绝不会让计划提前结束。沿用文件数、
字节数和截止时刻上限；本轮达到上限时剩余任务仍留待下轮。详情和 XML 的历史成功快照跨窗口
共用，已成功的文档不重复请求。每个逻辑窗口仍要验证从搜索、分页到详情和 XML 的完整图；
最早未完成窗口阻止后续完整窗口推进 covered，补齐后才恢复覆盖推进，不能用发现日期替代覆盖。

回放 XML 只检查成功 outcome 及可还原的 sha，不读取 XML 正文；缺少 XML 快照、失败 outcome、
或没有可还原 sha 的 304 仍为未完成。JSON 正文按 sha 在同一次 plan 及其 after_fetch 中共用缓存，
同一 sha（包括缺失正文）最多 raw.get 一次。已有 sha 的 XML raw 是否仍存在交给既有原文生命周期
校验，不在覆盖回放中额外请求。未修改 runner、契约、快照格式或其他抓取器。

Federal Register 旧测试适配：`test_fedreg.py` 的未完成窗口用真正未抓过的新文档，304 搜索只请求
搜索页并复用既有详情/XML；`test_audit_136.py` 补登记每轮新窗口的合成响应，并允许覆盖随已完整
的新窗口推进，文档仅抓一次及失败重试次数断言保留。`test_fedreg_e2e.py` 的 block_xml 断言原样保留。

Actions 将 stdout 结果同时写入日志和 `$RUNNER_TEMP/cloud-result.json`，运行及发布
步骤结束后，即使本轮返回失败，只要文件存在便调用 `scripts/source_issues.py`。
脚本通过工作流的 `GH_REPO`、`GH_TOKEN` 使用 `gh`，默认阈值为连续失败 2 轮，
可用 `--threshold N` 调整。`status == "failed"` 且达到阈值，或 `status == "missing"` 时，查找标签
`source-broken` 且标题以 `[source-broken] <source_id>` 开头（来源 ID 后可接空格、冒号等描述分隔符）的未关闭 issue；
已有则评论，没有则创建，标签缺失时先创建标签。本轮 `status` 为 `ok` 或 `degraded` 时，
给对应 issue 留言“已恢复（第 N 代）”并关闭。标题、正文和评论只使用上述健康字段
与代次号，不包含 URL、查询参数、来源正文、异常原文或凭证。
`gh` 输出全部捕获，失败只打印脱敏计数并返回非零；issue 步骤配置
`continue-on-error: true`，不影响结果和发布。仅 cloud-run 增加 `issues: write`，
保留仓库身份判断，fork 不执行。

| 组合 | 要设置的环境变量 | 写入方标记 | 定时触发 |
| --- | --- | --- | --- |
| Actions + R2（当前） | `DATUMOJ_STATE_BACKEND=s3`（可省略）；`DATUMOJ_S3_ENDPOINT=https://account-id.example.r2.cloudflarestorage.com`、`DATUMOJ_S3_BUCKET=example-state`、`DATUMOJ_S3_PREFIX=rikolto`、`DATUMOJ_S3_REGION=auto`；凭证变量见下文 | `DATUMOJ_WRITER_KIND=actions`；未显式设置时有 `GITHUB_RUN_ID` 就回退为 actions | `cloud-run.yml` 调用 `rikolto cloud run`；目前只有手动触发，secrets 补齐并手动跑通后再加定时 |
| 某台机器 + 本地目录 | `DATUMOJ_STATE_BACKEND=dir`、`DATUMOJ_STATE_DIR=/srv/example/rikolto-state`、`DATUMOJ_DATA_DIR=/srv/example/rikolto-data`、`DATUMOJ_RAW_BACKEND=local`；状态无需 S3 凭证 | `DATUMOJ_WRITER_KIND=local`（NAS 主机本机磁盘可设 nas） | 自行配置 launchd / cron 调用 `rikolto cloud run` |
| 某台机器 + S3 兼容服务 | `DATUMOJ_STATE_BACKEND=s3`（可省略）；`DATUMOJ_S3_ENDPOINT=https://s3.example.invalid`、`DATUMOJ_S3_BUCKET=example-state`、`DATUMOJ_S3_PREFIX=rikolto`、`DATUMOJ_S3_REGION=us-east-1`；凭证变量见下文 | `DATUMOJ_WRITER_KIND=local` 或 `nas` | 自行配置 launchd / cron 调用 `rikolto cloud run` |

以上域名、目录和桶名均为虚构示例，需替换为部署配置。S3 endpoint 必须使用 HTTPS；
只有本机回环地址允许 HTTP。S3 凭证通过
`DATUMOJ_S3_ACCESS_KEY_ID` 和 `DATUMOJ_S3_SECRET_ACCESS_KEY` 注入，不写入公开仓库。
旧名 `R2_ENDPOINT`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 继续兼容；新旧名同时
出现时值必须相同。`DATUMOJ_S3_PREFIX` 默认空，region 默认 auto。

`DATUMOJ_RAW_BACKEND` 默认 local；设 s3 时 raw 使用同一组 S3 配置，即使状态后端
选 dir 也仍需要 S3 凭证。状态 dir 不使用 S3 prefix。拉取端设置相同状态配置后，
执行 `rikolto pull --data-dir /srv/example/rikolto-replica`，恢复并校验到独立数据目录。
配置必须传给定时进程；不要假定 cron / launchd 会继承交互 shell 的变量。
launchd 配置与定时频率保持原有设置。

## 目录后端的原子性与限制

- `DATUMOJ_STATE_DIR` 必须是绝对路径；解析符号链接后也不能与数据目录相同、
  互为父子。状态目录必须独立持久保存，不能放在每轮临时工作目录内。
- 仅支持 macOS / Linux 的本机磁盘。构造时及打开每层对象目录时检查 `statfs`：
  macOS 要求 MNT_LOCAL 且为 APFS/HFS/UFS/tmpfs；Linux 只接受 ext、XFS、Btrfs、
  tmpfs、ramfs、ZFS、F2FS、overlay、ReiserFS。SMB、NFS、FUSE、未知文件系统拒绝运行。
  overlay 只适用于底层也是本机磁盘的部署，管理员必须保证这一点。
- **网络挂载上的文件锁不可靠**，包括 SMB、NFS 等；即使路径看起来是本地目录也不支持。
  要用网络存储，使用 R2、MinIO、AWS S3 等 S3 兼容服务。
- key 每一段映射为 `k-<段>` 子目录，对象存在该目录的 `value` 文件中；例如
  `current.json` 对应 `k-current.json/value`。这样 `a` 与 `a/b` 能同时存在，
  内部 lock / 临时文件不会与 key 冲突。所有接口拒绝绝对 key、`..` 等路径逃逸，
  通过目录描述符和 `O_NOFOLLOW` 拒绝子目录、对象文件及锁文件的符号链接。
- 每个 key 使用永久 lock 文件和 POSIX `flock`，所有写入和条件删除均在同一把锁内
  检查版本并完成操作，跨线程、跨进程、跨实例共享。不要手动删除 lock 文件。
- 每次写入生成新的随机 UUID ETag（同内容也改变），与正文写入同一临时文件，
  文件 fsync 后 `os.replace`，再 fsync 目录；删除也同步目录。读者只会读到完整版本。
  ETag 只是比较令牌，调用方必须原样传回；禁止把它当内容摘要。
- 条件失败抛 `PreconditionFailed`；磁盘、权限、损坏、同步等错误原样失败。
  replace 后 fsync 失败时写入可能已经可见，调用方须重新读取，不能当作条件失败。
- 状态根目录及父目录必须由可信本机用户管理，不能被其他程序改名、替换或挂载。
  本后端不是抵御同权限恶意本机进程的隔离边界。不要手动改写对象文件；备份时需
  停止写入或使用文件系统快照。目录锁文件和空目录会保留，崩溃可能留下临时文件；
  它们不会出现在对象列表中。对象大小和文件名长度受本机文件系统限制。

部署前运行 `rikolto cloud check`。此探测只验证条件写入语义，不证明底层设备在断电、
硬件故障或网络重挂载时的耐久性；目录后端不能替代独立备份。

## 独立补嵌作业

`cloud-run.yml` 负责抓取到期来源、解析、有限额嵌入及发布；`cloud-embed.yml` 专门
补嵌所有来源的已有文本块，不抓取、不解析。建议抓取作业结束后手动触发嵌入作业，
观察稳定后再另加定时；`cloud-embed` 目前只有 `workflow_dispatch`。`cloud-run` 从 `main` 每 3 小时定时运行（GitHub 只认默认分支上的定时）。
两者共用 `rikolto-cloud-run` concurrency group（不取消正在运行的作业），也使用同一
状态存储的写入租约，包括续期、过期接管与释放，避免跨作业或本机写入方同时发布。

嵌入作业执行 `rikolto cloud embed --max-minutes 100 --reserve-minutes 15`，Actions 时限
150 分钟。每批最多 32 块，每批前检查预算，剩余不足 1 分钟便停下并发布已成功的工作。
可选 `--max-embed` 限制整轮尝试块数（失败尝试也占额度），默认不限；重试与失败上限
沿用 cloud run。结果 `embedded` 是成功块数，`vector_pending` 是补嵌后积压块数。
没有成功嵌入且没有清理已关闭来源的待补记录时不发布新代次，`generation` 为 null；因此全部失败且未发布时，本轮新增的
重试计数只保留在本轮工作目录，不会同步到远端。进度行和结果只输出安全计数、固定阶段
与耗时，不输出来源正文或异常详情。此工作流无需 `ESTAT_APP_ID`。

来源配置的 `scope.embed` 是可选布尔值，缺省为 `true`。设为 `false` 时，解析、分块、
关键词索引、观测值和发布照常，但不写向量、不记待补、不消耗嵌入额度，两个云端作业
也会跳过其补嵌。加载向量状态时会清理能确认属于该来源的待补记录；无法确认来源的记录保留。
已有向量保留，因此部分旧条文仍能被向量检索命中，其余只能用关键词检索。
目前关闭的是 `jp-egov-law`（量大）和 `jp-jma`（每篇只有一个超长文本块，在 Actions CPU 上嵌入会反复超时）。

重新打开时将 `scope.embed` 设为 `true` 或删去该键，再执行 `rikolto parse <source_id>`
解析新快照，或执行 `rikolto index --rebuild` 从已有文本块重建。新解析或重建都会尝试嵌入，
超出额度或失败的文档重新进入待补队列，再由云端作业补嵌。
`rikolto index --vectors-only` 只处理已有待补记录，不能单独找回关闭期间清掉的记录。

### R2 保留期与清理开关

`cloud run` 和 `cloud embed` 仅在发布及发布后校验成功、仍持有写入租约时清理。
当前代次始终保留，其他代次保留 24 小时（含边界）；只删除不被保留代次引用的副本块，
中断留下的孤立块也在下一轮清理。删除顺序为代次清单、副本块、raw。
S3/R2 使用 DeleteObjects 串行批量删除，每批最多 200 个键，每批前重新检查一次租约。
每批只包含本轮删除计划列出的代次清单、`copies/sha256/` 或 `raw/` 键，
整批先校验键，含当前指针、租约或其他前缀就拒绝整批删除。
这些不可变对象依靠租约保障清理边界，R2 的 DELETE 条件不用于协调。
批量方法通过 S3 存储的可选能力接入，其他存储按库存 ETag 逐个条件删除，每个对象前检查租约。
逐项读取响应的 `Errors`，只将 `Deleted` 明确确认且未报错的对象计入删除数和字节数，
未确认删除的对象计入 `cleanup.failures`，立即停止后续批次，下一轮重新规划。
代次清单批次有任何失败，就停止副本块和 raw 删除，保全剩余清单的依赖。
清理失败不改变本轮发布结果或退出码。
清理不调用整代恢复，也不下载 raw 正文；没有孤儿 raw 候选时不下载副本块或逐代 manifest。
存在候选时仅读取保留代次的 manifest，复用恢复路径的分块下载、哈希/大小校验和缓存；
不还原其他库，这些额外读取的耗时计入 `cleanup.elapsed_s`。
发布后校验仍在发布路径完成。副本依赖只读代次 JSON 中的 `generation.stores`。
每轮只列一次对象库存，并读取当前指针、代次 JSON 和固定 NAS 回执。代次 JSON 的读取仍随代次数增长，
受现有存储契约限制，无法仅凭库存里的键、大小和 ETag 推导副本引用。
72/144 个保留代次的离线回归均断言副本和 raw GET 为 0、库存 LIST 为 1，且每代均可恢复。

清理使用单调时钟，规划和删除合计最多安排 600 秒。开始规划前，若距本轮
`开始时间 + max_minutes + 10 分钟解析宽限 + reserve_minutes` 不足 600 秒，直接跳过。
规划的远端阶段之间、每批删除的租约检查前后均检查截止时间，到时停止安排新的批次，
不计失败、不改变发布结果或退出码，下一轮按剩余对象重新规划。
已经开始的存储调用（含对象列表内部的分页）或本地查询会完成，因此墙钟耗时可能超过 600 秒。正在执行的批次会完成并计数。
上述截止时间来自命令声明的预算，不读取或修改平台作业时限，也不截断发布。
这替代了每轮重复恢复和下载最多数十份 manifest 的耗时；实际清理耗时见 `cleanup.elapsed_s`。

所有来源的 `scope.raw_retention_days` 统一为 2。raw 最多 48 小时，NAS 存档确认后可提前删除，最新快照受保护。同一哈希须等所有引用的年龄期限到期；来源的 `raw_retention_days` 生效，48 小时是上限。
JMA 使用独立的 `scope.feed_lookback_days: 7`，缺省为 7。按 feed 自身 `updated` 减去回看天数截断，包含截止点，不使用本机时钟。
entry 时间缺失、无法解析或没有时区时照常抓取，feed 时间无效时不做截止。已保存的 entry id 继续去重，limit 只计实际调度的条目。
每个 URL 最新的成功快照、待发布、staged 和 failed 快照受保护。
`scope.raw_expire_latest_types` 默认空列表，jp-jma 保持 `["entry"]`，feed 仍受保护。

发布前 `mark_raw` 在 raw 首次进入代次时就写入 manifest 的“允许缺失”标记，已有时间戳保持原值。
标记也覆盖当前受保护的 raw，以便它将来成为历史快照时可过期；还原时只豁免所选代次 manifest 中不受保护的 raw。
还原仍检查该代次最新成功、待发布、staged 和 failed 快照的 raw 是否存在且大小一致。
保留旧代次不代表保留它引用的全部 raw：较新代次可能已按回执或年龄规则删除旧代次中仍受保护的原文。
还原非 `current.json` 指向的旧代次时，缺少受保护 raw 会报固定原因 `rollback_raw_expired`，不交出恢复目录；
当前代次缺 raw 仍报“远端缺少 manifest 引用的 raw”，大小或哈希不符仍按原规则报错。
本规则不为回滚额外保留 raw；回滚前需要另行确认原文仍可用。

`capacity.allowed`、`tier` 和 `reason` 表示轮开始时的准入判断，不表示发布是否成功。
轮内越线停抓用 `capacity.mid_round_stop: true`、`skipped_sources` 和 `next_round: cleanup_only` 表示，
已完成来源仍照常发布；本轮发布结果以 `status` 和 `generation` 为准。

`raw_marked` 延续原有计数边界，只计本轮新标记且具备删除资格的哈希，保护中的哈希只补回滚元数据。
`state.py` 的 `RETENTION_WINDOW = timedelta(hours=24)` 继续控制旧代次保留和按年龄清理的标记保护。
常规 raw 到期清理只读当前 manifest、代次描述和固定回执，不下载逐代 manifest 或 raw 正文。
删除需要通过最新/发布状态保护、当前 manifest 必需引用检查，且标记时间不得晚于任何保留的非当前代次。
无有效回执时，raw 到达 min(来源 `raw_retention_days`, 48 小时) 且标记满 24 小时才可删除。
有效回执允许在标记未满 24 小时时提前删除，但必须通过相同的保留代次时间检查。
旧写入器或回滚留下的未标记代次早于新标记时，raw 会继续保留到这些代次退休。

孤儿 raw 删除必须等 DATUMOJ-KATABAMI-113 合并并部署后才可考虑启用；
`R2_RAW_CLEANUP=on` 和 `R2_ORPHAN_RAW_CLEANUP=on` 必须同时显式设置，缺省均关闭，
总清理开关和命令的预览/删除模式仍生效。本任务不打开任何开关。
只有当前及全部保留代次的 manifest 都不引用的标准 raw 键才算孤儿；
引用包含 snapshots 的 sha256 与 raw_allowed_missing（包括仅有标记的哈希）。
从旧代次恢复再发布后，另一保留代次独有的 raw 仍受此规则保护，直到该代次退出保留期。
任何保留代次 manifest 缺块、不可读或校验失败，整类孤儿 raw 跳过，
`orphan_raw_skip_reason=retained_refs_unavailable`；其他类别继续按原规则处理。
高于当前指针的孤儿代次仍使整类跳过，原因码为 `orphan_generation`。
非标准 raw 键不删除；清理报告和容量盘点仅在非零时输出 `unrecognized_raw_keys`，
兼容既有报告消费者。上述保护不改变 NAS 回执或年龄到期的常规 raw 删除规则。

NAS 在 rclone 成功拉取后，由人配置运行 `rikolto nas receipt /local/archive/raw`。
命令按 `raw/<前两位>/<sha256>` 布局流式校验本地文件，内容哈希与文件名相符才收录，跳过符号链接和不符布局的条目。
缺失或不可读的目录会使命令失败，上传前失败保留已有回执。
空目录或没有合格文件时不上传、不覆盖旧回执，非零退出并返回固定原因 `no_qualified_raw_files`。标准输出只含 `count` 和 `archived_at`，不打印哈希列表。读取失败记为 `unreadable`，内容不合格记为 `invalid`。

命令通过现有状态存储接口只向相对键 `receipts/nas-raw.json` 写一次，不读取远端键。
版本 1 回执只有 `version`、`archived_at` 和 `sha256` 三个字段。`archived_at` 为 UTC ISO 时间，`sha256` 为小写 64 位十六进制列表。
格式、额外字段、哈希和时间严格校验，未来时间也拒收。超过 48 小时为 stale，恰好 48 小时仍有效。
缺失、无效、读取失败或过期回执均退回按年龄清理。
`cloud usage` 使用与清理相同的回执读取和有效性判断，将回执传入共用删除规划，
因此 `reclaimable.raw` 和 `reclaimable_by_source` 与同一库存、时刻的清理预览一致，输出不新增字段。
raw 到期时间 = min(抓取时间 + 来源 `raw_retention_days`, 抓取时间 + 48 小时, 有效回执存档时间)。配置 1 天时 24 小时到期；共享哈希须等所有引用的年龄期限到期，再与回执时间取较早值。
回执不携带本地路径、主机名或账号，命令错误输出沿用状态命令的脱敏边界。

云端周报上传前按 UTF-8 字节数检查 `capacity.WEEKLY_REPORT_RESERVE`（1,000,000 字节）。
超限时只保留能放下的完整行，结尾注明截断及省略的事件条数；正常长度正文保持不变。
周报仍作为非必要写入经过容量闸门；轮内标记 `mid_round_freeze` 或 `mid_round_stop` 时，
嵌入补做和周报都跳过。

清理报告新增 `receipt_status`（ok、missing、invalid、unreadable、stale）、`raw_deleted_by_receipt`、
`raw_deleted_by_age` 以及各自的 `_bytes` 字段。达到来源年龄期限（上限 48 小时）的确认删除计入 age，其余提前删除计入 receipt。
两类之和等于原有 raw 删除总计，只计存储明确确认的删除。
本任务的离线报告样例、回执样例和逐来源保留期对照保存在 `results/DATUMOJ-KATABAMI-106/`。

KATABAMI-106 的版本 1 回执样例（合成内容哈希）如下。

```json
{"version":1,"archived_at":"2026-11-12T00:00:00+00:00","sha256":["2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"]}
```

假存储报告使用 49 小时 raw（14 B）、1 小时 raw（15 B）和受保护最新快照，确认删除结果如下。

| receipt_status | raw_deleted | raw_deleted_by_age | age 字节 | raw_deleted_by_receipt | receipt 字节 | failures |
|---|---|---|---|---|---|---|
| ok | 2 | 1 | 14 | 1 | 15 | 0 |
| missing | 1 | 1 | 14 | 0 | 0 | 0 |
| invalid | 1 | 1 | 14 | 0 | 0 | 0 |
| stale | 1 | 1 | 14 | 0 | 0 | 0 |

四种情形均保留最新快照。逐来源对照为 jp-jma 从 1 天改为 2 天，其余 16 个来源从 30 天改为 2 天。

| KATABAMI-106 检查 | 结果 |
|---|---|
| 任务指定 audit 131、134、141、katabami 101、102、104、受保护 audit 117 和 143 | 90 passed |
| acceptance.automated 第一条定向命令 | 84 passed |
| 状态存储、CLI、容量、cloud run/embed/cleanup 等调用方现有文件 | 346 passed |
| make lint | All checks passed |

全量测试按任务运行限制交由调度器执行，真实 R2/NAS 操作和人类验收尚待完成。
证据目录受 gitignore 忽略，沙箱禁止更新 worktree 的外部 Git 索引，完整日志及样例保存在任务工作树。

工作流传入两个仓库变量：`R2_CLEANUP` 默认开启，设为 `off` 暂停所有清理删除；
`R2_RAW_CLEANUP` 默认 `off`，只有设为 `on` 才真正删除 raw。
两条命令也支持 `--no-cleanup` 暂停所有清理删除。标记不受删除开关影响；
关闭期间仍报告可清理的对象数和字节数，重新开启后按相同规则清理积压数据。
结果 JSON 的 `cleanup` 只包含开关、计数、字节数、失败计数、`over_7gb`、`elapsed_s` 和 `timed_out`，
不输出对象键或 URL；发布后的进度行增加 `cleanup_end` 事件，字段白名单沿用原样。
总量按命名空间对象列表计算，`over_7gb` 以十进制 7 GB 为阈值。
因剩余时间不足而跳过时不读库存，总量与待清理计数保持初始化的 0，并用 `timed_out` 表明未完成规划。

测试兼容调整：audit 131 中直接依赖逐代 manifest 下载和清理内恢复的两条测试改查新标记宽限与不重复恢复；
audit 122 / cloud embed 的事件序列增加 `cleanup_end`，cloud run 的计数字段断言单独容许新增的浮点 `elapsed_s`。
audit 135 的批量测试使用计数假 S3 客户端。cloud run 的删除失败注入入口由逐个删除改为批量方法，其余断言保留。

raw 不压缩：NAS 每天用 `rclone copy` 按 sha256 文件名和原始字节存档，不删除 NAS 已有文件。
云端 raw 过期后不能从云端重新解析，解析任务跳过已标记且缺失的原文；引用仍返回快照元数据，
并提示“原文已归档，云端不再保留”。需要重新解析时，应从 NAS 取回原始字节。
未标记的 raw 缺失仍按原有错误处理。

## 132：按增量传输与离线测量

云端解析将一批最多 500 篇的向量写入合并提交；通常新增 1,000 篇只产生两次
Lance 提交，避免每篇都写一份列出全部碎片的版本清单。嵌入仍按原来的块额度和
截止时刻检查。提交前持久保存整篇待补记录；成功后只确认该批次真正提交的文档。
共享提交失败、进程退出、其他来源成功、零额度、deadline、exclude 和重试上限
均不会清掉尚未提交的记录。替换先写入新向量，再删除过期块；写入失败保留旧向量。

自动维护只有当前碎片数达到 10,000，或距上次压实满 7 天才调用 optimize；
`vectors/_maintenance.json` 按表记录时间并随副本发布。缺失、损坏或未来时间按
未知历史处理，从当前时间起算。正常轮次只增加数据文件与版本清单。
显式传入保留期的手动维护仍可强制压实；环境变量 `DATUMOJ_VECTOR_RETAIN_HOURS`
也保留原有显式维护行为。压实和版本清理仍使用 Lance 自带的在途文件保护。

目录跳过内容相同的数据集；搜索字段未变时，元数据更新也跳过 FTS 重建，资源仅在 JSON 改变时更新；文本块按
主键更新变化的块、删除本版本中消失的块，避免重写整篇；manifest 的发布状态与
parse_writers 使用局部更新。`parsed` 的 REPLACE 保留：parse_stats 依靠 rowid
表达跨解析器的最后写入顺序，直接换成 UPSERT 会改变查询结果。

两个 Actions 作业不再使用 Actions 缓存保存或恢复状态块。状态块可能包含
`no_outflow` 来源的正文（包括 chunks 和关键词索引中的文本），而公开仓库的
fork PR 可以恢复目标分支缓存，因此状态块不能进入 Actions 缓存。
嵌入模型的 `~/.ollama/models` 缓存继续保留。

每轮在新 runner 上从 R2 下载所选代次的状态副本，逐块核对大小与 SHA256，
再重建关键词索引。恢复耗时由约 180 秒增至约 310 秒，可接受。
核心恢复逻辑仍支持标准 `RUNNER_TEMP` 下的 `rikolto-blocks` 临时目录，
目录不存在时自动下载全部所需块，并在本轮内写入可验证副本。该目录只用于
本轮临时复用，不通过 Actions 缓存跨轮保存。离线回归测试覆盖目录缺失时的
远端下载及校验。已有目录中的损坏块仍回退远端下载并重新校验。
整库 SHA256、schema、向量覆盖和出处校验继续执行。`restore_timings` 增加
`downloaded_bytes` / `reused_bytes`（副本正文的字节计数，不含清单和列表响应），
云端输出仅允许固定阶段名及有限非负数字。旧的计时值类型保持为浮点数。

云端发布使用 StateManager 内保留的一致性快照，发布后列举 copies 和 raw，逐一
核对对象 key 与大小，再沿用 `_sample_provenance` 抽样还原出处。不再 GET 全部
副本或 raw；上传前的 SHA256 校验保留。列表无法发现同大小的远端内容篡改；需要
完整哈希校验时运行 `uv run --frozen rikolto state restore --to <新空目录> --raw local`
（可加 `--generation N`）。本机 state push、pull、回滚仍沿用完整恢复边界。

云端观测代次由已冻结并已检查远端清单的副本保留，`retain_generation` 仅在
`verified=True, remote_raw=True` 时允许 raw 留在远端，并在保留记录中标明。
本机 pull 的默认路径仍必须保留并验证本地 raw。由于 wiring.py 不在本任务 scope，
云端显式调用 StateManager 的发布入口，以绕开 wiring 中原有的全量恢复钩子；
未修改 wiring 或其他任务的实现。两代云端运行、pull 和 MCP query_sql 的现有
集成测试原样通过。

可复现测量：`make check T='tests/regressions/test_audit_132.py -s'`。全部为合成
数据、FakeEmbedder 和假对象存储，无网络。SQLite 用 backup() 包含 WAL；数字是
新增内容哈希对应的副本正文实际字节，不把整库校验描述重复算作上传。
改前使用本任务基线 f7e665e 的四个实现运行同一辅助函数；向量已有库逐篇 add，
真实 parse_pending 新增 200 篇。SQLite 重复提交已有目录并新增 20 篇。

| 库 | 本轮新增数据量 | 已有 1,000 篇：改前 → 改后新增块字节 | 已有 2,000 篇：改前 → 改后新增块字节 |
| --- | --- | ---: | ---: |
| 向量 | 200 篇单块，64 维 float32 共 51,200 B | 62,072,901 → 171,217 | 117,848,953 → 263,218 |
| 目录 | 20 数据集，新增 JSON 共 77,060 B | 4,272,128 → 1,896,448 | 15,835,136 → 1,978,368 |
| 文本块 | 20 篇单块，新增正文共 240,280 B | 3,129,344 → 3,129,344 | 2,801,664 → 2,801,664 |
| manifest | 20 个新快照，对应上述 240,280 B 正文 | 942,080 → 942,080 | 1,785,856 → 1,785,856 |

维护时间 JSON 的小数位可能使向量测量相差几字节。向量已有碎片确为 1,000 / 2,000，追加后为 1,001 / 2,001，当前实现新增字节均
低于已有文件总字节的 1%；未压实。版本清单仍有每个已有碎片的固定描述开销，
所以碎片加倍时本轮有约 92 KB 的差异；合并提交消除了每篇重复这份清单的放大。

文本块与 manifest 的本例仅插入新版本，局部 UPSERT 不会减少这些必需插入。
256 KiB 块覆盖多个 4 KiB B 树页面：chunks 的文档/块/当前版本索引，以及 manifest
按 SHA、URL、snapshot_id 的索引散落写入，页面分裂和目录 FTS 段合并也会改变块。
特别是小 manifest，本例新增 20 行仍可能改变几乎每个块，尚未达到严格与存量无关的
目标。没有按来源拆库或改变既有块格式；这些残余及上述数字供后续主对话决策。
进一步用 SQLite dbstat 对 1,000 + 20 篇的备份逐个 4 KiB 页对比：目录只改变
16 个旧页并新增 48 页；文本块只改变 22 个旧页并新增 69 页；manifest 只改变
35 个旧页并新增 5 页。manifest 的旧页中 16 页属于 snapshots_sha，10 页属于
snapshot_id 唯一索引；文本块改动分布在 chunks_chunk_id 与各主键索引。
这证明行写入已经局部化，剩余传输放大主要来自大块覆盖分散的小页。
manifest 的 schema 初始化迁移、parsed 最后写入顺序也是检查过且需保留的写入；
范围外的 keyword FTS 在恢复时重建，不上传；未发现必须越界修复的其他写入。

以下为历史缓存复用方案的离线对照，当前 Actions 每轮恢复按全量下载计算。
实际 StateManager + 假存储的两代传输测量（只保留上一代块缓存；关键词、raw 和观测接口用契约假实现，发布 raw 字节写入合成目录）：

| 已有 / 新增篇数 | 新上传 B（含代次清单和 raw） | 下载副本 B | 复用副本 B |
| --- | ---: | ---: | ---: |
| 400 / 10 | 3,088,472 | 3,018,752 | 1,634,304 |
| 800 / 10 | 3,514,529 | 3,440,640 | 5,566,464 |
| 400 / 20 | 3,255,002 | 3,121,152 | 1,634,304 |

存量加倍、增量相同时传输增加约 14%，并非整库加倍；增量翻倍仍受 256 KiB 块的
固定开销影响。回归分别约束每库字节、真实碎片数、上传/下载及损坏回退，保留上述
未达目标的 manifest 边界，避免只报告一个整体数掩盖问题。

已有测试只调整 tests/test_cloud_run.py 中依赖“发布后全量下载”的两个断言：
改为 inventory/sample/retain 耗时，并在模拟列表延迟时断言不 GET raw 或 copies。
其他原有断言保持不变；新增失败提交隔离、缓存、列表校验、低频压实和工作流位置测试。
未在生产 R2 或 GitHub Actions 上运行；未写凭证、未触碰生产数据、未新增依赖。

132 最终离线验收：state 45、cloud run 78、workflow 14、regressions 1,221 条通过；
`make lint` 0 条；`make check T=tests` 完整运行 401.06 秒，5,235 条通过。
全量保留现有 fork 测试的 1 条 DeprecationWarning，无失败或跳过。

### DATUMOJ-MAPLE-140 离线验收

状态块缓存撤除后的四条验收命令均正常结束。

| 命令 | 结果 |
| --- | --- |
| `make check T=tests/test_cloud_workflow.py` | 16 passed |
| `make check T=tests/regressions/test_audit_140.py` | 25 passed |
| `make lint` | All checks passed，0 条问题 |
| `make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"` | 5481 passed，1 deselected，1 warning，417.64 秒 |

额外在外层设置 `DATUMOJ_RAW_BACKEND=s3` 运行两个 e2e 用例，2 项通过。
全量的唯一警告来自既有 schedule 锁测试的 multiprocessing fork 弃用提示。
完整日志和变更清单位于 `results/DATUMOJ-MAPLE-140/`，本机路径已脱敏。
该目录受仓库忽略规则影响，执行沙箱也禁止写入 worktree 的 Git 索引，
因此证据文件目前仅保存在工作目录中，需调度器显式收集或纳入提交。
本节同步保留验收摘要，避免证据目录被默认忽略时丢失关键结果。
真实 R2、Actions 和 NAS 部署留待部署验收，异模型审查与人工抽验由调度器安排。
