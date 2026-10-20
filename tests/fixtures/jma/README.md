# JMA 防灾信息 XML 测试样本

供 DATUMOJ-IVY-203（JMA 长期 feed 采集器与解析器）离线测试使用。除标注 `.synthetic` 的文件外，均为原始响应字节，未格式化、未重新编码。

## 许可与署名

- 出典：気象庁ホームページ（https://www.jma.go.jp/）、気象庁防災情報XMLフォーマット形式電文（PULL型）https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml 及其链接的电文。
- 按气象厅网站利用规约（https://www.jma.go.jp/jma/kishou/info/coment.html ，与 CC BY 4.0 兼容）再分发。本目录对数据未做加工；`.synthetic` 文件为本项目人工构造，不是气象厅数据。
- 录制工具 User-Agent：`DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，限速每秒 ≤1 请求，共 7 个请求。

## 数据保留期说明

长期 feed（`*_l.xml`）每小时更新一次，FAQ 称约保留 7 天，但官方明确不保证。实测 `regular_l.xml` 覆盖 2026-10-13T12:01Z 至 2026-10-20T12:50Z，条目数固定为 9383 条（滑动窗口：新进 N 条就挤出最旧 N 条）。电文 URL 在 feed 中滚出后可能失效，**本目录的 URL 不保证日后仍可访问**，测试不得联网。

## 文件清单

抓取时间均为 JST（+09:00）。

| 文件名 | 请求 URL | HTTP 状态 | 抓取时间 | 大小（字节） | 用途 |
|---|---|---|---|---|---|
| `regular_l.snapshot1.xml` | https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml | 200 | 2026-10-20T21:00:01+09:00 | 4586489 | 长期 feed 第 1 次快照（feed `updated` 2026-10-20T20:51:31+09:00，Last-Modified 11:51:50 GMT） |
| `regular_l.snapshot2.xml` | https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml | 200 | 2026-10-20T21:53:16+09:00 | 4586489 | 长期 feed 第 2 次快照（feed `updated` 2026-10-20T21:51:29+09:00，Last-Modified 12:51:46 GMT），测去重/增量 |
| `20261020085325_0_VPFD51_240000.xml` | https://www.data.jma.go.jp/developer/xml/data/20261020085325_0_VPFD51_240000.xml | 200 | 2026-10-20T21:00:22+09:00 | 26721 | 府県天気予報（Ｒ１），三重県；有 `TargetDuration` |
| `20261020112204_0_VPWP50_050000.xml` | https://www.data.jma.go.jp/developer/xml/data/20261020112204_0_VPWP50_050000.xml | 200 | 2026-10-20T21:00:23+09:00 | 686457 | 気象警報・注意報時系列情報（Ｒ０６），秋田県；警报类，体积较大；**无 `TargetDuration`、无 `ValidDateTime`** |
| `20261020074609_0_VPFG50_460100.xml` | https://www.data.jma.go.jp/developer/xml/data/20261020074609_0_VPFG50_460100.xml | 200 | 2026-10-20T21:00:25+09:00 | 2201 | 府県天気概況，鹿児島；**缺可选字段样本**：无 `TargetDuration`、无 `ValidDateTime`，`<EventID/>`、`<Serial/>`、`<Headline><Text/></Headline>` 为空元素 |
| `20261015051031_0_VPCK50_471000.xml` | https://www.data.jma.go.jp/developer/xml/data/20261015051031_0_VPCK50_471000.xml | 200 | 2026-10-20T21:00:26+09:00 | 11108 | 地方１か月予報，沖縄地方；季节预报类，有 `TargetDuration` |
| `jma-osaka-forecast-20261015.xml` | （未请求；从旧项目复制） | — | 2026-10-20T21:00 复制（原文件 mtime 2026-10-15 18:58 JST） | 14153 | 府県天気予報（Ｒ１），大阪府。原样复制自 `digital-assets/repo/plugin-prototype/test/fixtures/jma-osaka-forecast-20261015.xml`；对应 feed 条目 `20261015073247_0_VPFD51_270000`（该 id 在 snapshot1/2 中都有） |
| `entry-doctype-entity.synthetic.xml` | （人工构造） | — | 2026-10-20 | 1373 | **合成恶意样本**：含 `<!DOCTYPE … [<!ENTITY …>]>`，有嵌套实体膨胀（lol3 → 1000 倍）和外部实体 `SYSTEM "file:///etc/passwd"`。解析器必须拒绝 DTD/实体，不得展开 |
| `SHA256SUMS` | — | — | — | — | 本目录各文件的 SHA-256 |

另：本次还下载了 1 个 `VPWS50`（気象警報・注意報（Ｒ０６）（集約通報），1600252 字节），因目录 10MB 上限未收录，不计入上表（计入请求数）。

## 两次 feed 快照的差异（entry id）

两次各 9383 条，id 全部唯一；**重叠 9377 条，新增 6 条，滚出 6 条**。

snapshot2 新增（都在 feed 顶部）：

- `https://www.data.jma.go.jp/developer/xml/data/20261020125057_0_VPWS50_010000.xml`
- `https://www.data.jma.go.jp/developer/xml/data/20261020124058_0_VPWS50_010000.xml`
- `https://www.data.jma.go.jp/developer/xml/data/20261020123052_0_VPWS50_010000.xml`
- `https://www.data.jma.go.jp/developer/xml/data/20261020122055_0_VPWS50_010000.xml`
- `https://www.data.jma.go.jp/developer/xml/data/20261020121101_0_VPWS50_010000.xml`
- `https://www.data.jma.go.jp/developer/xml/data/20261020120128_0_VPWS50_010000.xml`

snapshot1 中有、snapshot2 中滚出（最旧的 6 条）：

- `…/20261013125054_0_VPWS50_010000.xml`、`…/20261013124054_0_VPWS50_010000.xml`、`…/20261013123057_0_VPWS50_010000.xml`、`…/20261013122057_0_VPWS50_010000.xml`、`…/20261013121101_0_VPWS50_010000.xml`、`…/20261013120118_0_VPWS50_010000.xml`（前缀均为 `https://www.data.jma.go.jp/developer/xml/data/`）

其余 9377 条（包括上表 4 个电文和大阪样本对应条目）两次都在。

## 与文档/预期不一致处

- 访问的是 `regular_l.xml`，但 feed 内 `<link rel="self">` 写的是 `…/feed/regular.xml`，`<id>` 是 `…/feed/regular.xml#long_<数字>`（两次分别为 `#long_1792497091`、`#long_1792500689`）。不要用 self 链接回取 feed。
- 长期 feed 每小时更新一次（实测 HH:51 左右），间隔几分钟抓两次不会有新条目，所以两次间隔约 53 分钟。
- 条目数固定 9383、两次文件大小恰好相同（4586489 字节），不能用大小或条数判断是否更新，应比较 feed `updated` / entry id。
- 一小时内新增的只有每 10 分钟一次的 VPWS50，其他类型这一小时没有发报。
- snapshot2 中条目按 entry `updated` 倒序排列（实测无逆序），但解析器不应依赖这一点。
