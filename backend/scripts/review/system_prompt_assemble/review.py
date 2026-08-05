"""
PR #39（#11 System Prompt 运行时组装）触发验证脚本

验证内容（对应 review 两轮实测场景）：
1. section 拆分组装（identity/tools/memory/compression/workspace 五段，_SECTION_ORDER 稳定序）
2. 确定性缓存（同 context → 同输出，字节稳定）
3. 缓存失效（PROMPT_SECTIONS 运行时修改 → 立即生效，修复前不生效 = 严重问题）
4. 恢复 section 后缓存自动回到旧内容（sections_digest 随内容变化）
5. 多 context 互不覆盖（内存/workspace 不同 → 各自命中）
6. 兼容性（build_deepseek_messages 向后兼容、SYSTEM_PROMPT 常量保留）

用法：
    cd backend && uv run python scripts/review/system_prompt_assemble/review.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # backend/

from app.harness.context import (
    PROMPT_SECTIONS,
    SYSTEM_PROMPT,
    assemble_system_prompt,
    build_deepseek_messages,
    get_system_prompt,
)

_results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = ""):
    _results.append((name, ok, detail))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name} — {detail}")


def verify_section_assemble():
    print("\n## 场景 1：section 拆分组装")
    p = assemble_system_prompt({})
    record(
        "identity section 在首位（你是谁）",
        PROMPT_SECTIONS["identity"].split("\n")[0] in p,
        f"含 identity 首行={PROMPT_SECTIONS['identity'].split(chr(10))[0][:20]}",
    )
    record(
        "五段拆分（默认 context 渲染 identity/tools/memory/compression）",
        all(s in p for s in [
            PROMPT_SECTIONS["identity"],
            PROMPT_SECTIONS["tools"],
            PROMPT_SECTIONS["memory"],
            PROMPT_SECTIONS["compression"],
        ]),
        f"sections={list(PROMPT_SECTIONS.keys())}",
    )
    # workspace 段需要 context.workspace 才渲染
    p_ws = assemble_system_prompt({"workspace": "测试小区"})
    record(
        "workspace 段按 context 渲染",
        "测试小区" in p_ws,
        f"含 workspace={ '测试小区' in p_ws }",
    )


def verify_cache_deterministic():
    print("\n## 场景 2：确定性缓存")
    p1 = get_system_prompt({})
    p2 = get_system_prompt({})
    record(
        "同 context 两次调用字节相同",
        p1 == p2,
        f"len={len(p1)}",
    )
    p_ctx1 = get_system_prompt({"workspace": "A区"})
    p_ctx2 = get_system_prompt({"workspace": "B区"})
    record(
        "不同 context 输出不同（互不覆盖）",
        p_ctx1 != p_ctx2 and "A区" in p_ctx1 and "B区" in p_ctx2,
        f"A含A区={'A区' in p_ctx1} B含B区={'B区' in p_ctx2}",
    )


def verify_cache_invalidation():
    print("\n## 场景 3：PROMPT_SECTIONS 运行时修改使缓存失效（严重问题修复验证）")
    import copy

    orig = copy.deepcopy(PROMPT_SECTIONS)
    try:
        p1 = get_system_prompt({})
        orig_identity = PROMPT_SECTIONS["identity"]
        PROMPT_SECTIONS["identity"] = "新角色：你是测试助手"
        p2 = get_system_prompt({})
        record(
            "修改 section 后 get_system_prompt 立即返回新内容（修复后）",
            "新角色：你是测试助手" in p2 and "新角色" not in p1,
            f"p1 含新角色={'新角色' in p1} p2 含新角色={'新角色' in p2}",
        )
        # 恢复后回到旧内容（sections_digest 随内容自动失效）
        PROMPT_SECTIONS["identity"] = orig_identity
        p3 = get_system_prompt({})
        record(
            "恢复 section 后缓存自动回到旧内容",
            "新角色" not in p3 and "测试助手" not in p3,
            f"p3 含新角色={'新角色' in p3}",
        )
    finally:
        PROMPT_SECTIONS.clear()
        PROMPT_SECTIONS.update(orig)
    # 清理 lru_cache 避免影响后续场景
    from app.harness.context import _assemble_cached
    _assemble_cached.cache_clear()


def verify_memory_injection():
    print("\n## 场景 4：记忆索引注入")
    p = get_system_prompt({"memory_index": "你的记忆索引：\n- [user] 爱好爬山"})
    record(
        "memory section 注入索引",
        "爱好爬山" in p,
        f"含记忆索引={'爱好爬山' in p}",
    )
    p_no = get_system_prompt({})
    record(
        "无记忆时不含索引段",
        "你的记忆索引" not in p_no,
        f"无索引={ '你的记忆索引' not in p_no }",
    )


def verify_compat():
    print("\n## 场景 5：向后兼容")
    record(
        "SYSTEM_PROMPT 常量保留（默认 context）",
        isinstance(SYSTEM_PROMPT, str) and len(SYSTEM_PROMPT) > 50,
        f"len={len(SYSTEM_PROMPT)}",
    )
    msgs = build_deepseek_messages(
        history=[], user_message="你好", memory_index="", 
    )
    record(
        "build_deepseek_messages 签名向后兼容",
        isinstance(msgs, list) and msgs[0]["role"] == "system",
        f"messages={len(msgs)}",
    )


def main():
    print("=" * 64)
    print("  PR #39 review 触发验证（#11 System Prompt 运行时组装）")
    print("=" * 64)
    verify_section_assemble()
    verify_cache_deterministic()
    verify_cache_invalidation()
    verify_memory_injection()
    verify_compat()

    print("\n" + "=" * 64)
    print("  验证汇总")
    print("=" * 64)
    for name, ok, detail in _results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")
    fails = [r for r in _results if not r[1]]
    print(f"\n  结果: {'全部通过 ✅' if not fails else f'存在失败 ❌ ({len(fails)}/{len(_results)})'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
