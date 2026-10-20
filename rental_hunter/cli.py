"""A network-free entry point suitable for repeatable Skill delivery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rental_hunter import __version__
from rental_hunter.report import analyze, write_report

DEMO = [
    {
        "id": "demo-1",
        "title": "合成示例：整租一居室",
        "desc": "月租3800元，押一付一。周末可约看房，服务费另询。",
    },
    {"id": "demo-2", "title": "合成示例：合租次卧", "desc": "租金2200元/月，独立卧室，共用厨房。"},
    {
        "id": "demo-3",
        "title": "合成示例：整套转租",
        "desc": "不是合租。价格私聊，具体起租时间待定。",
    },
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按预算与原文依据比较房源；不验证身份")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("report", "demo"):
        child = sub.add_parser(command)
        if command == "report":
            child.add_argument("--input", required=True, help="用户提供的房源 JSON 数组")
        child.add_argument("--output", required=True, help="一个尚不存在的报告目录")
        child.add_argument("--min-budget", type=float, default=0)
        child.add_argument("--max-budget", type=float, default=5000)
        child.add_argument("--housing", choices=["entire", "shared", "any"], default="entire")
    args = parser.parse_args(argv)
    try:
        posts = DEMO
        if args.command == "report":
            source = Path(args.input)
            if source.stat().st_size > 5_000_000:
                raise ValueError("输入文件超过 5 MB，请拆分后处理。")
            posts = json.loads(source.read_text("utf-8-sig"))
        report = analyze(posts, args.min_budget, args.max_budget, args.housing)
        print(write_report(report, args.output) / "report.html")
        return 0
    except (OSError, ValueError) as exc:
        print(f"无法生成报告：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
