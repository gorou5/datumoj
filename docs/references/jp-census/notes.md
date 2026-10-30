# 令和6年、7年経済センサス‐基礎調査（jp-census）资料笔记

## 证据标签

S1-S3 指向下表列出的官方页面保存文本；S4 是维护者计划摘录，不是官方来源。核对级别只表示阅读了本次列出的保存页面文本，不表示下载或测试文件。

| 标签 | 官方页面或保存结果 | 验证级别 |
|---|---|---|
| S1 | 総務省統計局「令和6年、7年経済センサス‐基礎調査　調査の結果」：<https://www.stat.go.jp/data/e-census/2024/kekka.html>〔S1|census_results.txt:4〕。（读过官方页面（2026-10-30）；S1） | 读过官方页面（2026-10-30） |
| S2 | 統計局「サイトの利用について」：<https://www.stat.go.jp/info/riyou.html>〔S2|stat_policy.txt:4〕。（读过官方页面（2026-10-30）；S2） | 读过官方页面（2026-10-30） |
| S3 | 「政府統計の総合窓口（e-Stat）利用規約」：<https://www.e-stat.go.jp/terms-of-use>〔S3|estat_site_terms.txt:2〕。（读过官方页面（2026-10-30）；S3） | 读过官方页面（2026-10-30） |
| S4 | 维护者计划摘录 datumoj_plan_excerpt.md；非官方来源。（仅读文档；S4〔S4|datumoj_plan_excerpt.md:6〕） | 仅读文档 |

## 1. 调查身份与已列结果

- S1 标题为『令和6年、7年経済センサス‐基礎調査　調査の結果』〔S1|census_results.txt:4〕。
- 页面将甲调查描述为『民営事業所が対象で、5年に一度実施』〔S1|census_results.txt:7〕。
- 表列出的甲调查确报项为令和6年，发表日期是『令和7年12月24日』〔S1|census_results.txt:9〕〔S1|census_results.txt:10〕。
- 页面另列『速報集計結果』〔S1|census_results.txt:33〕，并指向速报结果概要〔S1|census_results.txt:35〕；该处没有印出速报日期。页面有『乙調査』标题〔S1|census_results.txt:37〕，所读文本没有印出乙调查日期。
- 页面说明标出的统计表是在 e-Stat 发布：『「e-Stat」掲載の統計表です』〔S1|census_results.txt:12〕。页面还提到统计数据库可用于数据提取、表显示、图表制作和数据下载〔S1|census_results.txt:14〕；未据此确认任何具体表号、文件名、sid、statInfId、字节数或下载跳转。
- 本包未捕获各表的 e-Stat 文件页或数据库详情页。表 5、表 1-1 及其文件级信息在所读 S1 页面中 NOT STATED。（仅读文档；S1〔S1|census_results.txt:12〕〔S1|census_results.txt:14〕；S4）

## 2. 页面印出的调查范围注意事项

- S1 明确写出令和6年甲调查不把『雇用者のいない個人経営の事業所』作为调查对象〔S1|census_results.txt:20〕。
- 页面说明与以往经济普查的调查对象范围不同，比较时『比較には留意が必要である。』〔S1|census_results.txt:20〕。
- 页面另说参考表可查看『雇用者のいない個人経営の事業所を含む結果』〔S1|census_results.txt:22〕。
- 页面要求结果使用者查看「甲調査結果　利用上の注意」PDF，并提及「甲調査　用語の解説」PDF〔S1|census_results.txt:25〕〔S1|census_results.txt:26〕；这两份 PDF 本次未捕获，相关定义未据其补充。

## 3. 规约范围、署名与加工

- 统计局规则介绍把统计局网站及其社交媒体账号发布的信息称为内容，并称未另行标示时适用 PDL1.0 条件〔S2|stat_policy.txt:8〕。统计局页面实际写出的条件及例外见 terms.md。
- e-Stat规则自称适用于『当サイトで公開している情報』〔S3|estat_site_terms.txt:4〕。S2与S3分别说明了各自页面内容的范围；所读页面没有说某个指定 census 表格文件究竟由哪一项规则涵盖。该项留给需要确定文件级范围的运营者确认。
- S2 的出典示例包含『出典：総務省統計局ホームページ　（当該ページのURL）』〔S2|stat_policy.txt:19〕及『出典：「○○調査結果」（総務省統計局）』〔S2|stat_policy.txt:20〕。编辑或加工时页面要求另行记载加工事实〔S2|stat_policy.txt:21〕，并禁止以仿佛由国家或府省制作的方式发布加工信息〔S2|stat_policy.txt:21〕。具体原文见 terms.md。
- e-Stat规约的署名、加工与第三方权利内容在本包仅作要点摘要，未逐项整理全部六项规则〔S3|estat_site_terms.txt:4〕。

## 4. 日期与新鲜度边界

- S1 页面列出的确报发表日期为令和7年12月24日〔S1|census_results.txt:10〕。速报与乙调查在已读 S1 文本中的具体日期 NOT STATED〔S1|census_results.txt:35〕〔S1|census_results.txt:37〕。
- S2 规则写明日期为『令和7年3月7日に定めたものです』〔S2|stat_policy.txt:42〕；并称与 CC BY 4.0 兼容〔S2|stat_policy.txt:43〕。
- S3 规则印出的日期为『平成２８年１月２９日に定めたものです』〔S3|estat_site_terms.txt:58〕，并称与 CC BY 4.0 兼容〔S3|estat_site_terms.txt:60〕。
- 整理日期与核对日期均为 2026-10-30；这是本次资料处理日期，不是调查表或利用规则的文件日期。（仅读文档；S1-S4）
- 页面捕获文本没有给出 census 文件的 Last-Modified、归档内容、文件更新周期或文件编码；未作文件下载或 HTTP HEAD，相关值均 NOT STATED。
