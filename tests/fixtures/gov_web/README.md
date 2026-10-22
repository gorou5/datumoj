# gov_web：省厅与自治体政策网页样本（TACHIBANA H1）

录制日期：2026-10-22（JST）。录制工具：curl，User-Agent `DATUMOJ/0.2 (+https://github.com/gorou5/datumoj; research fixture recording)`，相邻请求间隔至少 3 秒，共发出 52 次请求（含未保存的探测请求）。只抓公开页面，没有登录，没有提交表单，也没有执行 JavaScript。

响应字节原样保存，没有转码，也没有美化。下表的 Content-Type 是响应头里的值，“声明编码”是 HTML 内 `<meta>` 或 XML 声明写的编码。

## 文件清单

| 文件 | 原始 URL | 抓取时间 (JST) | HTTP | Content-Type | 声明编码 | 大小 (B) | SHA256 | 用途 | 父页面（来自哪个页面的链接） |
|---|---|---|---|---|---|---|---|---|---|
| html/cao/seed.html | https://www8.cao.go.jp/cstp/openinnovation/index.html | 2026-10-22T10:17:42 | 200 | text/html | UTF-8 | 4733 | 77b3e407664d94f7f3546740dc4059c6fb1b1c5b134b13a0b00786a377dc5c3a | 种子页（イノベーション推進 栏目首页） | — |
| html/cao/detail-1.html | https://www8.cao.go.jp/cstp/openinnovation/procurement/index.html | 2026-10-22T10:18:27 | 200 | text/html | UTF-8 | 6177 | 09d80e08ea608dde87780e39977b1cd1056d66075210172ce1b792e2e88aa4f8 | 详情页（スタートアップによる公共調達の促進） | html/cao/seed.html |
| html/cao/detail-2.html | https://www8.cao.go.jp/cstp/openinnovation/ecosystem/index.html | 2026-10-22T10:18:30 | 200 | text/html | UTF-8 | 7818 | 8d8650352e2336c8efa18045d3f4d9901133540e9de54e0f3fa0e431cefc787d（已脱敏，见“个人信息”） | 详情页（スタートアップ・エコシステム拠点都市） | html/cao/seed.html |
| html/soumu/robots.txt | https://www.soumu.go.jp/robots.txt | 2026-10-22T10:15:32 | 200 | text/plain | — | 35 | e0cc280a3ed01a4ee4785320fdacf31ef4724eddf0a46eb860df651eaede55e4 | robots（只禁止 ia_archiver） | — |
| html/soumu/seed.html | https://www.soumu.go.jp/denshijiti/digital_transformation.html | 2026-10-22T10:16:36 | 200 | text/html（响应头没有 charset） | Shift_JIS | 42520 | b96356a848073a26ad467dd1704bf743a56636cd1bcdcf9b6f946b569d07cfa8 | 种子页（地域におけるデジタル・トランスフォーメーション） | — |
| html/soumu/detail-1.html | https://www.soumu.go.jp/denshijiti/index_00001.html | 2026-10-22T10:17:46 | 200 | text/html | Shift_JIS | 68142 | fbe79047f0dd7473cb7b44c7dc84b724e646ba458f4c76381003d71db8b4b6ae | 详情页（自治体DXの推進） | html/soumu/seed.html |
| html/soumu/detail-2.html | https://www.soumu.go.jp/denshijiti/dejitarujinzai.html | 2026-10-22T10:17:49 | 200 | text/html | Shift_JIS | 44009 | 341aade6c0f201c46eb31eb4e27deb78aae2defad774aa23fa3bada2b462ea2d | 详情页（デジタル人材の確保・育成） | html/soumu/seed.html |
| html/tokyo/robots.txt | https://www.metro.tokyo.lg.jp/robots.txt | 2026-10-22T10:15:35 | 200 | text/plain;charset=UTF-8 | — | 712 | 357c4178d80b3ee2889539ef2bb43f9f1cad661c53275c039f1088709f08a384 | robots（含 `/*?page=` 等通配规则） | — |
| html/tokyo/robots-sangyo-rodo.txt | https://www.sangyo-rodo.metro.tokyo.lg.jp/robots.txt | 2026-10-22T10:16:50 | 200 | text/plain;charset=UTF-8 | — | 724 | 17ea0b6b3a1ced5500caf645ae1371e0ddc23c7dc1c7c7c6da5c016b861c7412 | robots（产业劳动局主机） | — |
| html/osaka-city/seed.html | https://www.city.osaka.lg.jp/keizaisenryaku/category/3037-1-0-0-0-0-0-0-0-0.html | 2026-10-22T10:17:36 | 200 | text/html | utf-8 | 31604 | 12bacaea342b3acd90af72ffbebb4ef2b585e91ada31ba7ae1fd54aaf5dba1e3 | 种子页（産業支援・創出支援・特区制度など） | — |
| html/osaka-city/detail-1.html | https://www.city.osaka.lg.jp/keizaisenryaku/page/0000685899.html | 2026-10-22T10:18:39 | 200 | text/html | utf-8 | 29576 | 126d7f17c3d62aa034fb30484d6afb4650057e4433f209c7bc0c4439cbb09557 | 详情页（先駆的金融市場等形成支援事業補助金）；页内日期 `2026年8月26日` 在 `div.page_day01` 里，没有“更新日”标签 | html/osaka-city/seed.html |
| html/spa/robots.txt | https://www.jgrants-portal.go.jp/robots.txt | 2026-10-22T10:22:10 | 200 | text/plain; charset=UTF-8 | — | 44 | 85fbeb05341de94979357cce40045d49a45294b5d07d0cda179e4b5d5631b736 | robots（`Disallow: /` 加 `Allow: /index.html`，可用来测试 Allow 优先） | — |
| html/spa/shell.html | https://www.jgrants-portal.go.jp/index.html | 2026-10-22T10:22:15 | 200 | text/html; charset=UTF-8 | utf-8 | 85904 | 227b7f927468b3dcf2e296ead93262ee2bb62e03dd25fcfc10b6a3210029e4bb | SPA 外壳页（Angular：body 里只有 `<app-root></app-root>` 和脚本，去掉脚本和样式后可见文字约 28 字） | — |
| pdf/text.pdf | https://www8.cao.go.jp/cstp/openinnovation/procurement/guideline.pdf | 2026-10-22T10:19:42 | 200 | application/pdf | — | 1172266 | b7832bd12ab490a9b33ef81ab5d9b366868ed9bd25b83a6bcc37f9e2546c4dc9 | 有文本层的 PDF，36 页 | html/cao/detail-1.html |
| pdf/broken.pdf | （生成）text.pdf 的前 40% 字节 | 2026-10-22（生成） | — | — | — | 468906 | 74ce839a4bf658e29d1c8ca921a11689cc7cbdd68c8bd9c3a3d8098ee5fc92a2 | 损坏的 PDF：把 text.pdf 截断到前 468906 字节（int(1172266×0.4)）生成，pypdfium2 打开时报 `Data format error` | pdf/text.pdf |

