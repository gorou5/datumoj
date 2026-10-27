# 72.datumoj.com · 评论区人物资产库

自媒体评论区 69 个角色的人物资产库，用游戏开场「选人画面」的形式展示：每页 9 个角色、共 8 页，69 实装 + 3 个锁定格（No.70–72）= 72 格。

## 内容

- 每个角色一名人名（本轮新起，69 个互不重复，多为与角色特征呼应的谐音名，如达克效应代言人「董半瓶」、时间轴狙击手「瞄小年」）
- 资产卡：人设说明、3 句招牌台词、名字由来、阵营（真爱粉丝 / 挑剔观众 / 理性分析 / 立场交锋 / 路过乱入 / 高阶抬杠）、手感参数（趣味示意）
- 视觉资产：全局粘土风格 Prompt（中/英 + Negative，The Neverhood 式定格粘土美学）+ 每人一句三视图视觉设定，页面内一键复制完整生成 Prompt；立绘本体待按 Prompt 生成后回填

## 文件

- `index.html` — 站点本体（单文件，含全部数据与交互：翻页 / 搜索 / 阵营筛选 / 详情 / 复制）
- `characters.json` — 69 个角色的结构化数据（与页面内嵌数据同源）
- `src/data.py` — 角色原始数据（人设、台词、视觉设定、全局 Prompt）
- `src/build.py` — 由 data.py 生成 index.html 与 characters.json

## 部署

Cloudflare Pages 项目 `datumoj-72`（Datumoj 帐户，Direct Upload），自定义域名 `72.datumoj.com`（CNAME `72` → `datumoj-72.pages.dev`）。

```bash
python3 src/build.py   # 在含 data.py 的目录生成 dist/
wrangler pages deploy dist --project-name=datumoj-72 --branch main
```
