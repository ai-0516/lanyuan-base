# Wiki Log

> 知识库操作日志。Append-only。
> Format: `## [YYYY-MM-DD] action | subject`

## [2026-09-06] create | Wiki 初始化（issue #103）

- Domain: lanyuan-base 小区知识库（供热条例首期）
- 结构: SCHEMA.md / index.md / log.md / raw/ / pages/
- 源资料: 徐州市集中供热条例（2025 修订）官网全文 → raw/xuzhou-central-heating-regulation-2025.md
- 知识页 6 个: heating-season-and-standard / heating-refund / heating-responsibility / heating-warranty-transfer / heating-user-rules / heating-fee

## [2026-09-06] compile | 供热条例 → 6 知识页

- 条款引用核对: 6 页共 34 处引用（按条款号出现计数，含同句多处标注），覆盖去重 22 个条款号，全部命中原文（原文共 44 条），无错引/虚构
- 关键事实核对: 供热期 11/21–次年 3/10 与开始前 3 日调试、卧室/起居室 ≥18°C、24h 到达测温、48h 整改、退费三档 20%/50%/全额及 5·31 退费时限、连续停热 >24h 退费、提前 2 日告知、保修期 ≥两供热期、40% 供热门槛、2/3 参与+双过半表决——均与原文一致
- lint: 无断链、无孤儿页、frontmatter 齐全、每页 ≥1 出链

## [2026-09-06] review-fix | PR #104 review 意见修复

- 原文枚举兜底项补齐: heating-user-rules 第 18 条清单补「（七）其他危害供热设施安全的行为」、第 34 条清单补「（六）其他妨碍供热设施正常运行的行为」（SCHEMA 增「原文枚举须完整照录」规则）
- 部署声明成立: .dockerignore 由排除整个 docs/ 改为仅排除 docs/ 下非 wiki 目录，Dockerfile 增 `COPY docs/wiki ./docs/wiki/` → 知识页随镜像内置（容器内 /app/docs/wiki/）
- index.md 移除硬编码 `Total pages: 6`
