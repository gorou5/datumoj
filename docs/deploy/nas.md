# NAS 每小时运行包

NAS 执行抓取、解析、索引和代次发布，状态写入 NAS 的 S3 兼容服务。
需要 Docker Compose v2、足够的临时磁盘和能访问来源站点的网络；Python 3.12
与 CI 一致。镜像按 `uv.lock` 安装，运行用户为 UID/GID 10001。
此包未在真实 NAS 验证；设备到货后的集成验收由任务 904 完成。

## 1. 准备 S3 存储

用 NAS 的应用管理器或服务商的正式镜像安装 MinIO（或其他 S3 兼容实现），
将对象目录挂到持久化磁盘；配置磁盘容量告警、备份和 NAS 时间同步。
选支持 PUT `If-None-Match`、PUT `If-Match`、DELETE `If-Match` 的版本，
但最终以实际探测为准。反向代理必须原样转发这些条件头。

创建专用 bucket `your-bucket-name`、专用访问密钥，应用不使用管理员密钥。
权限仅授予该 bucket 的 `s3:ListBucket` 和其对象的 `s3:GetObject`、
`s3:PutObject`、`s3:DeleteObject`，包括配置前缀内的 `probe/` 临时对象。
不授予创建 bucket 或访问其他 bucket 的权限；本包不创建任何服务或 bucket。
若启用版本管理，配置清理过期 probe 版本与删除标记的生命周期；探测清理的是当前对象。

S3 API 和管理控制台只在局域网或 VPN 内访问，不开公网端口。
推荐受信任证书的 HTTPS；示例 `https://nas.example:9000` 只是虚构地址。
Compose 不发布任何端口，容器需能经 NAS 的内部 DNS/VPN 到达存储。

## 2. 构建并先检查

在 NAS 的仓库副本中执行（以下路径均相对于仓库）：

```sh
cp deploy/nas/compose.example.yaml deploy/nas/compose.yaml
cp deploy/nas/env.example deploy/nas/env.local
chmod 600 deploy/nas/env.local
# 在 NAS 本地编辑 env.local，替换所有 your-* 和 nas.example 占位值。
cd deploy/nas
docker compose build datumoj
docker compose run --rm datumoj cloud check
```

配置和凭证只通过 `env_file: ./env.local` 传入，不放进镜像、compose 或版本库。
复制出来的 compose.yaml 与 env.local 留在 NAS；不要提交真实配置。
NAS 的环境文件设置 `DATUMOJ_WRITER_KIND=nas`，使状态代次和写入租约明确标记 NAS 写入者。
镜像显式复制源码、来源描述及 schema，不复制本地数据；`.dockerignore` 也排除
`.env*`、data、.orch、results、.git、.venv、handoff 和实际 env 文件。

`cloud check` 只操作配置命名空间内的 `probe/<随机 UUID>`：
验证首次创建成功、重复创建拒绝、正确 ETag 更新成功、错误 ETag 更新拒绝、
错误 ETag 删除拒绝、正确 ETag 删除成功，并读取核对对象内容与删除结果。
无论成功或失败都尽力无条件删除这一个临时键，不碰租约、current 或代次键。
结果只含固定检查标签，不回显 endpoint 或密钥。
通过时 `storage_probe.supported=true`、退出码 0；不支持时退出码 1，
`reason=storage_conditional_unsupported`、`phase=storage_probe`，
`storage_probe.check` 指出失败项。网络或权限错误也拒绝运行；修复存储后重试。
没有跳过、降级或无条件写状态的选项，`cloud run` 每轮仍会重新探测。
如果 `cleanup_failed=true`，管理员检查 probe 前缀并清理残留，不能据此手动放行。

## 3. 首个代次（H3）

已有本机数据时先迁移，再启动 NAS 定时运行。不要让 NAS 对空 bucket 发布空的首代。
以下是在 Mac 的私有终端操作的入口，目录名是相对路径示例：

1. 保留本机 launchd 工作。为取得一致备份，短暂停止本机目录写入，等待正在执行的
   抓取结束，制作包含 raw、manifest、各库和运行账本的一致副本 `data/h3-snapshot`，
   随即恢复本机抓取。在副本上做后续耗时工作，保留原目录，避免长时间停抓。
