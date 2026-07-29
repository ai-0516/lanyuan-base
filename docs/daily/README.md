# 📅 开发日志索引

> 每日工作记录，项目完成后回头看走过的路。  
> 每篇包含：今日成果 × 关键决策 × 技术挑战 × 经验教训

| 日期 | 主题 | 核心收获 |
|------|------|----------|
| [07-25](2026-07-25.md) | AI Tab 核心功能搭建 | harness 模块化、LLM 幻觉记录 |
| [07-26](2026-07-26.md) | Agent 幻觉修复 & 调试工具链 | 回填真实 tool_call、replay-llm CLI |
| [07-27](2026-07-27.md) | @tool 装饰器系统 & Markdown 渲染 | 14 个工具、towxml 集成 |
| [07-28](2026-07-28.md) | 事件驱动的钩子系统 | 观察者模式、8 事件、类型安全 |
| [07-29](2026-07-29.md) | NeMo Relay vs replay-llm 对比分析 | ATIF 轨迹导出、Correlation ID、Codec 层 |

---

## 技术债 & 待办

- [ ] large_tool 数据收集到位后调整阈值
- [x] LLM usage 数据写入 DB（模型已建，钩子未写库） — ✅ 已实现
- [ ] 部分 tool 返回仍包含 avatar 字段（如 `create_comment`），`_strip_avatar` 未覆盖所有路径 — 需排查并统一清理
- [x] `pyproject.toml` 中 `tool.uv.dev-dependencies` 迁移到 `dependency-groups.dev` — ✅ 已迁移
