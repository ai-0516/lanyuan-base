"""System Prompt 运行时组装（#11）单元测试

覆盖：
- PROMPT_SECTIONS 拆分为独立 section（identity / tools / memory / compression / workspace）
- 按需加载：memory_index / workspace 非空才注入，否则不注入
- 确定性缓存：同 context → 字节稳定（前缀缓存命中前提）；context 变化 → 重组装
- 多 context 交替（多用户/多会话）互不覆盖
- build_deepseek_messages 的 workspace 可选参数路径
- SYSTEM_PROMPT 兼容常量（默认 context 组装结果）
"""

from app.harness import context


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
        assert "当前对话上下文" not in prompt  # workspace 默认不注入

    def test_sections_joined_in_stable_order(self):
        """section 按 _SECTION_ORDER 稳定序拼接（字节稳定前提）"""
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

    def test_workspace_injected_when_non_empty(self):
        """workspace 非空 → 注入上下文段；空 → 不注入"""
        prompt = context.assemble_system_prompt({"workspace": "管理后台数据维护"})
        assert "当前对话上下文：管理后台数据维护" in prompt

        prompt_no_ws = context.assemble_system_prompt({"workspace": ""})
        assert "当前对话上下文" not in prompt_no_ws

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
    def test_same_context_byte_stable(self):
        """同 context → 返回同一对象（缓存命中，字节稳定 → 前缀缓存命中）"""
        ctx = {"memory_index": "你的记忆索引：\n- [user] #1 用户名字"}
        p1 = context.get_system_prompt(ctx)
        p2 = context.get_system_prompt(ctx)
        assert p1 is p2
        assert p1 == p2

    def test_context_change_invalidates_cache(self):
        """context 变化（记忆新增/会话切换）→ 重组装"""
        p1 = context.get_system_prompt({})
        p2 = context.get_system_prompt({"memory_index": "你的记忆索引：\n- [user] #1 用户名字"})
        p3 = context.get_system_prompt({})
        assert p2 != p1
        # 空 context 恢复后命中此前缓存（同一对象）
        assert p3 is p1

    def test_multi_context_alternating_no_override(self):
        """多 context 交替（多用户/多会话）→ 各自命中，互不覆盖"""
        ctx_a = {"memory_index": "你的记忆索引：\n- [user] #1 A的记忆"}
        ctx_b = {"memory_index": "你的记忆索引：\n- [user] #2 B的记忆"}
        ctx_c = {}
        a1 = context.get_system_prompt(ctx_a)
        b1 = context.get_system_prompt(ctx_b)
        c1 = context.get_system_prompt(ctx_c)
        # 交替访问后仍各自命中缓存
        assert context.get_system_prompt(ctx_a) is a1
        assert context.get_system_prompt(ctx_b) is b1
        assert context.get_system_prompt(ctx_c) is c1
        assert "A的记忆" in a1 and "B的记忆" not in a1
        assert "B的记忆" in b1 and "A的记忆" not in b1


# ═══════════════════════════════════════════
#  build_deepseek_messages — 集成入口
# ═══════════════════════════════════════════


class TestBuildDeepseekMessages:
    def _fake_msg(self, role: str, content: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            role=role,
            content=content,
            tool_call_id=None,
            tool_calls=None,
        )

    def test_workspace_param_injected(self):
        """build_deepseek_messages 支持 workspace 可选参数"""
        messages = context.build_deepseek_messages(
            [self._fake_msg("user", "你好")],
            "你好",
            workspace="管理后台数据维护",
        )
        assert "当前对话上下文：管理后台数据维护" in messages[0]["content"]

    def test_workspace_default_empty(self):
        """默认不传 workspace → 不注入 workspace 段"""
        messages = context.build_deepseek_messages([self._fake_msg("user", "你好")], "你好")
        assert "当前对话上下文" not in messages[0]["content"]

    def test_memory_index_still_supported(self):
        """memory_index 关键字参数兼容（#9 既有调用路径）"""
        index = "你的记忆索引：\n- [user] #1 用户名字"
        messages = context.build_deepseek_messages(
            [self._fake_msg("user", "你好")],
            "你好",
            memory_index=index,
        )
        assert "你的记忆索引：" in messages[0]["content"]
        assert "用户名字" in messages[0]["content"]


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


# ═══════════════════════════════════════════
#  PROMPT_SECTIONS 运行时修改 → 缓存失效（review #39 严重问题修复）
# ═══════════════════════════════════════════


class TestSectionsChangeInvalidatesCache:
    """PROMPT_SECTIONS 运行时修改必须使 get_system_prompt 缓存失效

    复现 dev-lead 实测场景：改 section 后 get_system_prompt 仍返回旧内容
    （原实现 lru_cache 只按 context 做 key，不感知 section 变化）。
    修复：sections_digest 并入缓存 key。
    """

    def test_section_change_invalidates_cached_prompt(self):
        """修改 PROMPT_SECTIONS → get_system_prompt 返回新内容（缓存失效）"""
        original = context.PROMPT_SECTIONS["identity"]
        try:
            p1 = context.get_system_prompt({})
            assert "你是兰园社区助手" in p1

            # 运行时修改 section（换角色/换场景热更新场景）
            context.PROMPT_SECTIONS["identity"] = "新角色：你是测试助手"
            p2 = context.get_system_prompt({})

            assert "新角色：你是测试助手" in p2  # 修复前：仍返回旧内容（bug）
            assert "你是兰园社区助手" not in p2
        finally:
            context.PROMPT_SECTIONS["identity"] = original

        # 恢复后回到旧内容（缓存随摘要自动失效）
        p3 = context.get_system_prompt({})
        assert "你是兰园社区助手" in p3
        assert "新角色" not in p3

    def test_section_change_keeps_context_distinction(self):
        """section 变化后，不同 context 仍各自独立命中（互不串扰）"""
        original = context.PROMPT_SECTIONS["identity"]
        ctx_a = {"memory_index": "你的记忆索引：\n- [user] #1 A的记忆"}
        try:
            a1 = context.get_system_prompt(ctx_a)

            context.PROMPT_SECTIONS["identity"] = "新角色：你是测试助手"
            a2 = context.get_system_prompt(ctx_a)

            assert "A的记忆" in a2
            assert "新角色" in a2
        finally:
            context.PROMPT_SECTIONS["identity"] = original
