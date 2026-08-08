"""System Prompt 运行时组装（#11）单元测试

覆盖：
- PROMPT_SECTIONS 拆分为独立 section（identity / tools / memory / compression）
- 按需加载：memory_index 非空才注入，否则不注入
- session 粒度缓存：同 session 冻结（memory_index 变化不重组装）；不同 session 各自组装
- build_messages 的 session_id 参数路径
- SYSTEM_PROMPT 兼容常量（默认 context 组装结果）
- orm_to_canonical：ORM → canonical（review #53 tool_name 直读列 / 旧数据兜底匹配）
"""

import json

import pytest

from app.harness import context


@pytest.fixture(autouse=True)
def _clear_prompt_cache():
    """每个用例独立：清空 session 级 system prompt 缓存

    模块级 OrderedDict 跨用例共享——带 session_id 的用例写入缓存，
    不清空会让后续用例命中前序用例的冻结结果。单测必须隔离。
    """
    yield
    context._SESSION_PROMPT_CACHE.clear()


# ═══════════════════════════════════════════
#  assemble_system_prompt — section 拆分与按需加载
# ═══════════════════════════════════════════


class TestAssembleSections:
    def test_default_sections_all_present(self):
        """默认 context：identity/tools/memory/compression 全部注入"""
        prompt = context.assemble_system_prompt({})
        assert "你是兰园社区助手" in prompt  # identity
        assert "你可以使用的功能" in prompt  # tools
        assert "跨会话记忆" in prompt  # memory 说明
        assert "上下文压缩" in prompt  # compression

    def test_sections_joined_in_stable_order(self):
        """section 按 PROMPT_SECTIONS 定义顺序拼接（字节稳定前提）"""
        prompt = context.assemble_system_prompt({})
        idx_identity = prompt.index("你是兰园社区助手")
        idx_tools = prompt.index("你可以使用的功能")
        idx_memory = prompt.index("跨会话记忆")
        idx_compression = prompt.index("上下文压缩")
        assert idx_identity < idx_tools < idx_memory < idx_compression

    def test_memory_index_injected_when_non_empty(self):
        """memory_index 非空 → 追加索引段；无记忆 → 不注入"""
        prompt = context.assemble_system_prompt({"memory_index": "你的记忆索引：\n- [user] #1 用户名字"})
        assert "你的记忆索引：" in prompt
        assert "用户名字" in prompt

        prompt_no_memory = context.assemble_system_prompt({})
        assert "你的记忆索引：" not in prompt_no_memory

    def test_sections_independent(self):
        """section 独立维护：单独修改一个 section 不影响组装逻辑"""
        original = context.PROMPT_SECTIONS["tools"]
        try:
            context.PROMPT_SECTIONS["tools"] = "新工具说明"
            prompt = context.assemble_system_prompt({})
            assert "新工具说明" in prompt
            assert "你可以使用的功能" not in prompt
        finally:
            context.PROMPT_SECTIONS["tools"] = original
        # 恢复后组装不受影响
        assert "你可以使用的功能" in context.assemble_system_prompt({})


# ═══════════════════════════════════════════
#  get_system_prompt — 确定性缓存
# ═══════════════════════════════════════════


class TestGetSystemPromptCache:
    def test_same_session_byte_stable(self):
        """同 session → 返回同一对象（缓存命中，字节稳定 → 前缀缓存命中）"""
        p1 = context.get_system_prompt({"session_id": "s1"})
        p2 = context.get_system_prompt({"session_id": "s1"})
        assert p1 is p2
        assert p1 == p2

    def test_session_freeze_ignores_memory_change(self):
        """session 内 memory_index 变化 → 不重组装（冻结：key 不含 memory_index）"""
        p1 = context.get_system_prompt({"memory_index": "你的记忆索引：\n- [user] #1 用户名字", "session_id": "s1"})
        p2 = context.get_system_prompt({"memory_index": "你的记忆索引：\n- [user] #2 新记忆", "session_id": "s1"})
        assert p1 is p2  # 冻结：同 session 复用首次组装结果
        assert "用户名字" in p1  # 首次注入
        assert "新记忆" not in p1  # 后续变化被忽略（新记忆下个 session 生效）

    def test_different_sessions_assembled_independently(self):
        """不同 session → 各自组装，交替访问互不覆盖"""
        a1 = context.get_system_prompt({"memory_index": "A的记忆", "session_id": "s1"})
        b1 = context.get_system_prompt({"memory_index": "B的记忆", "session_id": "s2"})
        c1 = context.get_system_prompt({"session_id": "s3"})
        assert "A的记忆" in a1 and "B的记忆" not in a1
        assert "B的记忆" in b1 and "A的记忆" not in b1
        assert "你的记忆索引：" not in c1
        # 交替访问后各自命中
        assert context.get_system_prompt({"memory_index": "A的记忆", "session_id": "s1"}) is a1
        assert context.get_system_prompt({"memory_index": "B的记忆", "session_id": "s2"}) is b1
        assert context.get_system_prompt({"session_id": "s3"}) is c1

    def test_no_session_id_assembles_fresh(self):
        """无 session_id → 不缓存，每次现组装（幂等：内容一致，非同一对象）"""
        p1 = context.get_system_prompt({})
        p2 = context.get_system_prompt({})
        assert p1 == p2
        assert p1 is not p2  # 无缓存：每次新组装
        assert context.SYSTEM_PROMPT == p1  # 常量 = 默认组装结果（内容一致）

    def test_entries_never_evicted_without_invalidation(self):
        """#46：普通 dict 无 LRU/maxsize——条目不自动淘汰，均保留"""
        p1 = context.get_system_prompt({"memory_index": "m1", "session_id": "s1"})
        p2 = context.get_system_prompt({"memory_index": "m2", "session_id": "s2"})
        p3 = context.get_system_prompt({"memory_index": "m3", "session_id": "s3"})
        # 三个条目全部保留（无淘汰），交替访问各自命中
        assert context.get_system_prompt({"session_id": "s1"}) is p1
        assert context.get_system_prompt({"session_id": "s2"}) is p2
        assert context.get_system_prompt({"session_id": "s3"}) is p3

    def test_invalidate_session_prompt_removes_entry(self):
        """#46/#45：invalidate_session_prompt 精确清理指定 session 条目"""
        context.get_system_prompt({"memory_index": "m1", "session_id": "s1"})
        context.get_system_prompt({"memory_index": "m2", "session_id": "s2"})
        context.invalidate_session_prompt("s1")
        assert "s1" not in context._SESSION_PROMPT_CACHE
        assert "s2" in context._SESSION_PROMPT_CACHE  # 其他条目不受影响
        # 失效后重新组装（新对象）
        p_new = context.get_system_prompt({"memory_index": "m1", "session_id": "s1"})
        assert "m1" in p_new


