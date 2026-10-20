"""Compare supplied listings without claiming to verify people, ownership or safety."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LIMIT = 500
DISCLAIMER = "结果只依据输入文字；身份、房屋状况、签约权限和是否仍可租均待核实。"


def safe_url(value: str) -> str:
    """Public XHS links only. Never publish session/query tokens in the report."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com", "xhslink.com"}
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            return ""
        return urlunsplit(("https", parsed.hostname, parsed.path, "", ""))
    except ValueError:
        return ""


def positive_mentions(text: str, terms: str) -> list[str]:
    """Conservative local negation handling; quotes remain available for human review."""
    hits = []
    for match in re.finditer(terms, text, flags=re.I):
        prefix = text[max(0, match.start() - 6) : match.start()]
        if re.search(r"(?:不|非|无|拒绝|无需|不是|不考虑|不要|不收|免)\s*$", prefix):
            continue
        hits.append(match.group())
    return list(dict.fromkeys(hits))


def rent_evidence(text: str) -> list[dict]:
    # Require an explicit monthly unit or a rent label. Deposits are not monthly rent.
    label = r"(?:租金每月|每月租金|月租金|租金|月租|房租)\s*[:：约为是]?\s*[¥￥]?\s*"
    number = r"(\d+(?:\.\d+)?)\s*([kK千]?)"
    monthly = r"\s*(?:元|块)?\s*(?:/\s*月|每月|一个月)"
    range_body = number + r"\s*(?:元|块)?\s*[-—–~～至到]\s*" + number
    range_pattern = label + range_body + "|" + range_body + monthly
    ranges = list(re.finditer(range_pattern, text))
    evidence = []
    for match in ranges:
        if re.match(
            r"\s*(?:元|块)?\s*(?:/\s*(?:年|天|日|周)|每(?:年|天|日|周))", text[match.end() :]
        ):
            continue
        values = match.groups()
        low, low_unit, high, high_unit = values[:4] if values[0] else values[4:]
        for value, unit in [(low, low_unit or high_unit), (high, high_unit)]:
            price = float(value) * (1000 if unit else 1)
            if 0 < price <= 1_000_000:
                evidence.append({"amount": price, "quote": match.group()})
    pattern = label + number + "|" + number + monthly
    for match in re.finditer(pattern, text):
        if any(match.start() < item.end() and match.end() > item.start() for item in ranges):
            continue
        prefix = text[max(0, match.start() - 5) : match.start()]
        if re.search(r"(押金|服务费|水电|物业费)\s*[:：]?\s*$", prefix):
            continue
        if re.match(
            r"\s*(?:元|块)?\s*(?:/\s*(?:年|天|日|周)|每(?:年|天|日|周))", text[match.end() :]
        ):
            continue
        number = match.group(1) or match.group(3)
        unit = match.group(2) or match.group(4)
        price = float(number) * (1000 if unit else 1)
        if 0 < price <= 1_000_000:
            evidence.append({"amount": price, "quote": match.group()})
    return evidence


def analyze(
    posts: list[dict], min_budget: float = 0, max_budget: float = 5000, housing: str = "entire"
) -> dict:
    for amount in (min_budget, max_budget):
        if isinstance(amount, bool) or not math.isfinite(amount) or amount < 0:
            raise ValueError("预算必须是有限非负数。")
    if min_budget > max_budget:
        raise ValueError("最低预算不能高于最高预算。")
    if housing not in {"entire", "shared", "any"}:
        raise ValueError("housing 只能为 entire、shared 或 any。")
    if not isinstance(posts, list) or len(posts) > LIMIT:
        raise ValueError(f"输入必须是数组，最多 {LIMIT} 条房源。")
    rows, seen, duplicates = [], set(), 0
    for index, post in enumerate(posts, 1):
        if not isinstance(post, dict):
            raise ValueError(f"第 {index} 条房源必须是对象。")
        title, desc = post.get("title", ""), post.get("desc", "")
        if not isinstance(title, str) or not isinstance(desc, str) or not (title or desc):
            raise ValueError(f"第 {index} 条需要 title 或 desc 文本。")
        if len(title) + len(desc) > 30_000:
            raise ValueError(f"第 {index} 条文字过长（上限 30000 字符）。")
        source = safe_url(str(post.get("url", "")))
        text = title + "\n" + desc
        identity = str(
            post.get("id")
            or source
            or hashlib.sha256(re.sub(r"\s+", "", text).encode()).hexdigest()
        )
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        prices = rent_evidence(text)
        distinct_prices = {item["amount"] for item in prices}
        price = next(iter(distinct_prices)) if len(distinct_prices) == 1 else None
        shared = positive_mentions(text, r"合租|次卧|主卧|床位|隔断")
        entire = positive_mentions(text, r"整租|独租|独立整套")
        kind = (
            "conflicting"
            if shared and entire
            else ("shared" if shared else "entire" if entire else "unknown")
        )
        mismatch = []
        unknown = ["身份、签约权限、房屋状态及当前可租状态尚未核实"]
        questions = ["目前还可租吗？方便提供最新看房时间和完整费用明细吗？"]
        evidence = [{"field": "月租", "quote": p["quote"]} for p in prices]
        evidence.extend({"field": "出租方式", "quote": q} for q in shared + entire)
        if price is None:
            unknown.append("月租存在多个报价，需确认适用条件" if prices else "未找到明确月租")
            questions.append("这套房每月租金是多少？是否有优惠条件或其他固定费用？")
        elif not min_budget <= price <= max_budget:
            mismatch.append(f"月租 {price:g} 元超出预算区间 {min_budget:g}–{max_budget:g} 元")
        if kind in {"unknown", "conflicting"}:
            unknown.append("出租方式不明或同时提及整租和合租")
            questions.append("是整套独租还是与他人合租？是否有独立厨卫？")
        elif housing != "any" and kind != housing:
            mismatch.append("出租方式与本次偏好不符")
        for term, question in [
            (r"押一付三|押一付一|押二付一", "押金和租金分别何时支付？退还条件是什么？"),
            (r"服务费|中介费|管理费", "这些费用分别多少、收取几次，由谁收取？"),
            (r"刚装修|新装修|全新装修", "装修完成时间是什么时候？有哪些可核对的房屋资料？"),
        ]:
            matches = positive_mentions(text, term)
            if matches:
                evidence.extend({"field": "需要确认", "quote": q} for q in matches)
                questions.append(question)
        published = str(post.get("published_at") or post.get("pub_time") or "")
        if not published:
            unknown.append("缺少发布时间")
        if not source:
            unknown.append("缺少可公开引用的来源链接")
        status = (
            "不符合已知条件"
            if mismatch
            else (
                "信息不足，先问清"
                if price is None or kind in {"unknown", "conflicting"}
                else "符合已知条件，待实地核实"
            )
        )
        rows.append(
            {
                "id": identity,
                "title": title,
                "description": desc,
                "url": source,
                "published_at": published,
                "price": price,
                "housing": kind,
                "status": status,
                "evidence": evidence,
                "mismatches": mismatch,
                "unknowns": unknown,
                "questions": list(dict.fromkeys(questions)),
            }
        )
    # Stable sorting expresses the user's constraints, never a credibility probability.
    rows.sort(key=lambda row: (bool(row["mismatches"]), row["price"] is None))
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "criteria": {"min_budget": min_budget, "max_budget": max_budget, "housing": housing},
        "input_count": len(posts),
        "duplicate_count": duplicates,
        "count": len(rows),
        "disclaimer": DISCLAIMER,
        "listings": rows,
    }


