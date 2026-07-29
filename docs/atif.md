# ATIF — Agent Trajectory Interchange Format

## 概述

ATIF（Agent Trajectory Interchange Format）是 NVIDIA 与 Harbor 联合推动的 Agent 轨迹交换格式，旨在标准化多轮 tool-call Agent 对话的结构化表示，用于离线分析、重放、评估和训练数据收集。

配套格式 **ATOF**（Agent Trajectory Observability Format）是 JSONL 事件流，记录 agent 运行时产生的原始事件，ATIF 由 ATOF 事件聚合而成。

> **定位：** 专门为 **Agent 重放和评估**设计的结构化轨迹交换格式，非通用可观测性方案。

---

## 发展历程

| 时间 | 事件 |
|------|------|
| 2026-04 初 | ATIF RFC 提交至 Harbor 仓库 |
| 2026-04-27 | ATIF v1.7 发布：子 agent 轨迹、Step extra、上下文管理 |
| 2026-07-15 | Provenance-aware 决策事件讨论（轨迹保留推理过程） |
| 2026-07-17 | Codex / Qwen 多 agent 轨迹修复 |
| 2026-07-22 | NeMo Relay 0.6.0 发布：首次稳定支持 ATOF + ATIF |
| 2026-07-28 | Hermes Agent 集成 NeMo Relay |

---

## 所属组织

| 产品/格式 | 所属 | 说明 |
|-----------|------|------|
| **ATIF / ATOF** | NVIDIA + Harbor | Harbor 是前 Meta AI 研究员主导的开源 Agent 评估框架，后被 NVIDIA 收编 |
| **NeMo Relay** | NVIDIA | ATIF/ATOF 的运行时宿主，NVIDIA NeMo 生态的 Agent 可观测性层 |

---

## ATIF 的核心能力

### 结构化轨迹表示

将一次完整的多轮 Agent 对话表示为 JSON，包含：

```
turn-by-turn 的工具调用输入/输出​
完整的 usage 和 timing 元数据​
子 agent 轨迹嵌入（subagent trajectory embedding）​
确定的非 LLM 编排步骤​
轨迹 ID 用于跨系统关联
```

### 服务于 Agent 评估

Harbor 评估框架使用 ATIF 做 side-by-side 对比：

```
同一 task → 模型 A 跑一次 → ATIF_A.json
          → 模型 B 跑一次 → ATIF_B.json
          → 对比两路径的差异（哪一步决策不同、哪个工具选错）
```

### 服务于 Agent 训练

ATIF 轨迹天然可作为训练数据：

| 用途 | 说明 |
|------|------|
| SFT 训练数据 | 多轮 tool call 轨迹 → 微调 base model |
| RL/RLHF rollout | 轨迹作为 rollout，用 AI feedback 做强化学习 |
| Process Reward Model | 逐轮标注中间 reward（哪一步做对了/错了） |

---

## 竞争格局

ATIF 在"Agent 重放和评估用的结构化轨迹交换格式"这一细分领域目前没有直接竞品。

| 产品 | 能做轨迹重放 | 开放标准 | 定位差异 |
|------|:---:|:--------:|---------|
| **ATIF** | ✅ 原生设计 | 开源 RFC | Agent 评估+重放的交换格式 |
| OpenTelemetry GenAI | ❌ | 开放 | 通用可观测性 trace，非重放设计 |
| LangFuse | 需二次加工 | 自家格式 | LLM 应用可观测性 SaaS |
| LangSmith | 需二次加工 | 自家格式 | LangChain 生态商业产品 |
| Weights & Biases | 有限 | 自家格式 | ML 实验追踪 |

---

## 为什么 Agent 训练侧是强需求

**大模型从业者对 agentic training 的需求正在推动这一方向：**

```
ATIF 标准化轨迹 → 规模化收集 → Agent 训练数据 → 更好的 Agent 模型
                                 ↓
                    SFT / RLHF / PRM 全覆盖
```

1. **SFT（监督微调）** — 需要大量"好"的多轮 tool call 轨迹。公开数据集极少，ATIF 让各组织能交换和共享轨迹数据
2. **RL/RLHF** — 轨迹作为 rollout，AI feedback 做强化学习的 reward 信号
3. **PRM（Process Reward Model）** — 逐轮判断工具调用对错，需要每步标注的轨迹
4. **评估** — Side-by-side 跑同一组 task，ATIF 轨迹精确对比"模型在哪个 turn 做了不同决策"

---

## 参考资料

- [Harbor RFC 0001 — Trajectory Format](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md)
- [NeMo Relay — NVIDIA](https://github.com/NVIDIA/NeMo-Relay)
- [ATOF Event Format](https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/develop/packages/nvidia_nat_atif/atof-event-format.md)