# ═══════════════════════════════════════════
#  build_messages — 集成入口
# ═══════════════════════════════════════════


class TestBuildMessages:
    def _fake_msg(self, role: str, content: str, tool_call_id=None, tool_calls=None, tool_name=None):
        from types import SimpleNamespace

        return SimpleNamespace(
            role=role,
            content=content,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
            tool_name=tool_name,
        )

    def test_memory_index_still_supported(self):
        """memory_index 关键字参数兼容（#9 既有调用路径）"""
        index = "你的记忆索引：\n- [user] #1 用户名字"
        messages = context.build_messages(
            [self._fake_msg("user", "你好")],
            "你好",
            memory_index=index,
        )
        assert "你的记忆索引：" in messages[0]["content"]
        assert "用户名字" in messages[0]["content"]

    def test_session_id_freezes_system_across_calls(self):
        """同 session_id 连续组装 → system 冻结（复用首次结果）；不同 session → 各自组装"""
        fake = self._fake_msg("user", "你好")
        m1 = context.build_messages(
            [fake],
            "你好",
            memory_index="索引A",
            session_id="s1",
        )
        m2 = context.build_messages(
            [fake],
            "你好",
            memory_index="索引B",
            session_id="s1",
        )
        assert m1[0]["content"] is m2[0]["content"]  # 冻结：同 session 复用
        assert "索引A" in m1[0]["content"]
        assert "索引B" not in m1[0]["content"]

        m3 = context.build_messages(
            [fake],
            "你好",
            memory_index="索引C",
            session_id="s2",
        )
        assert "索引C" in m3[0]["content"]  # 新 session 用当轮记忆索引


class TestOrmToCanonical:
    """orm_to_canonical：DB ORM → canonical（review #53：tool_name 直读列，旧数据兜底匹配）"""

    def _msg(self, role, content="", tool_call_id=None, tool_calls=None, tool_name=None):
        from types import SimpleNamespace

        return SimpleNamespace(
            role=role, content=content,
            tool_call_id=tool_call_id, tool_calls=tool_calls, tool_name=tool_name,
        )

    def test_tool_name_reads_db_column_directly(self):
        """新数据：tool_name 列有值 → 直接读列，不依赖 assistant 消息"""
        history = [
            self._msg("user", "查天气"),
            self._msg("assistant", tool_calls=json.dumps([{
                "id": "call_1", "type": "function",
                "function": {"name": "get_weather", "arguments": "{}"},
            }])),
            self._msg("tool", "晴", tool_call_id="call_1", tool_name="get_weather"),
        ]
        out = context.orm_to_canonical(history)
        assert out[-1]["role"] == "toolResult"
        assert out[-1]["tool_name"] == "get_weather"

    def test_tool_name_fallback_to_assistant_match(self):
        """旧数据：tool_name 列为 NULL → 按 tool_call_id 从 assistant tool_calls 反向匹配"""
        history = [
            self._msg("assistant", tool_calls=json.dumps([{
                "id": "call_9", "type": "function",
                "function": {"name": "create_post", "arguments": "{}"},
            }])),
            self._msg("tool", "ok", tool_call_id="call_9", tool_name=None),
        ]
        out = context.orm_to_canonical(history)
        assert out[-1]["tool_name"] == "create_post"

    def test_tool_name_missing_stays_absent(self):
        """都匹配不到 → 不带 tool_name 键（ToolResultMessage NotRequired）"""
        history = [self._msg("tool", "ok", tool_call_id="call_x")]
        out = context.orm_to_canonical(history)
        assert "tool_name" not in out[-1]

    def test_plain_user_and_assistant_passthrough(self):
        """纯文本 user / assistant 透传（assistant 包 TextBlock）"""
        history = [
            self._msg("user", "你好"),
            self._msg("assistant", "你好呀"),
        ]
        out = context.orm_to_canonical(history)
        assert out[0] == {"role": "user", "content": "你好"}
        assert out[1] == {"role": "assistant", "content": [{"type": "text", "text": "你好呀"}]}


# ═══════════════════════════════════════════
#  SYSTEM_PROMPT 兼容常量
# ═══════════════════════════════════════════


def test_system_prompt_constant_is_default_assembly():
    """兼容常量 = 默认 context 组装结果，旧引用（测试/调用方）不破坏"""
    assert context.SYSTEM_PROMPT == context.get_system_prompt({})
    assert "你是兰园社区助手" in context.SYSTEM_PROMPT
    assert "memory_add" in context.SYSTEM_PROMPT  # memory 说明 section
    assert "不要告诉用户「我可以帮你记住」" in context.SYSTEM_PROMPT
    assert "你的记忆索引：" not in context.SYSTEM_PROMPT  # 无记忆时不注入索引