def render_html(report: dict) -> str:
    esc = html.escape
    cards = []
    for row in report["listings"]:
        evidence = "".join(
            f"<li>{esc(e['field'])}：<q>{esc(e['quote'])}</q></li>" for e in row["evidence"]
        )
        missing = "".join(f"<li>{esc(x)}</li>" for x in row["mismatches"] + row["unknowns"])
        questions = "".join(f"<li>{esc(q)}</li>" for q in row["questions"])
        link = (
            f'<a href="{esc(row["url"], quote=True)}" rel="noreferrer">查看原文</a>'
            if row["url"]
            else "来源链接待补充"
        )
        price = f"{row['price']:g} 元/月" if row["price"] is not None else "月租待确认"
        cards.append(
            f'<article><p class="badge">{esc(row["status"])}</p><h2>{esc(row["title"])}</h2>'
            f'<p class="price">{price}</p><p>{link} · 发布时间：'
            f"{esc(row['published_at'] or '未知')}</p><h3>原文依据</h3>"
            f"<ul>{evidence or '<li>信息不足</li>'}</ul>"
            f"<h3>差异与待核实</h3><ul>{missing}</ul><h3>可以问对方</h3><ul>{questions}</ul>"
            "<details><summary>展开输入原文</summary>"
            f'<p class="source">{esc(row["description"])}</p></details></article>'
        )
    budget = report["criteria"]
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'\">"
        "<title>租房候选对比报告</title><style>body{font:16px/1.7 system-ui;background:#f2f5f8;"
        "color:#172d40;max-width:1040px;margin:40px auto;padding:0 20px}article{background:white;"
        "border:1px solid #dbe3eb;border-radius:16px;padding:28px;margin:24px 0}"
        ".badge{color:#286752}h1{font-size:36px}.price{font-size:24px;font-weight:700}"
        ".source{white-space:pre-wrap}li{margin:6px 0}a{color:#175da8}</style>"
        f"<h1>租房候选对比报告</h1><p>{esc(DISCLAIMER)}</p>"
        f"<p>预算 {budget['min_budget']:g}–{budget['max_budget']:g} 元/月 · "
        f"{report['count']} 条候选 · 去除 {report['duplicate_count']} 条重复</p>"
        "<p>报价仅按原文提取；没有报价不视为免费。记录的排序不是可信度评分。"
        "报告为本地静态文件，不联网、不发评论、不支付租金。</p>" + "".join(cards) + "</html>"
    )


def write_report(report: dict, output: str) -> Path:
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError(f"输出目录已存在，请换一个名称：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rental-", dir=destination.parent) as tmp:
        stage = Path(tmp) / "report"
        stage.mkdir()
        (stage / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
        (stage / "report.html").write_text(render_html(report), "utf-8")
        md = ["# 租房候选对比", DISCLAIMER, ""]
        for row in report["listings"]:
            # Listing titles are untrusted text, including in Markdown viewers.
            title = " ".join(row["title"].split())
            title = re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html.escape(title))
            md += [
                f"## {title}",
                row["status"],
                "",
                *[f"- {q}" for q in row["questions"]],
                "",
            ]
        (stage / "questions.md").write_text("\n".join(md), "utf-8")
        os.rename(stage, destination)
    return destination
