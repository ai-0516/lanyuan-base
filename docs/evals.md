# Agent 行为评测（evals）体系

> 覆盖：ATOF 报告器（#56）、评测 harness（#57）、测试集（#58）、CI 集成（#59 进行中）
> 调研背景见 issue #55（三轮调研 + 8 个吸收设计点 + 定稿方案）。

## 评测什么

pytest 覆盖函数/API 层和部分编排层，但测不到「行为层」——真实对话里 agent 会不会**正确调用工具**、回答准不准。evals 补的正是这层。

## 运行形态两档（#55 定稿）

| 档位 | 内容 | 成本 | 运行方式 |
|---|---|---|---|
| 无 LLM 类 | ATOF 报告器（读 jsonl 出指标）+ 测试集 mock 冒烟跑 | 免费 | pytest 集成 + CI 可跑（#59 接入） |
| 有 LLM 类 | 评测 harness（跑真实 agent） | 花钱 | **仅独立命令 + `--llm` 门控**，CI 不挂 |

## 模块结构

```
backend/
└── app/harness/evals/
    ├── report.py                  # 无 LLM 类：ATOF 报告器（读 jsonl 出行为指标）
    ├── judge.py                   # 确定性断言组件 + AgentTrace
    ├── harness.py                 # 执行器：run_task / compare / bootstrap / load_tasks
    └── cli.py                     # 有 LLM 类入口，--llm 门控
```

设计原则（#55/#56 定稿）：

- **数据格式服务于评测**：指标只用语义明确的字段（`tool:end.status`、`llm:end.usage`），不写启发式兜底；字段缺失改 jsonl.py 写入而非在报告器里猜
- **确定性优先**：有客观判据一律确定性断言（tool_call 序列/参数、marker、DB 状态），零 LLM judge；回答质量类才考虑 LLM judge（v2）
- **配置三分离**：被测配置（RunConfig：system_prompt）vs 测试集（tasks）vs 评测参数（reps）
- **样本与逻辑分离**：任务文件（数据）导出 `TASKS`，judge 是独立逻辑组件
- **不修改生产代码**：复用 `AIAgent` + `TOOLS` + `build_messages`

## ATOF 报告器（#56）

```bash
cd backend
uv run python -m app.harness.evals.report                    # 默认读 logs/llm-requests/
uv run python -m app.harness.evals.report <jsonl 文件或目录>
```

指标口径：

| 指标 | 来源 |
|---|---|
| turns | turn:start 事件数 |
| llm_calls | llm:end 事件数 |
| tool_calls | tool:start 事件数 |
| tool_errors | tool:end 且 status != "ok" |
| retries | 工具出错后同名工具再次调用（error → 同名 start） |
| tokens | llm:end usage.completion_tokens 之和（无 usage 回退 tokens 字段） |
| wall_s | req 内首事件到 agent:end 的墙钟秒数 |

> **口径差异（PR #67 review）**：`reqs_with_error`（聚合 TOTAL 行）只统计 **req 级错误**（agent:end.error / llm:error），
> 而 `tool_errors` 是**工具级错误**（tool:end status != "ok"）——同一请求可能工具出错但重试成功（req 最终无错），
> 因此 TOTAL 行可能出现 `reqs_with_error=0` 但 `errs=1` 的情况。两口径都正确，各司其职，不是 bug。

## 评测 harness（#57）

### 任务定义

测试集 = 纯数据（jsonl）+ 判定逻辑（judge 组件）分离（#58 落地，#66 定：任务文件只用 jsonl，
需要新判定时先补全 judge 组件、再用 jsonl 定义 case，不保留 Python 任务文件）。

**jsonl 数据文件**：每行一个 JSON 对象
`{"name", "prompt", "expect"}`，空行与 `#` 注释行跳过。expect 翻译为确定性 judge：

```json
{"name": "search_rotation_session",
 "prompt": "帮我找一下上次讨论的 session 旋转方案",
 "expect": {"tool": "search_history", "params": {"query": ["旋转"]}}}
```

expect 支持（判定键互斥，组合用 all/any 显式表达，可递归）：

