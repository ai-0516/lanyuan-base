"""
issue #103（小区知识库 wiki）校验脚本 — 固化开发期手工校验 + review 建议

校验内容：
1. 结构 lint：index.md 覆盖与 pages/ 一致；wikilinks 无断链（排除代码块内）；
   无孤儿页（入链 > 0）；每页 >= 1 出链；frontmatter 五键齐全且 sources 指向存在的 raw
2. raw 溯源：sha256 复算（= 去 frontmatter 后正文 hex）与声明一致，源漂移可检出
3. 条款引用核对：页面引用的「第 X 条」必须存在于其 sources 指向的 raw 正文
   （法规类 raw 适用；非法规 raw 无条款则跳过）
4. 镜像构建校验：.dockerignore 未排除 docs/wiki 且 Dockerfile 含 COPY docs/wiki
   ——防止「知识源随镜像内置」声明与构建配置再次脱节（PR #104 R1 严重问题回归）

用法：
    cd backend && uv run python scripts/review/wiki_kb/review.py
无第三方依赖（纯 stdlib）。wiki 位置固定为仓库根 docs/wiki/。
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]  # backend/scripts/review/wiki_kb/review.py -> repo root
WIKI = REPO_ROOT / "docs" / "wiki"
PAGES = WIKI / "pages"
RAW = WIKI / "raw"

# frontmatter 必需键（SCHEMA.md 约定）
REQUIRED_FM_KEYS = ["title", "created", "updated", "type", "sources"]
ALLOWED_TYPES = ("regulation", "community", "guide")

_results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = ""):
    _results.append((name, ok, detail))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name} — {detail}")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (frontmatter dict, body 文本)。无 frontmatter 时返回 ({}, 全文)。"""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    fm = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, parts[2]


def strip_code_blocks(text: str) -> str:
    """去掉 ``` 代码块，避免块内 [[wikilinks]] 示例被误判为真实链接。"""
    return re.sub(r"```[\s\S]*?```", "", text)


def find_wikilinks(text: str) -> set[str]:
    return set(re.findall(r"\[\[([^\]]+)\]\]", strip_code_blocks(text)))


def verify_structure():
    print("\n## 场景 1：结构 lint")
    pages = {p.stem: p for p in PAGES.glob("*.md")}
    index_text = strip_code_blocks((WIKI / "index.md").read_text(encoding="utf-8"))
    indexed = set(re.findall(r"\[\[([^\]]+)\]\]", index_text))

    record(
        "index.md 列出页面与 pages/ 目录一致",
        indexed == set(pages),
        f"pages={sorted(pages)} index={sorted(indexed)}",
    )

    # 断链 + 出链数
    outbound: dict[str, set[str]] = {}
    for slug, pf in pages.items():
        outbound[slug] = find_wikilinks(pf.read_text(encoding="utf-8"))
    broken = [(s, l) for s, links in outbound.items() for l in links if l not in pages]
    record("无断链（wikilink 目标均存在）", not broken, f"broken={broken}" if broken else "OK")

    # 孤儿页（无入链）
    inbound: dict[str, int] = {s: 0 for s in pages}
    for links in outbound.values():
        for l in links:
            if l in inbound:
                inbound[l] += 1
    orphans = [s for s, n in inbound.items() if n == 0]
    record("无孤儿页（每页均有入链）", not orphans, f"orphans={orphans}" if orphans else "OK")

    # 每页 >= 1 出链（SCHEMA 约定）
    no_out = [s for s, links in outbound.items() if not links]
    record("每页 >= 1 出链", not no_out, f"no_outbound={no_out}" if no_out else "OK")

    # frontmatter 完整性
    fm_issues = []
    for slug, pf in pages.items():
        fm, _ = parse_frontmatter(pf.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_FM_KEYS if k not in fm]
        bad_type = fm.get("type") not in ALLOWED_TYPES
        bad_src = [s for s in re.findall(r"raw/[^\]]+", fm.get("sources", "")) if not (RAW / s[4:]).exists()]
        if missing or bad_type or bad_src:
            fm_issues.append(f"{slug}: missing={missing} type={fm.get('type')} bad_src={bad_src}")
    record("frontmatter 五键齐全 / type 合法 / sources 指向存在 raw", not fm_issues,
           "; ".join(fm_issues) if fm_issues else "6 pages OK")


