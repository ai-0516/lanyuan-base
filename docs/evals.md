# Agent 行为评测（evals）体系

> 覆盖：ATOF 报告器（#56）、评测 harness（#57）、测试集（#58 待做）、CI 集成（#59 待做）
> 调研背景见 issue #55（三轮调研 + 8 个吸收设计点 + 定稿方案）。

## 评测什么

pytest 覆盖函数/API 层和部分编排层，但测不到「行为层」——真实对话里 agent 会不会**正确调用工具**、回答准不准。evals 补的正是这层。

## 运行形态两档（#55 定稿）

| 档位 | 内容 | 成本 | 运行方式 |
|---|---|---|---|
| 无 LLM 类 | ATOF 报告器（读 jsonl 出指标） | 免费 | pytest 集成 + CI 可跑（#59 接入） |
| 有 LLM 类 | 评测 harness（跑真实 agent） | 花钱 | **仅独立命令 + `--llm` 门控**，CI 不挂 |

## 模块结构

```
backend/
├── app/evals/report.py            # 无 LLM 类：ATOF 报告器（独立包，零依赖）
└── app/harness/evals/
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
uv run python -m app.evals.report                    # 默认读 logs/llm-requests/
uv run python -m app.evals.report <jsonl 文件或目录>
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

## 评测 harness（#57）

### 任务定义（任务文件，Python）

```python
# tasks/retrieval.py
from app.harness.evals.judge import ToolCalled, NoToolCalled

TASKS = [
    {"name": "retrieval_uses_search_history",
     "prompt": "帮我找上次讨论 session 旋转的内容",
     "judge": ToolCalled("search_history")},
    {"name": "no_fabrication_when_empty",
     "prompt": "查不到就直说",
     "judge": NoToolCalled()},
]
```

judge 组件：`ToolCalled(name, params=None)`（params 为参数子集匹配）/ `NoToolCalled()` / `MarkerInReply(marker)` / `AllOf(*j)` / `AnyOf(*j)`。

### 运行（有 LLM 类，花钱）

```bash
cd backend
# 单配置跑全部任务
uv run python -m app.harness.evals.cli --tasks tasks/ --llm

# baseline/candidate 对比（system_prompt 维度）+ bootstrap 置信度
uv run python -m app.harness.evals.cli --tasks tasks/ --llm \
    --baseline-prompt-file baseline.txt --candidate-prompt-file candidate.txt --reps 3
```

- **`--llm` 是门控**：不加直接退出，零 LLM 调用（CI 不会误触发花钱）
- 数据库默认独立评测库 `eval_lanyuan.db`（不碰开发/生产数据），`--db-url` 可覆盖
- 无 LLM key 时自动走 mock（`mock_chat`），管线零成本冒烟，但不产出真实结论

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
- ⏳ #58 手工测试集（检索/记忆/边界行为陷阱题，依赖 #57）
- ⏳ #59 CI 集成 + 报告可见（无 LLM 档 + PR 评论摘要 + Artifact 完整报告）

评测不修改生产代码，全部开发期离线运行。