| expect | 翻译为 | 说明 |
|---|---|---|
| `{"tool": "名称"}` | ToolCalled | 断言调用了工具 |
| `{"tool": "名称", "params": {...}}` | ToolCalled | 参数子集匹配；**params 值为 list = 包含匹配**（实际参数须含全部字符串，用于 query 措辞不可控的检索断言） |
| `{"no_tool": true}` | NoToolCalled | 断言未调用任何工具 |
| `{"marker": "字符串"}` | MarkerInReply | 最终回复包含 marker |
| `{"db_memory_contains": "关键词"}` | DBMemoryContains | DB 状态检查：该用户已写入含关键词的记忆 |
| `{"all": [...]}` / `{"any": [...]}` | AllOf / AnyOf | 递归组合 |

judge 组件：`ToolCalled(name, params=None)`（params 为参数子集匹配）/ `NoToolCalled()` / `MarkerInReply(marker)` / `DBMemoryContains(keyword)` / `AllOf(*j)` / `AnyOf(*j)`。

**首批测试集**（`backend/app/harness/evals/tasks/*.jsonl`，#58）：检索正确性
（search_history 调用 + query 含关键词）、记忆正确性（memory_add 落库 +
memory 工具查询不瞎编）、边界行为（检索无结果/领域外问题不瞎编）三维度各 2 题。

### 运行（有 LLM 类，花钱）

```bash
cd backend
# 单配置跑全部任务
uv run python -m app.harness.evals.cli --tasks app/harness/evals/tasks/ --llm

# baseline/candidate 对比（system_prompt 维度）+ bootstrap 置信度
uv run python -m app.harness.evals.cli --tasks app/harness/evals/tasks/ --llm \
    --baseline-prompt-file baseline.txt --candidate-prompt-file candidate.txt --reps 3
```

- **`--llm` 是门控**：不加直接退出，零 LLM 调用（CI 不会误触发花钱）
- 数据库默认独立评测库 `eval_lanyuan.db`（不碰开发/生产数据），`--db-url` 可覆盖
- **每轮评测自动重置评测用户数据**（#58 可复现性）：上轮的对话/记忆会污染下轮
  陷阱题判定（如 memory_add 写入的偏好会被下轮检索到），从干净状态开始
- 无 LLM key 时自动走 mock（`mock_chat`），管线零成本冒烟，但不产出真实结论

## CI 无 LLM 档（#59）

`eval` job（与 lint/test 平级）三段，全部零 LLM 调用：

| 段 | 内容 | 作用 |
|---|---|---|
| 单元测试 | `pytest -m eval`（judge/harness/report/tasks 四件套标记 `pytest.mark.eval`） | 组件层确定性测试 |
| ATOF 报告器 | 跑 `tests/data/sample_atof.jsonl` 出指标表 | 报告器可用性 |
| 测试集冒烟跑 | 每个任务 mock 跑通 agent 循环 + judge，输出 OK/ERROR | 任务定义与 judge 可执行（不抛异常） |

> **冒烟跑为什么是 OK/ERROR 而非 PASS/FAIL**：mock 下 agent 不调工具，工具/标记/DB
> 断言必然 FAIL（实测 0/6），展示会误导成「评测全挂」；每任务真实判定（PASS/FAIL）
> 由开发期 `--llm` 手动评测产出（#58 验收 6/6 通过）。
> 报告贴 PR 评论（删旧重建，始终在 PR 最底部）+ Artifact 完整报告。

### 对比报告

```
task                         baseline           candidate        delta
----------------------------------------------------------------------
retrieval_uses_search_hist  100% (100-100)     100% (100-100)     0pp
```

delta 为 candidate - baseline 的百分点；括号内为 bootstrap 95% 置信区间（区分真提升与随机波动，n=reps 小时成功率差异是噪声，需逐次审计）。

## 状态与后续

- ✅ #56 报告器（PR #60，已 merge）
- ✅ #57 harness 框架（PR #61）
- ✅ #58 首批测试集（检索/记忆/边界行为陷阱题，jsonl 数据文件）
- ⏳ #59 CI 集成（PR #67 已实现：无 LLM 档三段 + PR 评论摘要 + Artifact；待 merge）

评测不修改生产代码，全部开发期离线运行。