def verify_raw_sha256():
    print("\n## 场景 2：raw 源文件 sha256 溯源")
    raw_files = list(RAW.glob("*.md"))
    record("raw/ 存在源文件", bool(raw_files), f"files={[p.name for p in raw_files]}")
    import hashlib

    issues = []
    for rf in raw_files:
        fm, body = parse_frontmatter(rf.read_text(encoding="utf-8"))
        declared = fm.get("sha256", "")
        actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if declared != actual:
            issues.append(f"{rf.name}: declared={declared[:12]} actual={actual[:12]}")
    record("raw frontmatter sha256 与正文一致（可复算）", not issues, "; ".join(issues) if issues else "OK")


def cn_article_pattern():
    # 兼容中文数字（第二十六条）与阿拉伯数字（第 26 条）
    return re.compile(r"第\s*([一二三四五六七八九十百零0-9]+)\s*条")


# 中文数字 → 阿拉伯数字（1-99，法规条款足够）
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_num(s: str) -> str:
    """'二十六' -> '26'；已是纯数字原样返回。"""
    if s.isdigit():
        return s
    if "十" not in s:
        return str(sum(_CN_DIGITS.get(ch, 0) for ch in s))
    tens, _, rest = s.partition("十")
    t = _CN_DIGITS.get(tens, 1) if tens else 1
    o = _CN_DIGITS.get(rest, 0) if rest else 0
    return str(t * 10 + o)


def verify_citations():
    print("\n## 场景 3：页面条款引用存在性（法规类）")
    issues = []
    checked_pages = 0
    for pf in PAGES.glob("*.md"):
        fm, body = parse_frontmatter(pf.read_text(encoding="utf-8"))
        cites = set(cn_article_pattern().findall(strip_code_blocks(body)))
        if not cites:
            continue
        sources = re.findall(r"raw/([^\]]+\.md)", fm.get("sources", ""))
        raw_text = ""
        for s in sources:
            sp = RAW / s
            if sp.exists():
                _, raw_body = parse_frontmatter(sp.read_text(encoding="utf-8"))
                raw_text += raw_body
        checked_pages += 1
        # 归一化比对：页面与 raw 的条款号统一转阿拉伯数字（raw 原文为中文数字「第二十六条」）
        def norm_articles(t: str) -> set[str]:
            return {cn_to_num(x) for x in cn_article_pattern().findall(strip_code_blocks(t))}
        raw_arts = norm_articles(raw_text)
        for c in cites:
            num = cn_to_num(c)
            if num not in raw_arts:
                issues.append(f"{pf.stem}: 引用第{c}条(={num}) 在 sources 原文条款中不存在")
    record(f"条款引用均存在于 sources raw（{checked_pages} 页含条款引用）", not issues,
           "; ".join(issues) if issues else "OK")


def verify_mirror_build():
    print("\n## 场景 4：docs/wiki 进镜像构建校验")
    di = (REPO_ROOT / ".dockerignore")
    df = (REPO_ROOT / "Dockerfile")
    di_lines = [l.strip() for l in di.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")]
    df_text = df.read_text(encoding="utf-8") if df.exists() else ""
    ok_di = "!docs/wiki/" in di_lines and not any(re.fullmatch(r"docs/?", l) for l in di_lines)
    ok_df = "COPY docs/wiki" in df_text
    record(
        ".dockerignore 放行 docs/wiki（!docs/wiki/ 且无裸 docs/ 排除）",
        ok_di,
        f"有效行: {di_lines}" if not ok_di else "OK（忽略注释行）",
    )
    record("Dockerfile 含 COPY docs/wiki ./docs/wiki/", ok_df,
           "COPY docs/wiki 存在" if ok_df else "Dockerfile 缺 COPY docs/wiki")


def main():
    print("=" * 64)
    print("  issue #103 wiki 校验（docs/wiki: 结构 / 溯源 / 条款 / 镜像）")
    print("=" * 64)
    verify_structure()
    verify_raw_sha256()
    verify_citations()
    verify_mirror_build()

    print("\n" + "=" * 64)
    print("  校验汇总")
    print("=" * 64)
    for name, ok, detail in _results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")
    fails = [r for r in _results if not r[1]]
    print(f"\n  结果: {'全部通过 ✅' if not fails else f'存在失败 ❌ ({len(fails)}/{len(_results)})'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
