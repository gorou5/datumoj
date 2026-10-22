# DATUMOJ-TACHIBANA-202 完成说明

## 变更文件

- `src/datumoj/parsers/html_page.py`：新增政府 HTML 正文解析器，声明 `source_kind=gov_web`、`content_type=text/html`。使用 `datumoj.text.decode_bytes` 解码；移除常见页面导航和页面外壳后优先选择 `main` / `role=main`，否则选择正文最多的内容块；根据 h1–h3 建立标题路径并按段落边界切块；从更新标签和页面元信息提取日期；根据规范化 URL 的 SHA-1 生成文档键；复制有效的父页面引用和 seed 元数据；正文少于 100 字时抛出包含 `empty_content` 的 `ParseError`。
- `tests/parsers/test_html_page.py`：新增 `ParserSuite` 合格测试和离线样本测试，覆盖内阁府、总务省 Shift_JIS、大阪市、和历/西历日期、元信息日期、标题路径、URL 规范化、元数据保留、长节切块及空页识别。
- `results/DATUMOJ-TACHIBANA-202/test-output.log`：验收命令输出。
- `results/DATUMOJ-TACHIBANA-202/summary.md`：本说明。

## 实现与验证

解析器只依赖契约模型和协议、`datumoj.text` 解码/和历函数及已锁定的 lxml；lxml 设置 `no_network=True`，不执行网页脚本或联网。HTML 中声明的编码用于内容解码，忽略响应头编码。文档发布日期取正文或 meta 中有效的最新更新日，不使用抓取时间；仅有掲載日等日期时保持为空。

运行验收命令 `make check T=tests/parsers/test_html_page.py`：通过，19 项测试通过。`make check` 提示沙箱无法使用 uv，按仓库约定改用只读主仓库 `.venv`，运行正常。

**样本差异：** `dated-wareki.html` 的“申請の手続き”节经当前解析后约 1153 字，没有达到 1500 字。为实际覆盖切块边界，测试在内存中基于该样本增加段落后验证切块；原始 fixture 未修改。该 fixture 本身没有直接产出 `para=` 切块。

未运行网络测试，也未引入或修改依赖。未完成 acceptance 中要求的 other_family 独立复核；测试本身离线，解析器设置了 `no_network=True`，且实现只导入本任务允许的契约及 `datumoj.text`。未验证其他未录制政府站点的专属页面布局；范围外文件未修改。剩余风险是未录制站点可能使用当前常量表未覆盖的导航标记；日期标签之外的无标签日期不会被采用。
