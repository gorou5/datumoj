# Rikolto 运行位置与状态存储

运行位置、状态后端和 raw 后端分别配置。未设置新变量时，状态仍使用 S3；
当前部署是 GitHub Actions + Cloudflare R2。`rikolto cloud check` 检查所选后端的
条件写入能力；`rikolto cloud run` 获取写入租约，从状态存储恢复到空工作目录，
抓取、解析后发布代次。常驻机器也每次使用空工作目录。
S3 上的 cloud run 用 8 个线程传输不可变副本与 raw，代次清单和当前指针仍串行条件写入；
其他 StateManager 调用默认单线程，线程安全的存储可显式设置 `transfer_workers`。
结果 JSON 与 `publish_end` 进度行中的 `publish_timings`、`restore_timings` 只含白名单阶段名、秒数与副本传输字节计数，
其中 `total` 是完整调用耗时，`cleanup` 是临时目录清理，`post_publish` 包含发布后对象列表核对、出处抽样与保留观测副本的时间（细项带 `post_publish_` 前缀）。

`cloud run --max-minutes 40 --reserve-minutes 10` 的前 40 分钟是从本轮开始计算的
发布前工作预算（含恢复、抓取、解析与索引），另外固定预留 10 分钟给发布、周报和释放租约。
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
那一轮才记 `embedding_failed` 并让来源或本轮失败。达到上限的记录保留但不再自动重试，
可由人工检查后显式运行向量重建。旧状态没有失败计数时按 0 处理。
`vector_pending` 是本轮补嵌结束后的待补块总数，包含额度延后、暂时出错和已达重试上限的
块；它大于 0 不一定表示本轮失败。持续增加时先检查额度是否足够，再查看安全状态文件中
的 `failures` 计数；补嵌与错误不会撤销已发布的解析、关键词或其他数据。

`cloud run` 的结果新增 `source_health` 列表，包含本轮执行的来源，以及现有
`cloud_run_missing` 规则触发的缺跑来源。每项只含 `source_id`、`status`
（`ok` / `failed` / `deadline` / `missing`）、`phase`
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
成功或无失败的 deadline 都停止计数，deadline 本身不算失败，也不作为恢复成功。

Actions 将 stdout 结果同时写入日志和 `$RUNNER_TEMP/cloud-result.json`，运行及发布
步骤结束后，即使本轮返回失败，只要文件存在便调用 `scripts/source_issues.py`。
脚本通过工作流的 `GH_REPO`、`GH_TOKEN` 使用 `gh`，默认阈值为连续失败 2 轮，
可用 `--threshold N` 调整。达到阈值或 `status == "missing"` 时，查找标签
`source-broken` 且标题以 `[source-broken] <source_id>` 开头（来源 ID 后可接空格、冒号等描述分隔符）的未关闭 issue；
已有则评论，没有则创建，标签缺失时先创建标签。本轮 `status == "ok"` 时，
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
观察稳定后再另加定时；目前两个工作流都只有 `workflow_dispatch`。
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
当前代次始终保留，其他代次保留 72 小时（含边界）；只删除不被保留代次引用的副本块，
中断留下的孤立块也在下一轮清理。每次删除前重新检查租约，并使用列表中的 ETag 条件删除。
清理失败只增加结果 `cleanup.failures`，下一轮继续，不改变本轮发布结果。
清理不再调用整代恢复，也不下载任何 `copies/` 副本块、逐代 manifest 或 raw 正文；
发布后校验仍在发布路径完成。副本依赖只读代次 JSON 中的 `generation.stores`。
每轮只列一次对象库存，并读取当前指针与代次 JSON；这些小型清单的读取仍随代次数增长，
受现有存储契约限制，无法仅凭库存里的键、大小和 ETag 推导副本引用。
72/144 个保留代次的离线回归均断言副本和 raw GET 为 0、库存 LIST 为 1，且每代均可恢复。

清理使用单调时钟，规划和删除合计最多安排 600 秒。开始规划前，若距本轮
`开始时间 + max_minutes + 10 分钟解析宽限 + reserve_minutes` 不足 600 秒，直接跳过。
规划的远端阶段之间、每次删除前及租约检查后均检查截止时间；到时不再开始新的删除，
不计失败、不改变发布结果或退出码，下一轮按剩余对象重新规划。
已经开始的存储调用（含对象列表内部的分页）或本地查询会完成，因此墙钟耗时可能超过 600 秒；不强行中断条件删除。
上述截止时间来自命令声明的预算，不读取或修改平台作业时限，也不截断发布。
这替代了每轮重复恢复和下载最多数十份 manifest 的耗时；实际清理耗时见 `cleanup.elapsed_s`。

来源的 `scope.raw_retention_days` 必须是正整数，默认 30 天；jp-jma 为 3 天。
同一 raw 的年龄从最近一次抓取算起，跨来源采用最长保留期。
每个 URL 最新的成功快照、待解析和 staged 快照受保护；
`scope.raw_expire_latest_types` 默认空列表，jp-jma 设为 `["entry"]`，允许旧 entry 过期，feed 仍受保护。
raw 在到期前 3 天先写入 manifest 的“允许缺失”标记；不足 3 天的保留期立即标记。
raw 判定只读工作目录里的当前 manifest；保留策略所需快照、元数据和发布状态通过联表批量读取，
再取一次 no-parser 集合，不逐快照查询。只有当前标记满 72 小时、达到保留期、仍是保留策略候选、
且不在当前代次必需 raw 引用中才删除。其他保留代次均在最近 72 小时创建，晚于这个标记，
所以也带有允许缺失标记，恢复时不要求这份 raw；当前代次即使更早，也直接按自己的 manifest 判定。
因此无需再逐代下载 manifest 验证。
回滚恢复出的当前 manifest 若没有标记，就不会删；之后重新标记，以新时间重新等待完整 72 小时。

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

两个 Actions 作业使用相同的、固定提交哈希的 `actions/cache/restore` 和 `actions/cache/save` 步骤。
缓存键是 `rikolto-state-<OS>-<run_id>-<attempt>`，恢复时回退到同 OS 最新的键；块按 SHA256 校验，
所以不需要按代次号定键，也不需要在作业开始前读 R2，R2 密钥只给写入步骤。
保存步骤 `if: always()`：来源失败时本轮仍会发布但退出码非零，合一的 `actions/cache` 这时不会保存。
缓存只是可验证的内容来源，不决定代次选择。缓存步骤位于依赖安装和服务准备之后、云端命令之前；fork 的整个
作业仍被仓库身份条件禁止。云端使用标准 `RUNNER_TEMP` 下的
`rikolto-blocks`（无需新增项目环境变量），只存以 SHA256 命名的不可变副本字节，不缓存工作目录、环境、
连接配置、凭证或 raw。新上传的副本也进入缓存。缓存跨多代保留；Actions 的容量
淘汰会降低命中率，不影响正确性。

恢复时对本地块逐个核对大小与 SHA256，只 GET 缺少的块；部分缓存可以继续复用。
完全没有缓存时全量恢复；发现损坏时整轮副本下载回退远端，并重新校验、覆盖坏块。
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