### 没有录到的文件
- `pdf/encrypted.pdf`、`pdf/scanned.pdf`：公开站点上没有录到（试过的 PDF 都有文本层），改为合成，见“合成样本”一节。
- 各站点的 robots.txt 状态：
  - 内阁府：`www.cao.go.jp/robots.txt` 和 `www8.cao.go.jp/robots.txt` 都是 404（返回 HTML 错误页），按“没有 robots”处理，没有保存。
  - 经产省：`www.meti.go.jp/robots.txt` 返回 403（CloudFront 和 S3 的错误页），没有保存。
  - 大阪府：`www.pref.osaka.lg.jp/robots.txt` 是 404，没有保存。
  - 大阪市：`www.city.osaka.lg.jp/robots.txt` 是 404，没有保存。
  - e-Gov 法令检索：`laws.e-gov.go.jp/robots.txt` 返回 403（“ただいまシステムメンテナンス中です”），没有保存。

## 合成样本（2026-10-22 主对话补充）

公开站点上找不到的情形用合成样本补上。合成样本的内容都是虚构的（“架空市”），HTML 文件开头有注释说明，不是任何真实网站的页面。PDF 由 `gen/make_pdfs.py` 生成（只用标准库和 Pillow），可以重新生成。

| 文件 | 大小 (B) | SHA256 | 用途 |
|---|---|---|---|
| pdf/scanned.pdf | 119455 | 4851bd229b56803476d6c1d6b23d466262e46411ae78c51109b2330f240edf8a | 合成扫描件：两页纯图像，没有文本层（pypdfium2 每页取到 0 字） |
| pdf/encrypted.pdf | 918 | 760cc3fb5ecee67200b42e9919f96da31c2367c2b356cfe4d76d5ed920ee5317 | 合成加密件：标准安全处理器 RC4 40 位，用户密码 `datumoj-test`；不给密码打开报 Incorrect password |
| html/synthetic/dated-wareki.html | 5096 | 018ba3a15796fe2d0b246fa48fbd55caa25d9533c75ef61f98fe3f1d01761e5e | 合成页：导航、面包屑、侧栏、页脚齐全；`最終更新日：令和7年4月1日` 和 `掲載日：2025年3月15日`；h1–h3 分节；一节超过 1500 字 |
| html/synthetic/injection.html | 1597 | 8cfb4f2839a6997a0c539928c202a818e786b52f7eada5bf4b0e0a2151b07489 | 合成页：正文里放了“指令样式”的文字（日文和英文各一句），用于注入评测 |
| html/synthetic/site/robots.txt | 49 | e375d660b37173b48a72c1d3a306cbd064c739b895982cb7b64f2f95a6e10e02 | 合成小站点的 robots：禁止 `/policy/startup/private/` |
| html/synthetic/site/policy/startup/index.html | 1935 | e3b53f64acbabc4b48cbb82ab725f8c1c44b9dc9b21c1cf079ff0efe428f1c5b | 合成小站点的种子页（深度 0）：二级页、片段和 utm 重复链接、robots 禁止页、PDF、前缀外、外部主机、mailto / javascript / tel 链接 |
| html/synthetic/site/policy/startup/detail-a.html | 1287 | e866a50e4a321fb45073c2e3535ee2933d8b3cff0a8e0b15ac390ac356ae07ee | 深度 1；链接到深度 2 的页面和重复的 PDF 链接 |
| html/synthetic/site/policy/startup/deeper/detail-b.html | 1142 | f53068b9e34e4541f1658fc2e72885ac15d6cc51b176337acb8a91736a080817 | 深度 2；链接到深度 3 |
| html/synthetic/site/policy/startup/deeper/deep/detail-c.html | 1141 | 6707658018694f36407a9d167dd0c1bcd77800cfb5c1c888cbd7aefd80c10904 | 深度 3：默认 max_depth=2 时不应被抓取 |
| html/synthetic/site/policy/startup/private/internal.html | 1062 | 93d5a37f068ecfd09a89d729ea378a8b1e814ef01fea7ad73ee5e10e406ff613 | robots 禁止的页面：不应被请求 |
| html/synthetic/site/policy/other/out.html | 1027 | 6c168871f3365fe59810af21c2dc824f46f2729d790478ff859b4421235d5f4e | 路径前缀之外：不应被跟进 |
| html/synthetic/site/policy/startup/guide.pdf | 674 | 60960fb92fea56127d9e7a688182f713c50fc468b45103afd3ce7193bafe9827 | 小站点的文本 PDF（1 页） |

