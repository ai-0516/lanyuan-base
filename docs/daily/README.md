# 📅 开发日志索引

> 每日工作记录，项目完成后回头看走过的路。  
> 每篇包含：今日成果 × 关键决策 × 技术挑战 × 经验教训

| 日期 | 主题 | 核心收获 |
|------|------|----------|
| [07-25](2026-07-25.md) | AI Tab 核心功能搭建 | harness 模块化、LLM 幻觉记录 |
| [07-26](2026-07-26.md) | Agent 幻觉修复 & 调试工具链 | 回填真实 tool_call、replay-llm CLI |
| [07-27](2026-07-27.md) | @tool 装饰器系统 & Markdown 渲染 | 14 个工具、towxml 集成 |
| [07-28](2026-07-28.md) | 事件驱动的钩子系统 | 观察者模式、8 事件、类型安全 |
| [07-29](2026-07-29.md) | ATIF v1.7 导出 + SQLite 并发修复 | ATOF 实时日志、ATIF 按 RFC 对齐、单连接池 |
| | 独立产出： | [nemo-relay-vs-replay-llm.md](../nemo-relay-vs-replay-llm.md) |
| [07-30](2026-07-30.md) | Error Recovery — 三层容错 | LLMStatus 错误码、指数退避、降级兜底 |
| | 独立产出： | [error-recovery.md](../error-recovery.md) |
| [07-31](2026-07-31.md) | Context Compact 四层压缩 + Review 流程改进 | L4 保留尾部、review 脚本入库、worktree 策略 |
| | 追加： | Issue #19 业务错误语义演进（api_success(None)）、Issue #22 message:start 边界（fallback 路径） |

---

## 技术债 & 待办

- [ ] large_tool 数据收集到位后调整阈值
- [x] LLM usage 数据写入 DB（模型已建，钩子未写库） — ✅ 已实现
- [ ] tool_executor.execute 注入 db/user_id 的任务（待设计）
- [ ] 部分 tool 返回仍包含 avatar 字段（如 `create_comment`），`_strip_avatar` 未覆盖所有路径 — 需排查并统一清理
- [ ] 如何使用导出的 ATIF 文件（文档 + 示例）
- [x] 给 dev 和 dev-lead 配置不同的 GitHub 账户，避免 credentials 混淆 — ✅ 已落地（2026-07-31，org ai-0516 + machine user，见 [github-multi-identity.md](../github-multi-identity.md)）
- [ ] SSE 事件类型统一定义（目前 ai.py 用白名单硬编码，streaming/agent 各有事件产出，需统一管理事件类型常量 + 黑白名单策略）
- [x] #8 上下文压缩实现时：`PAYLOAD_TOO_LARGE`（errors.py）从 `None` 改为「压缩后重试」配置，并同步 `docs/error-recovery.md` — ✅ 已实现（2026-07-31，feat/8 分支）
