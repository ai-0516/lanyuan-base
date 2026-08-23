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
| [08-02](2026-08-02.md) | 统一错误格式 + Like 外键约束（#27/#28）+ 跨会话记忆（#9） | Starlette HTTPException 精确键查找、RequestValidationError 0.141+ 非 HTTPException 子类、SQLite PRAGMA FK、迁移双方言、review 脚本入库、MemoryProvider 抽象层（写入/读取/抽取）、agent:end 异步提取 |
| [08-03](2026-08-03.md) | PR #33 二轮 review：记忆系统 15 条意见 | memory_service 分层、AGENT_END 补 meta + try/finally 防泄漏、relevant 拼 user 消息保缓存命中、DBMemoryProvider 改名、consolidate LLM 失败降级 |
| [08-04](2026-08-04.md) | dev-server 部署 + seed_data.py 修复（#37） | systemd 常驻、sftp 绕过 banner、engine.dispose() 消除退出噪音 |
| [08-05](2026-08-05.md) | System Prompt 运行时组装（#11） | PROMPT_SECTIONS 拆分、确定性缓存（json.dumps + lru_cache）、workspace 按需注入 |
| [08-06](2026-08-06.md) | Session 管理设计调研与定案（#41）+ TECH_SPEC 第 8 章（#43） | 压缩旋转 rotation、摘要 tool 消息入库 + tail 迁移、/new 移除、search_history（LIKE 起步）、缓存去 LRU |
| [08-07](2026-08-07.md) | LLM Adapter 方案调研与定案（#15）+ TECH_SPEC | pi-ai 多厂商抽象调研（canonical block 消息 / 两段式转换）、D1 block 风格 / D2 一段式 / D3 LLM_* 配置、Message 表列式存储无需迁移、LLMAdapter 抽象类（review 采纳） |
| [08-09](2026-08-09.md) | evals 评测体系调研与落地（#55/#56/#57）+ max_tokens 调研 | 三轮调研吸收 8 设计点、架子优先策略、运行形态两档（--llm 门控）、报告器独立包零依赖、harness 复用 agent 循环 + mock 冒烟 |
| [08-10](2026-08-10.md) | dev-server 事故排查：帖子接口 500 + error.log 未记录（#64 → PR #65） | 连接池僵尸连接（Lost connection 2013）、SQLAlchemy 异常自带 detail=[] 致 hasattr 误判、fastapi.HTTPException 是独立子类、starlette 1.3.1 ServerErrorMiddleware 总是 re-raise、pool_pre_ping/recycle 保活 |

---

| 日期 | 链接 |
|------|------|
| 2026-08-23 | [2026-08-23.md](2026-08-23.md)（v2 M1 review）|

## 技术债 & 待办

- [ ] large_tool 数据收集到位后调整阈值
- [x] LLM usage 数据写入 DB（模型已建，钩子未写库） — ✅ 已实现
- [ ] tool_executor.execute 注入 db/user_id 的任务（待设计）
- [ ] 部分 tool 返回仍包含 avatar 字段（如 `create_comment`），`_strip_avatar` 未覆盖所有路径 — 需排查并统一清理
- [ ] 如何使用导出的 ATIF 文件（文档 + 示例）
- [x] 给 dev 和 dev-lead 配置不同的 GitHub 账户，避免 credentials 混淆 — ✅ 已落地（2026-07-31，org ai-0516 + machine user，见 [github-multi-identity.md](../github-multi-identity.md)）
- [ ] SSE 事件类型统一定义（目前 ai.py 用白名单硬编码，streaming/agent 各有事件产出，需统一管理事件类型常量 + 黑白名单策略）
- [x] #8 上下文压缩实现时：`PAYLOAD_TOO_LARGE`（errors.py）从 `None` 改为「压缩后重试」配置，并同步 `docs/error-recovery.md` — ✅ 已实现（2026-07-31，feat/8 分支）
