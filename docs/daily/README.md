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
| [08-01](2026-08-01.md) | 飞书群聊 Code & Review 流程自动化 | group_msg 权限、单聊模式（REQUIRE_MENTION=true）、bot 间 @ 通知、daily 分 dev/dev-lead 两部分 |
| [08-02](2026-08-02.md) | 统一错误格式 + Like 外键约束（#27/#28） | Starlette HTTPException 精确键查找、RequestValidationError 0.141+ 非 HTTPException 子类、SQLite PRAGMA FK、迁移双方言、review 脚本入库 |
| [08-03](2026-08-03.md) | PR #33 二轮 review：记忆系统 15 条意见 | memory_service 分层、AGENT_END 补 meta + try/finally 防泄漏、relevant 拼 user 消息保缓存命中、DBMemoryProvider 改名、consolidate LLM 失败降级 |

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
