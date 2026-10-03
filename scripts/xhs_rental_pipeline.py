"""xhs_rental_pipeline.py — 小红书个人整租猎人 CLI。

子命令（均可单独运行，默认读写 ./rental_work）::

    doctor         环境自检（bridge / 扩展 / 中文字体 / 依赖）
    search         多关键词检索（默认“最新”排序、仅图文），结果去重累积
    filter         卡片级初筛（求租/已租/合租/商业公寓/中介昵称），排除项另存
    inspect        逐条抓取详情（正文、图片、首屏评论），带缓存可断点续跑
    check-authors  访问发帖人主页，统计历史房源帖（法则 2：多套房 = 中介）
    analyze        规则研判 + 需求匹配（预算/户型/时效/区域）
    digest         生成 review.md，供 Claude 逐条复核
    render-poster  渲染长图报告（优先使用 Claude 写的 curated.json）
    comment        给选定帖子发评论 —— 默认只预览，必须加 --confirm 才发送
    run-all        search → filter → inspect → check-authors → analyze → digest → render-poster

stdout 最后一行是 JSON 摘要；日志走 stderr。
退出码：0 成功；2 错误；3 小红书要求人工验证（请在浏览器中完成验证后重跑，已有进度会保留）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rental.pipeline import (
    BridgeUnavailableError,
    RentalPipeline,
    VerificationRequiredError,
    summarize_paths,
)

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
log = logging.getLogger("rental")

BEDROOM_WORDS = {
    "开间": 0,
    "0": 0,
    "一居": 1,
    "1": 1,
    "两居": 2,
    "二居": 2,
    "2": 2,
    "三居": 3,
    "3": 3,
}


def _emit(data: dict, code: int = 0) -> None:
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(code)


def _bedrooms(values: list[str] | None) -> list[int] | None:
    if not values:
        return None
    out = []
    for v in values:
        if v not in BEDROOM_WORDS:
            raise SystemExit(f"无法识别的户型：{v}（可用：开间/一居/两居/三居 或 0/1/2/3）")
        out.append(BEDROOM_WORDS[v])
    return sorted(set(out))


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--work-dir", default="./rental_work", help="工作目录（默认 ./rental_work）")
    p.add_argument("--bridge-url", default="ws://localhost:9333")
    p.add_argument(
        "--delay",
        type=float,
        nargs=2,
        default=[2.5, 5.0],
        metavar=("MIN", "MAX"),
        help="两次页面访问之间的随机间隔秒数（默认 2.5 5.0，请勿调得过低）",
    )


def _add_req(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("需求（写入 requirements.json，后续步骤自动沿用）")
    g.add_argument("--budget-min", type=int)
    g.add_argument("--budget-max", type=int)
    g.add_argument("--bedrooms", nargs="+", help="允许的户型：开间 一居 两居 三居（或 0 1 2 3）")
    g.add_argument("--max-age-days", type=int, help="只要 N 天内发布的帖子（超出会标记，不删除）")
    g.add_argument("--area-keywords", nargs="+", help="区域/地铁站/小区关键词，正文未提及则标记")


def _save_req(pl: RentalPipeline, a: argparse.Namespace) -> None:
    req = {
        "budget_min": a.budget_min,
        "budget_max": a.budget_max,
        "bedrooms": _bedrooms(a.bedrooms),
        "max_age_days": a.max_age_days,
        "must_have": a.area_keywords,
    }
    if any(v not in (None, [], "") for v in req.values()):
        pl.save_requirements(req)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="小红书个人整租猎人：检索、防中介研判与长图报告")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="环境自检")
    _add_common(p)

    p = sub.add_parser("search", help="多关键词检索")
    _add_common(p)
    p.add_argument("--keywords", nargs="+", required=True)
    p.add_argument("--per-keyword", type=int, default=20)
    p.add_argument(
        "--sort", default="最新", choices=["综合", "最新", "最多点赞", "最多评论", "最多收藏"]
    )
    p.add_argument("--publish-time", default="", choices=["", "不限", "一天内", "一周内", "半年内"])

    p = sub.add_parser("filter", help="卡片初筛")
    _add_common(p)
    p.add_argument("--raw-file")

    p = sub.add_parser("inspect", help="抓取详情")
    _add_common(p)
    p.add_argument("--filtered-file")
    p.add_argument("--limit", type=int, default=15)

    p = sub.add_parser("check-authors", help="核验发帖人主页")
    _add_common(p)
    p.add_argument("--detailed-file")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("analyze", help="研判打分")
    _add_common(p)
    _add_req(p)
    p.add_argument("--detailed-file")

    p = sub.add_parser("digest", help="生成 review.md")
    _add_common(p)
    p.add_argument("--analyzed-file")
    p.add_argument("--max-items", type=int, default=20)

    p = sub.add_parser("render-poster", help="渲染长图")
    _add_common(p)
    p.add_argument("--analyzed-file")
    p.add_argument("--curated-file", help="Claude 复核后的精选清单（默认 <work-dir>/curated.json）")
    p.add_argument("--output", help="输出 PNG 路径")
    p.add_argument("--title", default="")
    p.add_argument("--subtitle", default="")
    p.add_argument("--top-n", type=int, default=5)

    p = sub.add_parser("comment", help="发评论（默认预览，--confirm 才发送）")
    _add_common(p)
    p.add_argument("--analyzed-file")
    p.add_argument(
        "--message", required=True, help="评论内容，建议具体问题，如“请问还在吗？能和房东直签吗？”"
    )
    p.add_argument("--ids", nargs="+", help="指定帖子 id（推荐，由用户挑选）")
    p.add_argument("--top-n", type=int, default=3)
    p.add_argument("--confirm", action="store_true", help="确认真正发送（用户已明确同意）")

    p = sub.add_parser("run-all", help="一键流程（不含评论）")
    _add_common(p)
    _add_req(p)
    p.add_argument("--keywords", nargs="+", required=True)
    p.add_argument("--per-keyword", type=int, default=20)
    p.add_argument("--publish-time", default="", choices=["", "不限", "一天内", "一周内", "半年内"])
    p.add_argument("--limit", type=int, default=15, help="详情抓取上限")
    p.add_argument("--author-limit", type=int, default=8, help="主页核验上限（0 跳过）")
    p.add_argument("--output-png")
    p.add_argument("--title", default="")
    return ap


def cmd_doctor(a: argparse.Namespace) -> dict:
    from rental.pipeline import ensure_bridge
    from rental.poster import FontNotFoundError, find_fonts

    report: dict = {"python": sys.version.split()[0]}
    try:
        report["font"] = find_fonts()[0]
    except FontNotFoundError as e:
        report["font"] = None
        report["font_help"] = str(e)
    try:
        report.update(ensure_bridge(a.bridge_url))
    except Exception as e:
        report.update(server=False, extension=False, bridge_error=str(e)[:200])
    hints = []
    if not report.get("server"):
        hints.append(
            "Bridge 未运行：python scripts/bridge_server.py（或运行 cli.py check-login 自动拉起）"
        )
    elif not report.get("extension"):
        hints.append("扩展未连接：在 Chrome 加载 extension/ 目录并打开 xiaohongshu.com")
    if not report.get("font"):
        hints.append("缺少中文字体，海报无法渲染（见 font_help）")
    report["ok"] = not hints
    report["hints"] = hints
    return report


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    if a.command == "doctor":
        r = cmd_doctor(a)
        _emit(r, 0 if r["ok"] else 2)

    pl = RentalPipeline(work_dir=a.work_dir, bridge_url=a.bridge_url, delay=tuple(a.delay))
    out: dict = {"command": a.command, "work_dir": str(pl.work)}
    try:
        if a.command == "search":
            out["file"] = str(pl.search(a.keywords, a.per_keyword, a.sort, a.publish_time))
        elif a.command == "filter":
            out["file"] = str(pl.filter_candidates(a.raw_file))
        elif a.command == "inspect":
            out["file"] = str(pl.inspect(a.filtered_file, a.limit))
        elif a.command == "check-authors":
            out["dir"] = str(pl.check_authors(a.detailed_file, a.limit))
        elif a.command == "analyze":
            _save_req(pl, a)
            out["file"] = str(pl.analyze(a.detailed_file))
        elif a.command == "digest":
            out["file"] = str(pl.digest(a.analyzed_file, a.max_items))
        elif a.command == "render-poster":
            png = pl.render_poster(
                a.analyzed_file, a.output, a.curated_file, a.title, a.subtitle, a.top_n
            )
            out["png"] = str(png) if png else None
        elif a.command == "comment":
            plan = pl.comment(a.message, a.analyzed_file, a.ids, a.top_n, a.confirm)
            out.update(sent=a.confirm, targets=plan)
            if not a.confirm:
                out["hint"] = "仅预览，未发送。用户确认后加 --confirm 重新运行。"
        elif a.command == "run-all":
            _save_req(pl, a)
            pl.search(a.keywords, a.per_keyword, "最新", a.publish_time)
            pl.filter_candidates()
            pl.inspect(limit=a.limit)
            if a.author_limit > 0:
                pl.check_authors(limit=a.author_limit)
            pl.analyze()
            pl.digest()
            png = pl.render_poster(output=a.output_png, title=a.title)
            out["png"] = str(png) if png else None
    except VerificationRequiredError as e:
        out.update(
            ok=False, verification_required=True, error=str(e)[:300], files=summarize_paths(pl.work)
        )
        out["hint"] = (
            "小红书要求人工验证：请用户在 Chrome 中打开小红书完成验证，"
            "稍等几分钟后重跑同一命令（已缓存的进度会跳过）。"
        )
        _emit(out, 3)
    except (BridgeUnavailableError, FileNotFoundError) as e:
        out.update(ok=False, error=str(e), files=summarize_paths(pl.work))
        _emit(out, 2)
    except Exception as e:
        log.exception("执行失败")
        out.update(ok=False, error=str(e)[:300], files=summarize_paths(pl.work))
        _emit(out, 2)

    analyzed = Path(pl.work / "analyzed_results.json")
    if analyzed.exists() and a.command in ("analyze", "run-all"):
        res = json.loads(analyzed.read_text(encoding="utf-8"))
        out["counts"] = {
            t: sum(r["tier"] == t for r in res)
            for t in ("trusted", "verify", "suspect", "excluded")
        }
    out["ok"] = True
    out["files"] = summarize_paths(pl.work)
    _emit(out, 0)


if __name__ == "__main__":
    main()
