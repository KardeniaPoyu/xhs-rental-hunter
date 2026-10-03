"""生成 review.md：把研判结果压缩成 Claude 可一次读完的复核材料。

规则引擎只能做关键词级判断；“昵称女生化但口吻像中介”“图片像样板间”“文案像 AI 批量生成”
这类需要语义理解的判断，由 Claude 读 review.md 中的正文与证据完成，再写入 curated.json。
"""

from __future__ import annotations

from .extract import clean_post_text
from .rules import Requirements


def _fmt_price(f: dict) -> str:
    p = f["price"]
    if p.get("low") and p.get("high"):
        return f"{p['low']}-{p['high']}元/月"
    if p.get("price"):
        return f"{p['price']}元/月（置信度 {p['confidence']}）"
    return "未写价格"


def _fmt_req(req: Requirements) -> str:
    parts = []
    if req.budget_min or req.budget_max:
        parts.append(f"预算 {req.budget_min or '?'}-{req.budget_max or '?'}")
    if req.bedrooms is not None:
        parts.append("户型 " + "/".join("开间" if b == 0 else f"{b}居" for b in req.bedrooms))
    if req.max_age_days:
        parts.append(f"{req.max_age_days} 天内发布")
    if req.must_have:
        parts.append("区域关键词 " + "、".join(req.must_have))
    if req.city:
        parts.append(f"城市 {req.city}")
    return "；".join(parts) or "（未设置）"


def build_review_markdown(
    results: list[dict], req: Requirements, max_items: int = 20, body_chars: int = 600
) -> str:
    active = [r for r in results if r["tier"] != "excluded"]
    excluded = [r for r in results if r["tier"] == "excluded"]
    counts = {
        t: sum(r["tier"] == t for r in results)
        for t in ("trusted", "verify", "suspect", "excluded")
    }

    out = [
        "# 房源复核材料",
        "",
        f"- 需求：{_fmt_req(req)}",
        f"- 规则初判：高可信 {counts['trusted']} / 待核实 {counts['verify']} / "
        f"疑似中介 {counts['suspect']} / 排除 {counts['excluded']}",
        "- 说明：分数只是关键词初判。请逐条读正文，按 SKILL.md 的鉴别法则给出最终结论，",
        "  并把精选结果写入 curated.json（格式见 references/curated-schema.md）。",
        "",
    ]

    for i, r in enumerate(active[:max_items], 1):
        f = r["facts"]
        hist = r.get("author_history") or {}
        out += [
            f"## {i}. {r['title'] or '(无标题)'}",
            "",
            f"- id: `{r['id']}` · [原帖]({r['url']}) · 作者：{r['author']}"
            + (f" · IP：{r['ip']}" if r.get("ip") else "")
            + (f" · {r['age_days']:.0f} 天前" if r.get("age_days") is not None else ""),
            f"- 初判：**{r['verdict']}**（{r['trust_score']} 分）",
            f"- 抽取：{_fmt_price(f)} · {f['layout']}"
            + (f" · {f['area_sqm']:g}㎡" if f.get("area_sqm") else "")
            + (f" · {f['payment']}" if f.get("payment") else "")
            + (f" · 租期：{f['lease']}" if f.get("lease") else "")
            + (f" · 入住：{f['move_in']}" if f.get("move_in") else "")
            + (f" · 交通：{'、'.join(f['metro'])}" if f.get("metro") else ""),
        ]
        if r["positives"]:
            out.append(
                "- ✅ " + "；".join(f"{h['label']}「{h['evidence']}」" for h in r["positives"])
            )
        if r["risks"]:
            out.append("- ⚠️ " + "；".join(f"{h['label']}「{h['evidence']}」" for h in r["risks"]))
        if r["mismatches"]:
            out.append("- ❌ 不符需求：" + "；".join(r["mismatches"]))
        if hist.get("checked"):
            line = f"- 主页：共 {hist['total_notes']} 篇；其他房源帖 {hist['listing_notes']} 篇"
            line += (
                f"（不同房源 {hist.get('distinct_listings', 0)}，"
                f"同一套重复发布 {hist.get('repost_notes', 0)}）"
            )
            if hist.get("listing_titles"):
                line += "；不同房源：" + "；".join(hist["listing_titles"][:3])
            if hist.get("repost_titles"):
                line += "；重复发布：" + "；".join(hist["repost_titles"][:2])
            out.append(line)
        elif hist.get("error"):
            out.append(f"- 主页：核验失败（{hist['error'][:40]}）")
        else:
            out.append("- 主页：未核验")
        body = clean_post_text(r.get("desc") or "").replace("\n", " ")
        if len(body) > body_chars:
            body = body[:body_chars] + "…"
        out += ["", f"> {body or '(正文为空)'}", ""]
        out.append(
            f"- 图片 {len(r.get('images') or [])} 张 · 评论 {r.get('comment_count') or 0} 条"
        )
        if r.get("open_questions"):
            out.append("- 建议追问：" + " / ".join(r["open_questions"]))
        out.append("")

    if len(active) > max_items:
        out += [f"_另有 {len(active) - max_items} 条未展开，见 analyzed_results.json_", ""]

    if excluded:
        out += ["## 已排除（核对是否误杀）", "", "| 标题 | 作者 | 原因 |", "|---|---|---|"]
        for r in excluded[:40]:
            t = (r["title"] or "").replace("|", "/")[:28]
            out.append(f"| {t} | {r['author'][:12]} | {r['exclusion']} |")
        out.append("")
    return "\n".join(out)