2. 在不设置远端 S3 配置的本机维护环境中，对副本只读诊断，再重放到新空目录：

   ```sh
   datumoj diagnose evidence --data-dir data/h3-snapshot --verify-raw
   datumoj replay --from data/h3-snapshot --to data/h3-replayed --vectors
   datumoj diagnose evidence --data-dir data/h3-replayed --verify-raw
   datumoj diagnose storage --data-dir data/h3-replayed
   datumoj index maintain --data-dir data/h3-replayed
   ```

   先在 Mac 的 Ollama 中拉取 `bge-m3`。核对同 SHA 多资源、旧失败加新成功、
   `obs_id` 冲突、current 与应发布版本，以及重放前后的快照、块、观测值和向量覆盖。
   `replay` 必须返回 `complete`；`partial` 或未解释差异不能作为 H3 发布。
   `diagnose storage` 是体积与压缩估算，`index maintain` 做向量整理；两者不能代替比对。
3. 在维护进程中加载 NAS 的 S3 配置（私密环境文件），设置
   `DATUMOJ_RAW_BACKEND=local` 读取重放目录的本地 raw，保持 NAS 计划任务尚未启用。
   先执行 `datumoj cloud check`，再执行 `datumoj state list` 确认为空。
   `datumoj state lease acquire --holder local/h3` 取得租约，将返回的 token 仅放入当前进程环境变量
   `DATUMOJ_LEASE_TOKEN`（不要写进命令历史、日志或仓库）；在租约有效的五分钟内开始
   `datumoj state push --data-dir data/h3-replayed`。push 会维持租约并发布首个代次。
   完成后 `datumoj state lease release`，清除 token 环境变量。失败保留本机备份并检查原因，
   不覆盖已发布代次。所有写入步骤均使用已有租约，不并发执行 NAS round。
4. 只读 `datumoj pull --data-dir data/h3-verify` 核对首代校验结果及关键检索。
   NAS 的 env.local 仍用 `DATUMOJ_RAW_BACKEND=s3`，之后每轮从该代次恢复。
   H3 的具体诊断差异和首代发布需负责人复核；这一步不由示例自动执行。

## 4. 每小时第 17 分钟运行

先在 `deploy/nas` 手动运行一轮。没有 Ollama 时明确使用：

```sh
docker compose run --rm datumoj cloud run --max-embed 0
```

在 NAS 自带计划任务里设置工作目录为仓库的 `deploy/nas`，每小时第 17 分钟
（cron 表达式 `17 * * * *`），命令也用上面这一行。
基本调用形式是 `docker compose run --rm datumoj cloud run`；无 Ollama 必须加
`--max-embed 0`，因为显式命令会覆盖 compose 的默认 command。
将退出码和 JSON 输出保留在 NAS 私有任务日志，失败时检查最近成功时间。
容器每轮使用空临时目录，退出即销毁；不要绑定旧工作目录，也没有常驻循环。
重叠执行时探测使用不同临时键，已有写入租约使第二轮返回 `skipped`。
存储探测失败则在拿租约前失败退出，不发布任何状态。

## 5. 逐来源接管与回退

1. **JMA 优先**。接管期间本机 launchd 继续运行，NAS 从首代恢复后执行到期来源。
   若某来源尚未到期，等待其在 NAS 上实际成功抓取，不能用首代继承的成功时间当作 NAS 成功。
2. 逐个来源保存本机 `datumoj --data-dir data/local schedule status` 的最近成功抓取时间。
   NAS 一轮成功后，Mac 用 `datumoj pull --data-dir data/nas-status` 拉取最新代次，
   检查该目录 `runs.jsonl` 中此来源新的 `final/completed` 记录，并用
   `state-pull.json` 的 `last_successful_runs.<来源>.finished_at` 核对成功时间。
   对照 NAS round 的 `sources` 记录和代次，确认是 NAS 新跑的结果。
