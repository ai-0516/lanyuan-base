"""评测首批测试集测试（#58）

覆盖：
- jsonl 任务加载（expect 翻译、注释/空行跳过、缺字段/未知 expect 报错、.py+.jsonl 混合目录）
- ToolCalled 参数 list 值 = 包含匹配（检索参数合理性断言）
- DBMemoryContains DB 状态检查（记忆落库验证）
- CLI _reset_eval_user 每轮重置（可复现性）
- 真实测试集目录（app/harness/evals/tasks/*.jsonl）可加载、题目覆盖三维度
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.harness.evals.harness import _expect_to_judge, load_tasks
from app.harness.evals.judge import (
    AgentTrace,
    AllOf,
    AnyOf,
    DBMemoryContains,
    EvalContext,
    MarkerInReply,
    NoToolCalled,
    ToolCall,
    ToolCalled,
)
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.models.user_memory import UserMemory

TASKS_DIR = Path(__file__).parent.parent / "app" / "harness" / "evals" / "tasks"


def _write_jsonl(tmp_path: Path, lines: list[str]) -> Path:
    f = tmp_path / "tasks.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


@pytest.fixture
async def tmp_db(tmp_path):
    """临时 sqlite 库（建全部表），返回 async_sessionmaker"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/db.sqlite")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


# ── jsonl 任务加载 ───────────────────────────────────────────────


def test_load_tasks_from_jsonl_file(tmp_path):
    f = _write_jsonl(tmp_path, [
        '{"name": "a", "prompt": "p1", "expect": {"tool": "search_history"}}',
        '{"name": "b", "prompt": "p2", "expect": {"no_tool": true}}',
    ])
    tasks = load_tasks(f)
    assert [t.name for t in tasks] == ["a", "b"]
    assert isinstance(tasks[0].judge, ToolCalled)
    assert isinstance(tasks[1].judge, NoToolCalled)


def test_load_tasks_jsonl_skips_comments_and_blanks(tmp_path):
    f = _write_jsonl(tmp_path, [
        "# 注释行：本文件是检索陷阱题",
        "",
        '{"name": "a", "prompt": "p", "expect": {"marker": "x"}}',
    ])
    tasks = load_tasks(f)
    assert [t.name for t in tasks] == ["a"]


def test_load_tasks_jsonl_missing_field_raises(tmp_path):
    f = _write_jsonl(tmp_path, ['{"name": "a", "prompt": "p"}'])
    with pytest.raises(ValueError, match="缺少字段"):
        load_tasks(f)


def test_load_tasks_jsonl_unknown_expect_raises(tmp_path):
    f = _write_jsonl(tmp_path, [
        '{"name": "a", "prompt": "p", "expect": {"hack": true}}',
    ])
    with pytest.raises(ValueError, match="无法识别"):
        load_tasks(f)


def test_load_tasks_dir_mixes_py_and_jsonl(tmp_path):
    (tmp_path / "a.py").write_text(
        "from app.harness.evals.judge import NoToolCalled\n"
        'TASKS = [{"name": "py1", "prompt": "p", "judge": NoToolCalled()}]\n',
        encoding="utf-8",
    )
    (tmp_path / "b.jsonl").write_text(
        '{"name": "j1", "prompt": "p", "expect": {"no_tool": true}}\n',
        encoding="utf-8",
    )
    tasks = load_tasks(tmp_path)
    # 按文件名排序：a.py 先于 b.jsonl
    assert [t.name for t in tasks] == ["py1", "j1"]


# ── expect 翻译 ─────────────────────────────────────────────────


def test_build_messages_includes_user_message():
    """回归（#58 实测 0/6 根因）：build_messages 不拼 user 消息（生产由 DB
    history 提供），评测无 history，_build_messages 必须显式追加，
    否则 LLM 只收到 system、回复与 prompt 无关。"""
    from app.harness.evals.harness import _build_messages

    msgs = _build_messages("帮我找 session 旋转", "t1", system_prompt=None)
    assert msgs[-1] == {"role": "user", "content": "帮我找 session 旋转"}
    assert msgs[0]["role"] == "system"
    # system_prompt 覆盖分支同样含 user 消息
    msgs2 = _build_messages("帮我找 session 旋转", "t1", system_prompt="自定义")
    assert msgs2 == [
        {"role": "system", "content": "自定义"},
        {"role": "user", "content": "帮我找 session 旋转"},
    ]


def test_expect_translation_all_types():
    j_tool = _expect_to_judge({"tool": "memory_add", "params": {"body": ["美式"]}})
    assert isinstance(j_tool, ToolCalled)
    assert j_tool.params == {"body": ["美式"]}
    assert isinstance(_expect_to_judge({"tool": "memory_add"}), ToolCalled)
    assert isinstance(_expect_to_judge({"no_tool": True}), NoToolCalled)
    assert isinstance(_expect_to_judge({"marker": "没有"}), MarkerInReply)
    assert isinstance(_expect_to_judge({"db_memory_contains": "美式"}), DBMemoryContains)
    assert isinstance(_expect_to_judge({"all": [{"no_tool": True}]}), AllOf)
    assert isinstance(_expect_to_judge({"any": [{"marker": "a"}, {"marker": "b"}]}), AnyOf)


def test_expect_translation_nested_combo():
    """嵌套组合：all(any(tool), marker) 递归翻译"""
    exp = {
        "all": [
            {"any": [{"tool": "memory_list"}, {"tool": "memory_get"}]},
            {"marker": "没有"},
        ]
    }
    j = _expect_to_judge(exp)
    assert isinstance(j, AllOf)
    assert isinstance(j.judges[0], AnyOf)
    assert isinstance(j.judges[1], MarkerInReply)


