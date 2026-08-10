"""评测（evals）模块 — 开发期 agent 行为评测（#55 调研定稿）

- report.py: ATOF 报告器（#56）——无 LLM 类评测，读 jsonl 出行为指标，CI 可跑
- judge.py: 确定性断言组件（#57）
- harness.py: 评测执行器 + baseline/candidate 对比 + bootstrap（#57）
- cli.py: 有 LLM 类评测入口，--llm 门控（#57）

分档（#55 定稿）：无 LLM 类（report）进 pytest/CI；有 LLM 类（harness/cli）仅手动 + --llm 门控。
"""
