# wto_bulk：WTO 商品贸易年度批量包（裁剪样本）

供 DATUMOJ-WISTERIA-306（WTO 批量包 → 观测值）离线测试使用。定稿见 `docs/plans/0.4-wisteria-sources.md` 2.5、4.5。

- 原文件：`https://stats.wto.org/assets/UserGuide/merchandise_values_annual_dataset.zip`，2026-11-17（JST）下载，约 2.1 MB，内含一个约 68 MB 的 CSV；`Last-Modified: Wed, 28 Oct 2026 19:55:37 GMT`，ETag 和 SHA-256 见 `_responses.json` 的 `derived_from`。原文件不提交。
- `https://stats.wto.org/robots.txt` 返回 200，批量包路径未被禁止。
- 样本类型 `derived`：`merchandise_values_annual_dataset_excerpt.zip` 只含一个同名 CSV，是原 CSV 的表头 + 下列 124 行，**按 cp1252 重新编码**（与原文件一致，非 UTF-8），行尾 CRLF 原样保留：
  - 6 个报告方（840、156、276、250、392、410）× `ITS_MTV_AX`/`ITS_MTV_AM` × 伙伴 `000` × 产品 `TO` × 2015–2024，共 120 行；这些行都没有值标记；
  - scope 外 4 行：2 行农产品（`AG`），1 行 Sao Tomé and Principe（含 `é`，测 cp1252 解码），1 行 Senegal（`ValueFlagCode=B` 断点；要测标记时，测试把报告方 686 加进自己的 scope）。
- 完整性校验：`shasum -a 256 -c SHA256SUMS`。

## 许可与署名

- 数据门户标 ODbL 1.0；网站版权页允许非商业复制并署名 WTO。本项目按非商业使用定为 `raw_ok`（2026-11-16 负责人定），输出须带署名和 ODbL 通知。
- 署名：Source: World Trade Organization, WTO Stats (merchandise_values_annual_dataset), made available under the ODbL 1.0.
