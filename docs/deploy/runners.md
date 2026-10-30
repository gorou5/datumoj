# Rikolto 运行位置与状态存储

运行位置、状态后端和 raw 后端分别配置。未设置新变量时，状态仍使用 S3；
当前部署是 GitHub Actions + Cloudflare R2。`rikolto cloud check` 检查所选后端的
条件写入能力；`rikolto cloud run` 获取写入租约，从状态存储恢复到空工作目录，
抓取、解析后发布代次。常驻机器也每次使用空工作目录。

| 组合 | 要设置的环境变量 | 写入方标记 | 定时触发 |
| --- | --- | --- | --- |
| Actions + R2（当前） | `DATUMOJ_STATE_BACKEND=s3`（可省略）；`DATUMOJ_S3_ENDPOINT=https://account-id.example.r2.cloudflarestorage.com`、`DATUMOJ_S3_BUCKET=example-state`、`DATUMOJ_S3_PREFIX=rikolto`、`DATUMOJ_S3_REGION=auto`；凭证变量见下文 | `DATUMOJ_WRITER_KIND=actions`；未显式设置时有 `GITHUB_RUN_ID` 就回退为 actions | `cloud-run.yml` 调用 `rikolto cloud run`；目前只有手动触发，secrets 补齐并手动跑通后再加定时 |
| 某台机器 + 本地目录 | `DATUMOJ_STATE_BACKEND=dir`、`DATUMOJ_STATE_DIR=/srv/example/rikolto-state`、`DATUMOJ_DATA_DIR=/srv/example/rikolto-data`、`DATUMOJ_RAW_BACKEND=local`；状态无需 S3 凭证 | `DATUMOJ_WRITER_KIND=local`（NAS 主机本机磁盘可设 nas） | 自行配置 launchd / cron 调用 `rikolto cloud run` |
| 某台机器 + S3 兼容服务 | `DATUMOJ_STATE_BACKEND=s3`（可省略）；`DATUMOJ_S3_ENDPOINT=https://s3.example.invalid`、`DATUMOJ_S3_BUCKET=example-state`、`DATUMOJ_S3_PREFIX=rikolto`、`DATUMOJ_S3_REGION=us-east-1`；凭证变量见下文 | `DATUMOJ_WRITER_KIND=local` 或 `nas` | 自行配置 launchd / cron 调用 `rikolto cloud run` |

以上域名、目录和桶名均为虚构示例，需替换为部署配置。S3 凭证通过
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
