"""评测任务示例 — 展示任务文件长什么样（PR #61 review 补充 sample 数据）

任务文件 = 纯数据（prompt + judge 断言），load_tasks() 扫描目录/文件里的 TASKS 列表加载。
设计（#55 定稿）：测试样本（prompt）与判定逻辑（judge）分离，判定一律确定性断言。
任务元素用 Task dataclass 构造（字段：name / prompt / judge / system_prompt?）。

运行方式：
    # 无 LLM 门控提示（不会产生任何 LLM 调用）
    python -m app.harness.evals.cli --tasks tests/data/sample_tasks.py
    # 真实评测（花钱）：mock 模式下 agent 不调工具，ToolCalled 类任务会 FAIL 属预期
    python -m app.harness.evals.cli --tasks tests/data/sample_tasks.py --llm
"""

from app.harness.evals.harness import Task
from app.harness.evals.judge import AllOf, AnyOf, MarkerInReply, NoToolCalled, ToolCalled

TASKS = [
    Task(
        # 最简单：确认 agent 对问候不胡乱调工具（边界行为）
        name="greeting_no_tool",
        prompt="你好",
        judge=NoToolCalled(),
    ),
    Task(
        # 断言调用了指定工具（参数子集匹配：post_id 必须等于 1）
        name="get_post_by_id",
        prompt="帮我查一下 id 为 1 的帖子内容",
        judge=ToolCalled("get_post", {"post_id": 1}),
    ),
    Task(
        # 参数匹配失败示例：断言 post_id=2，实际调用了 post_id=1 → FAIL
        name="get_post_wrong_param",
        prompt="帮我查一下 id 为 1 的帖子内容",
        judge=ToolCalled("get_post", {"post_id": 2}),
    ),
    Task(
        # 组合断言：先调搜索工具，再在最终回复里带上搜索关键词（完整流程）
        name="search_and_mention",
        prompt="搜索一下「兰园」相关的历史消息，然后告诉我",
        judge=AllOf(
            ToolCalled("search_history", {"query": "兰园"}),
            MarkerInReply("兰园"),
        ),
    ),
    Task(
        # 任一断言通过即可：mock 模式回复固定含「模拟模式」→ 本任务 PASS
        name="either_tool_or_marker",
        prompt="随便说点什么",
        judge=AnyOf(
            ToolCalled("get_post"),
            MarkerInReply("模拟模式"),
        ),
    ),
]