## 种子清单

| 站点 | 种子 URL | 栏目名 (label) | 建议 path_prefix | 状态 |
|---|---|---|---|---|
| cao | https://www8.cao.go.jp/cstp/openinnovation/index.html | イノベーション推進（科学技術・イノベーション推進事務局） | `/cstp/openinnovation/`（主机 www8.cao.go.jp） | 已录。www.cao.go.jp 本身很少有政策栏目，内阁府的政策页面大多在 www8 等子域名上 |
| meti | https://www.meti.go.jp/policy/newbusiness/index.html | スタートアップ・新規事業 | `/policy/newbusiness/` | **没有录到**：robots.txt、种子页和首页都返回 403 |
| soumu | https://www.soumu.go.jp/denshijiti/digital_transformation.html | 地域におけるデジタル・トランスフォーメーション | `/denshijiti/` | 已录（页面是 Shift_JIS） |
| tokyo | https://www.sangyo-rodo.metro.tokyo.lg.jp/chushou/shoko/jyosei/ | 中小企業向けの助成（産業労働局） | `/chushou/shoko/`（主机 www.sangyo-rodo.metro.tokyo.lg.jp） | 只保留 URL 和 robots，原因见利用规约一节。曾抓过的详情页：https://www.sangyo-rodo.metro.tokyo.lg.jp/chushou/shoko/chizai/jyosei/（不带斜杠的 `.../index.html` 会 301 到它）。注意：这个 CMS 的页面每页 510–550KB |
| osaka-pref | https://www.pref.osaka.lg.jp/o110030/ritchi/ichiran.html | 国際ビジネス・スタートアップ支援課 事業一覧 | `/o110030/` | 只保留 URL，原因见利用规约一节。曾抓过的详情页：https://www.pref.osaka.lg.jp/o110030/shogyoshien/shokishien/index.html（有 `更新日：2019年3月8日`）、https://www.pref.osaka.lg.jp/o110030/ritchi/globalstartup/index.html（有 `更新日：2023年5月15日`） |
| osaka-city | https://www.city.osaka.lg.jp/keizaisenryaku/category/3037-1-0-0-0-0-0-0-0-0.html | 産業支援・創出支援・特区制度など（経済戦略局） | `/keizaisenryaku/` | 已录 |

