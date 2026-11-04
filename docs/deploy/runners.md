# Rikolto 运行位置与状态存储

运行位置、状态后端和 raw 后端分别配置。未设置新变量时，状态仍使用 S3；
当前部署是 GitHub Actions + Cloudflare R2。`rikolto cloud check` 检查所选后端的
条件写入能力；`rikolto cloud run` 获取写入租约，从状态存储恢复到空工作目录，
抓取、解析后发布代次。常驻机器也每次使用空工作目录。
S3 上的 cloud run 用 8 个线程传输不可变副本与 raw，代次清单和当前指针仍串行条件写入；
其他 StateManager 调用默认单线程，线程安全的存储可显式设置 `transfer_workers`。
结果 JSON 与 `publish_end` 进度行中的 `publish_timings`、`restore_timings` 只含阶段名与秒数，
其中 `total` 是完整调用耗时，`cleanup` 是临时目录清理，`post_publish` 包含发布后还原与保留观测副本的时间（其还原细项带 `post_publish_` 前缀）。

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
本任务不修改现有 Actions、launchd 配置或定时频率。

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

来源的 `scope.raw_retention_days` 必须是正整数，默认 30 天；jp-jma 为 7 天。
同一 raw 的年龄从最近一次抓取算起，跨来源采用最长保留期。
每个 URL 最新的成功快照、待解析和 staged 快照受保护；
`scope.raw_expire_latest_types` 默认空列表，jp-jma 设为 `["entry"]`，允许旧 entry 过期，feed 仍受保护。
raw 在到期前 3 天先写入 manifest 的“允许缺失”标记；不足 3 天的保留期立即标记。
只有标记满 72 小时、所有保留代次均携带标记、且达到保留期后才删除。

工作流传入两个仓库变量：`R2_CLEANUP` 默认开启，设为 `off` 暂停所有清理删除；
`R2_RAW_CLEANUP` 默认 `off`，只有设为 `on` 才真正删除 raw。
两条命令也支持 `--no-cleanup` 暂停所有清理删除。标记不受删除开关影响；
关闭期间仍报告可清理的对象数和字节数，重新开启后按相同规则清理积压数据。
结果 JSON 的 `cleanup` 只包含开关、计数、字节数、失败计数及 `over_7gb`，不输出对象键或 URL。
总量按命名空间对象列表计算，`over_7gb` 以十进制 7 GB 为阈值。

raw 不压缩：NAS 每天用 `rclone copy` 按 sha256 文件名和原始字节存档，不删除 NAS 已有文件。
云端 raw 过期后不能从云端重新解析，解析任务跳过已标记且缺失的原文；引用仍返回快照元数据，
并提示“原文已归档，云端不再保留”。需要重新解析时，应从 NAS 取回原始字节。
未标记的 raw 缺失仍按原有错误处理。
