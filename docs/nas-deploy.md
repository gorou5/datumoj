# NAS 部署说明（ReadyNAS / RAIDiator 4.2.31）

给 zenithli：这台 NAS 只做一件事——**每天定时从云端把文件拉回来存档**。全部是 NAS 主动往外拉，不用开任何对外端口，别人连不进来。云端所有采集管道统一只往同一个 R2 桶放文件，NAS 只认这一个取货点。

存档的规矩只有两条：

- 每个文件用它内容的 **SHA-256 哈希**当唯一 ID，文件名就是哈希值，不会重、不会串。
- 每个文件抓的时候记一行账：**来源 URL ↔ 哈希**。以后线上那个 URL 失效了，凭哈希来这台 NAS 上找原件。

---

## 一、先向五郎拿 3 样东西

1. R2 存储桶名（存放待归档文件的地方）
2. R2 的**只读**密钥一对（Access Key ID + Secret，只读，不能删改云端）
3. R2 的接入地址（endpoint，形如 `https://<一串编号>.r2.cloudflarestorage.com`）

密钥只填在 NAS 本机的配置文件里，不要发到聊天或邮件里。

## 二、开 SSH

1. 浏览器打开 NAS 管理页（Frontview）。
2. 在「系统 → 更新 → 安装插件」里装官方插件 **EnableRootSSH**（NETGEAR 官网可下），装完 NAS 会多一个 SSH 登录。
3. 用 SSH 登录：用户名 `root`，密码就是管理页的管理员密码。

后面所有命令都在 SSH 里敲。

## 三、建目录

```bash
mkdir -p /c/archive/r2 /c/archive/bin
```

拉回来的东西全放 `/c/archive/r2`，里面的结构跟云端保持一致，不用自己整理：

```
/c/archive/r2/
  objects/    # 文件本体，文件名 = SHA-256，按哈希前几位分了小目录
  index/      # 账本（URL ↔ 哈希对照表）
```

## 四、装 rclone（拉文件就靠它）

```bash
cd /c/archive/bin
curl -O https://downloads.rclone.org/rclone-current-linux-amd64.zip
unzip rclone-current-linux-amd64.zip
cp rclone-*/rclone /c/archive/bin/rclone
/c/archive/bin/rclone version
```

最后一行能打印出版本号就算装好了。**如果报错跑不起来，先停下，把报错原样发给五郎**，换备选办法，不要自己折腾系统。

## 五、配 R2 连接

新建文件 `/root/.config/rclone/rclone.conf`，内容如下，尖括号处换成第一步拿到的东西：

```ini
[r2arc]
type = s3
provider = Cloudflare
access_key_id = <只读 Key ID>
secret_access_key = <只读 Secret>
endpoint = <R2 接入地址>
```

测一下通不通（能列出桶里的目录就对了）：

```bash
/c/archive/bin/rclone lsd r2arc:<桶名>
```

## 六、拉取脚本 + 定时

新建 `/c/archive/bin/pull.sh`：

```bash
#!/bin/bash
LOG=/c/archive/pull.log
echo "=== $(date) ===" >> "$LOG"
/c/archive/bin/rclone copy r2arc:<桶名> /c/archive/r2 --checksum >> "$LOG" 2>&1
echo "done" >> "$LOG"
```

注意用的是 `copy`（只往回拉，不删本地）——云端就算误删，NAS 上这份也不受影响。

```bash
chmod +x /c/archive/bin/pull.sh
/c/archive/bin/pull.sh        # 先手动跑一次，确认能拉下来
cat /c/archive/pull.log       # 看日志没报错
crontab -e                    # 加下面这一行，每天凌晨 3 点自动拉
```

```
0 3 * * * /c/archive/bin/pull.sh
```

## 七、怎么找文件（以后调用就这么查）

**知道哈希**（线上数据里记着的那个 ID）——文件路径直接拼出来：

```bash
H=把哈希粘在这里
ls /c/archive/r2/objects/${H:0:2}/${H:2:2}/$H
```

**只知道原 URL**——先去账本里反查哈希：

```bash
grep "example.go.jp/那个文件名" /c/archive/r2/index/*.jsonl
```

查出来的那一行里就有它的 sha256，再按上面第一种办法定位文件。取回后想验真伪：`sha256sum 文件` 算出来的值跟文件名一致，就是原件无误。

## 八、日常维护（就 3 条）

1. **不要手动改 `/c/archive/r2/objects` 里的任何文件**，文件名就是身份证，改了就找不回来。
2. 每月瞄一眼 `/c/archive/pull.log` 最后几行，确认拉取没连续报错。
3. 在 Frontview 里打开硬盘健康告警（邮件提醒），硬盘有异常它会自己报。

有问题把 `pull.log` 最后 30 行发给五郎就行。