## 各站点利用规约

| 站点 | 利用规约 URL | 结论 |
|---|---|---|
| cao | https://www.cao.go.jp/notice/rule.html（令和7年3月25日現在） | 适用“公共データ利用規約（第1.0版）”（デジタル庁）：可以转载和再分发，包括修改和商用；需要注明出处，修改过的要另行说明（与 CC BY 4.0 兼容）。徽标等少数内容除外。 |
| meti | https://www.meti.go.jp/main/rules.html（**没有核对**：经产省全站返回 403） | 据既有了解，经产省也采用政府标准利用规约或公共数据利用规约（与 CC BY 兼容），但这次没有取到原文，需要另行确认。本次没有保存任何 METI 内容。 |
| soumu | https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html | 适用“公共データ利用規約（第1.0版）”，需注明出处，例如“出典：総務省ホームページ（URL）”；修改过的要说明，并且不得做成看起来像是国家所作的样子（与 CC BY 4.0 兼容）。可以再分发。 |
| tokyo | https://www.sangyo-rodo.metro.tokyo.lg.jp/policy | **不允许再分发**：页面写明 `無断で複製・転用することはできません`，只有私人使用和引用等著作权法允许的情形除外。所以只保留 robots.txt 和 URL 记录，不保存页面正文。www.metro.tokyo.lg.jp 主站的规约没有核对。 |
| osaka-pref | https://www.pref.osaka.lg.jp/o070050/koho/information/use.html | **不允许再分发**：页面写明 `無断使用・無断転載することはできません`，只有私人使用和引用等情形除外。所以不保存页面正文；robots.txt 是 404，因此只有 URL 记录。 |
| osaka-city | https://www.city.osaka.lg.jp/main/site_policy/0000000160.html（掲載データを使う，2026年3月9日） | 大阪市拥有权利的标题、正文、数值和表格可以“不经确认直接使用”，复制、公众传播和改编都可以，商用也可以。条件是注明出处，例如“出典：大阪市ホームページ（URL）”，修改过的要说明（CC BY 兼容）。没有许可标示的图片和附件需要自行确认，所以本次没有保存大阪市的 PDF。 |
| spa（jGrants） | 没有核对（robots.txt 只允许 /index.html，所以没有去抓规约页） | 外壳页几乎只有代码，没有正文。运营方是デジタル庁，其主站采用公共数据利用规约；jGrants 自己的规约需要另行确认。 |

## 个人信息（已处理）

- `html/cao/detail-2.html` 第 97 行的问合先原本列出多名职员的姓氏，提交前已替换为 `○○`（只改了这几个字，其余字节不变；上表的 SHA256 是脱敏后的值）。同一处的 `03-6257-1333(直通)` 是部门直通电话，保留。
- 其余页面和 `pdf/text.pdf` 里出现的电话、传真、邮箱都是部门或机关的联系方式，不是个人信息。

## 其他备注

- **日期抽取**：详情页里带标签的“更新日”只出现在大阪府页面上（西暦），而大阪府页面因为规约原因没有保存。已保存的页面里：
  - 大阪市 detail-1 有一个没有标签的西暦日期（`div.page_day01`）。
  - 总务省 detail-1 正文里有大量和历（例如 `2025年（令和7年）12月改定`），但没有“更新日”标签。
  - 内阁府 detail-2 的 h2 里有 `令和７年６月選定`（全角数字）。

  也就是说，带标签的和历更新日样本没有录到。
- **HTML 编码**：总务省页面的响应头是 `Content-Type: text/html`，不带 charset，只在 XML 声明和 meta 里写了 Shift_JIS，可以用来测试编码检测。
