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

## [2026-09-06] add | 校验脚本入库（review 建议落地）

- 新增 `backend/scripts/review/wiki_kb/review.py`（纯 stdlib，无第三方依赖）：
  1. 结构 lint — index 与 pages/ 一致、wikilink 无断链（排除代码块）、无孤儿页、每页 ≥1 出链、frontmatter 五键齐全且 sources 指向存在 raw
  2. raw 溯源 — frontmatter sha256 与正文复算一致（源漂移检出）
  3. 条款引用 — 页面引用的条款号（中/阿拉伯数字）均存在于 sources raw 原文
  4. 镜像构建 — .dockerignore 放行 docs/wiki（忽略注释行）、Dockerfile 含 COPY docs/wiki
- SCHEMA.md 增「校验（编译后必跑）」节：编译后须 `cd backend && uv run python scripts/review/wiki_kb/review.py` 全绿（exit 0）才可提交
- 判别力验证：人为破坏 5 处（断链/错条款 99/sha256 漂移/index 不一致/dockerignore 真行移除仅留注释）脚本全部抓出 ❌，还原后全绿 ✅

## [2026-09-18] add | 2025 年东方兰园车库管理方案与历史车位位置

- 源资料：2025 年 10 月《东方兰园车辆管理公告》Word 文档及“可出租车位明细”图片
- 新增知识页 3 个：parking-management-rules / parking-space-fees-and-rental / parking-space-location-reference
- 完整转录历史公告中的管理原则、违停措施、车辆登记、费用、租期和车位出售后的处理规则
- 录入 A/B/C/D 四区共 56 个车位及对应楼栋或通道位置
- 时效确认：资料提供者于 2026-09-18 确认，附件所列 56 个车位均已出租；清单仅作历史公告核对和位置参考，不作为当前可租信息
- 车位容量：按 parking_spots_buildings_gates_roads.json 统计 A 区 234、B 区 228、C 区 324、D 区 244、F 区 355，JSON 小计 1385 个；资料提供者确认另有 10 个 W 编号车位未纳入 JSON，停车场合计 1395 个；所有 W 车位均无精确坐标，公告仅提供其中 4 个编号的大致方位文字，其余 6 个编号待补充
