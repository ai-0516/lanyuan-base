# Wiki Schema — lanyuan-base 小区知识库

> 本 wiki 是 lanyuan-base 线上 AI 助手回答小区事实性问题的知识源。
> markdown 页面随 Docker 镜像构建部署（容器无持久磁盘，镜像内置即持久）。
> **AI 运行时只读**，不支持运行时写文档；内容更新走 git 分支 + PR + 部署。

## Domain

覆盖业主可能向 AI 询问的小区/居住相关事实与规定，例如：
- 小区档案（建成/开售年代、户数、户型、物业公司、联系方式）
- 本地法规（供热条例、物业条例等政府公开文件）
- 办事指南（报修、缴费、投诉渠道等）

## Conventions

- 目录：`docs/wiki/raw/` 存源资料（不可变），`docs/wiki/pages/` 存编译知识页
- 文件名：小写、连字符（如 `heating-refund.md`）
- 页面须带 frontmatter（见下）
- 知识页之间用 `[[wikilinks]]` 互链（至少 1 个出链）
- 法规类页面：事实表述后标注依据条款，如 `（依据《徐州市集中供热条例》第28条）`，便于人工核对
- 更新页面时 bump `updated` 日期；新页面加入 `index.md`；每次操作追加 `log.md`
- **口径冲突**：不同来源说法矛盾时，两说并存并标注 `contradictions:`，留人工裁决，不得静默覆盖

## Frontmatter

```yaml
---
title: 页面标题
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: regulation | community | guide   # 法规 / 小区档案 / 办事指南
sources: [raw/<source-file>.md]
# 可选:
contradictions: [other-page-slug]
---
```

## raw/ Frontmatter

源资料（网页/文档原文）也带 frontmatter，用于重摄取时检测漂移：

```yaml
---
source_url: https://原链接
ingested: YYYY-MM-DD
sha256: <正文 hex，不含 frontmatter>
---
```

## Page Thresholds

- 建页：一个主题被 1 份以上源资料实质覆盖且业主查询价值高（法规的每个可独立回答的关切点）
- 不建页：纯程序性、与业主无关的内容（如部门内部职责协调条款）
- 页面超 ~150 行拆分为子主题页并互链
- 内容被新版本完全取代时移入归档并在 index 移除

## Index & Log

- `index.md`：全部知识页目录（每行：wikilink + 一句话摘要）
- `log.md`：追加式操作日志 `## [YYYY-MM-DD] action | subject`