3. **只有 NAS 上该来源最近一次成功时间晚于本机最近一次抓取时间**，才在本机原数据目录
   执行 `datumoj --data-dir data/local schedule disable jp-jma`（其他来源替换 ID）。
   临停前再核对本机账本，若本机又抓了一次就等 NAS 更新。等待本机已启动任务结束，
   再复核时间条件；若不满足，恢复这个来源的开关并继续双跑，直到条件成立。
   launchd 的总任务保留，让其继续处理尚未接管的来源，不要一次卸载全部来源。
4. JMA 只保留约 7 天：接管前、迁移中和接管后都要检查账本中成功抓取的间隔，
   保持每小时检查；任何阶段都不能接近或超过这约 7 天的保留期。
   发现 NAS 故障立即回退，不等保留期届满；已发生的缺口必须记录，不能宣称补齐。
5. 全部来源逐个满足条件后，才可考虑本机
   `datumoj schedule launchd uninstall`。保留本机数据、来源开关清单与一致备份用于回退。

回退时先暂停 NAS 计划任务，确认容器已结束；可用时检查 `datumoj state lease status`，
避免两个远端写入者竞争，不强制抢占仍活跃的租约。
在原本机抓取环境（本地 raw、无远端 S3 写配置）恢复原数据目录，按记录逐来源
`schedule enable <来源>`、`schedule master on`；若卸载过再 `schedule launchd install`。
用 `schedule run jp-jma` 优先立即补抓并检查成功，随后检查其他来源和定时任务。
本机恢复抓取不依赖损坏的 NAS；恢复后的数据再次接管前需做差异诊断与迁移，
不能拿旧 NAS 代次覆盖本机新增数据。

## 6. 向量的两种做法

**NAS 上计算**：容量允许时，启用单独 profile 及覆盖文件：

```sh
docker compose -f compose.yaml -f compose.ollama.yaml --profile ollama up -d ollama
docker compose -f compose.yaml -f compose.ollama.yaml --profile ollama exec ollama ollama pull bge-m3
docker compose -f compose.yaml -f compose.ollama.yaml --profile ollama run --rm datumoj cloud check
docker compose -f compose.yaml -f compose.ollama.yaml --profile ollama run --rm datumoj cloud run
```

计划任务改为最后一行，仍在第 17 分钟运行。datumoj 与 ollama 共享网络命名空间，
现有嵌入地址 `localhost:11434` 可直接访问，不改代码，不向宿主机发布 Ollama 端口。
模型保存在命名卷；先拉好 `bge-m3` 再启动运行。不要在基础模式单独添加
`network_mode: service:ollama`，否则未启用 profile 时引用的服务不存在。

**Mac 上补**：NAS 持续以 `--max-embed 0` 抓取，关键词与结构化数据仍可用。
Mac 的 `datumoj pull` 副本用于查询，属于只读副本，不能直接在其中 `index` 或 `state push`。
要把向量回传，安排短维护窗口：暂停 NAS 计划、等待运行结束，在 Mac 加载远端配置，
`cloud check` 通过后执行 `datumoj state lease acquire --holder local/mac-vectors`，
取得专用租约并按 H3 方法传入 token，
用 `datumoj state restore --to data/mac-vectors --raw remote` 恢复最新代次到新目录，
在 Mac 启动 Ollama 并拉取 `bge-m3`，执行
`datumoj --data-dir data/mac-vectors index --vectors-only`，再
`datumoj state push --data-dir data/mac-vectors` 发布包含向量的新代次。
`state push` 会维持租约；`index` 仅持有本地写锁，耗时超过几分钟时需在另一私有终端
用相同 token 每分钟执行 `datumoj state lease renew`，在整个维护窗口维持远端租约。
命令之间若租约过期则重新取得租约并重新核对 current 未变化，
不要发布落后的恢复目录。完成后释放租约并立即恢复 NAS 计划，确认 JMA 下一轮成功。
全量首次向量优先在 H3 时建立，后续维护窗口控制在一次抓取间隔内；无法及时完成时
先恢复 NAS 抓取，放弃本次向量发布并下次从最新代次重试。