def test_expect_translation_multiple_keys_raises():
    """#66 review：判定键互斥，组合必须用 all/any 显式表达（避免静默忽略）"""
    with pytest.raises(ValueError, match="互斥"):
        _expect_to_judge({"tool": "memory_add", "marker": "已记住"})


# ── ToolCalled 参数 list 包含匹配 ────────────────────────────────


async def test_tool_called_param_list_contains():
    j = ToolCalled("search_history", {"query": ["旋转"]})
    # 实际 query 包含关键词 → 通过
    trace = AgentTrace(prompt="p")
    trace.tool_calls.append(
        ToolCall(name="search_history", arguments={"query": "帮我找 session 旋转方案"})
    )
    assert (await j.check(EvalContext(trace=trace))).passed
    # 实际 query 不含关键词 → 失败
    trace2 = AgentTrace(prompt="p")
    trace2.tool_calls.append(
        ToolCall(name="search_history", arguments={"query": "春游安排"})
    )
    r = await j.check(EvalContext(trace=trace2))
    assert not r.passed
    assert "参数不匹配" in r.reason


async def test_tool_called_exact_match_unchanged():
    """非 list 值仍为精确匹配（#57 既有语义不回退）"""
    trace = AgentTrace(prompt="p")
    trace.tool_calls.append(ToolCall(name="get_post", arguments={"post_id": 1}))
    assert (await ToolCalled("get_post", {"post_id": 1}).check(EvalContext(trace=trace))).passed
    r = await ToolCalled("get_post", {"post_id": 2}).check(EvalContext(trace=trace))
    assert not r.passed


# ── DBMemoryContains DB 状态检查 ────────────────────────────────


async def test_db_memory_contains_found(tmp_db):
    async with tmp_db() as session:
        session.add(
            UserMemory(user_id=7, name="coffee", type="user",
                       description="喜欢美式咖啡", body="用户喜欢喝美式咖啡")
        )
        await session.commit()
        r = await DBMemoryContains("美式").check(
            EvalContext(trace=AgentTrace(prompt="p"), db=session, user_id=7)
        )
        assert r.passed
        assert "美式" in r.reason


async def test_db_memory_contains_not_found(tmp_db):
    async with tmp_db() as session:
        # 用户 8 无记忆 / 用户 7 有但不含关键词
        session.add(
            UserMemory(user_id=7, name="tea", type="user",
                       description="喜欢奶茶", body="用户喜欢喝奶茶")
        )
        await session.commit()
        r = await DBMemoryContains("美式").check(
            EvalContext(trace=AgentTrace(prompt="p"), db=session, user_id=7)
        )
        assert not r.passed


async def test_db_memory_contains_without_db_context():
    r = await DBMemoryContains("x").check(EvalContext(trace=AgentTrace(prompt="p")))
    assert not r.passed
    assert "无 db" in r.reason


# ── CLI _reset_eval_user（可复现性） ─────────────────────────────


async def test_reset_eval_user_clears_data(tmp_db):
    from app.harness.evals.cli import _reset_eval_user

    async with tmp_db() as session:
        session.add(User(openid="eval", nickname="评测用户"))
        await session.commit()
        user = (await session.execute(
            select(User).where(User.openid == "eval")
        )).scalar_one()
        conv = Conversation(user_id=user.id, title="上轮对话")
        session.add(conv)
        await session.commit()
        session.add(Message(conversation_id=conv.id, role="user", content="上轮消息"))
        session.add(
            UserMemory(user_id=user.id, name="old", type="user",
                       description="上轮记忆", body="上轮写入的记忆")
        )
        await session.commit()

        await _reset_eval_user(session, user.id)

        assert (await session.execute(
            select(Conversation).where(Conversation.user_id == user.id)
        )).scalars().all() == []
        assert (await session.execute(select(Message))).scalars().all() == []
        assert (await session.execute(
            select(UserMemory).where(UserMemory.user_id == user.id)
        )).scalars().all() == []
        # 用户本身保留（评测用户是常驻的）
        assert (await session.execute(
            select(User).where(User.openid == "eval")
        )).scalar_one() is not None


async def test_reset_eval_user_rejects_non_eval_user(tmp_db):
    """#66 review：只允许重置评测用户（openid=eval），拒绝误删正常用户数据"""
    from app.harness.evals.cli import _reset_eval_user

    async with tmp_db() as session:
        session.add(User(openid="wx_normal_user", nickname="正常用户"))
        await session.commit()
        normal = (await session.execute(
            select(User).where(User.openid == "wx_normal_user")
        )).scalar_one()
        conv = Conversation(user_id=normal.id, title="正常用户的对话")
        session.add(conv)
        await session.commit()

        with pytest.raises(ValueError, match="openid=eval"):
            await _reset_eval_user(session, normal.id)

        # 拒绝时未删除任何数据（正常用户及其对话都保留）
        assert (await session.execute(
            select(User).where(User.openid == "wx_normal_user")
        )).scalar_one() is not None
        assert (await session.execute(
            select(Conversation).where(Conversation.user_id == normal.id)
        )).scalars().all()


# ── 真实测试集目录 ──────────────────────────────────────────────


def test_load_real_tasks_dir():
    """真实测试集可加载，题目覆盖检索/记忆/边界三维度（各 2 题）"""
    tasks = load_tasks(TASKS_DIR)
    names = [t.name for t in tasks]
    assert len(names) == 6, names
    for t in tasks:
        assert callable(getattr(t.judge, "check", None)), t.name
    expected = {
        # 检索正确性
        "search_rotation_session",
        "search_lanyuan_topic",
        # 记忆正确性
        "memory_add_preference",
        "memory_ask_without_fabrication",
        # 边界行为
        "no_fabrication_empty_search",
        "domain_question_no_tool",
    }
    assert expected.issubset(set(names))
