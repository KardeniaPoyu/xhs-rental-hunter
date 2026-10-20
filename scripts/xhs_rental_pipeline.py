"""
xhs_rental_pipeline.py - 可选的小红书候选采集与旧版海报适配器

功能支持：
1. search: 针对指定区域与户型多词检索小红书
2. filter: 排除明确求租或已出租的标题，其他条件留到分析时比较
3. inspect: 读取详情正文、来源和页面提供的图片链接
4. analyze: 按预算和整租/合租偏好比较，保留原文依据与待核实项
5. render-poster: 旧版 PNG 海报，需要额外字体和网络图片
6. comment: 只生成询问草稿，不发送评论
7. run-all: 旧版采集、比较、海报流程，不验证房源真实性

商品主入口为 python -m rental_hunter report；Chrome 采集尚待真实账号验收。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import time
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 引入本包内模块
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Allow direct execution from the source/Skill directory as well as an installed package.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rental_hunter.report import analyze as compare_listings
from rental_hunter.report import positive_mentions
from xhs.bridge import BridgePage
from xhs.urls import make_feed_detail_url

EXTRACT_DETAIL_JS = """
(() => {
    const s = window.__INITIAL_STATE__?.note?.noteDetailMap;
    if (!s) return null;
    const curId = Object.keys(s)[0];
    const data = s[curId]?.note;
    if (!data) return null;
    return JSON.stringify({
        id: curId,
        title: data.title || "",
        desc: data.desc || "",
        images: (data.imageList || []).map(img => img.urlDefault || img.urlPre || img.url || ""),
        user: {
            nickname: data.user?.nickname || "",
            userId: data.user?.userId || ""
        },
        ip: data.ipLocation || "",
        time: data.time || 0
    });
})()
"""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
}


class RentalPipeline:
    def __init__(self, bridge_url: str = "ws://127.0.0.1:9333", work_dir: str = "./rental_work"):
        self.bridge_url = bridge_url
        self.work_dir = os.path.abspath(work_dir)
        os.makedirs(self.work_dir, exist_ok=True)
        self.cache_dir = os.path.join(self.work_dir, "img_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_page(self) -> BridgePage:
        return BridgePage(self.bridge_url)

    def search(self, keywords: list[str], max_per_keyword: int = 15) -> str:
        """多关键词检索并聚合去重"""
        page = self.get_page()
        all_feeds = []
        seen_ids = set()

        for kw in keywords:
            print(f"[Search] 正在检索关键词: '{kw}'...")
            search_url = f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(kw)}&source=web_search_result_notes"
            page.navigate(search_url)
            page.wait_for_load()
            page.wait_dom_stable()
            time.sleep(2.0)

            # 获取页面卡片
            js_extract = """
            (() => {
                const s = window.__INITIAL_STATE__?.search?.feeds;
                if (s && Array.isArray(s) && s.length > 0) {
                    return JSON.stringify(s.map(f => ({
                        id: f.id,
                        xsec_token: f.xsecToken,
                        title: f.noteCard?.displayTitle || "",
                        author: f.noteCard?.user?.nickName || "",
                        user_id: f.noteCard?.user?.userId || "",
                        pub_time: f.noteCard?.cornerTagInfo || "",
                        cover: f.noteCard?.cover?.urlDefault || f.noteCard?.cover?.urlPre || ""
                    })));
                }
                return JSON.stringify([]);
            })()
            """
            raw = page.evaluate(js_extract)
            feeds = json.loads(raw or "[]")
            print(f"  -> 获取到 {len(feeds)} 条原始卡片")
            for f in feeds[:max_per_keyword]:
                fid = f.get("id")
                if fid and fid not in seen_ids:
                    seen_ids.add(fid)
                    f["query"] = kw
                    all_feeds.append(f)
            time.sleep(1.5)

        out_path = os.path.join(self.work_dir, "raw_feeds.json")
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(all_feeds, f_out, ensure_ascii=False, indent=2)
        print(f"[Search] 完成！共去重汇总 {len(all_feeds)} 条房源候选，保存至: {out_path}")
        return out_path

    def filter_candidates(self, raw_feeds_path: str) -> str:
        """应用硬性排除词与初筛规则"""
        with open(raw_feeds_path, encoding="utf-8") as f:
            feeds = json.load(f)

        # Keep candidates unless the title explicitly says seeking/already rented.
        # Housing preferences belong in analysis, not hidden keyword exclusions.
        filtered = []
        for candidate in feeds:
            title = candidate.get("title", "")
            if re.search(r"^(?:求租|求.{0,8}租|想租)", title):
                continue
            if positive_mentions(title, r"已出租|已租出|已出"):
                continue
            filtered.append(candidate)

        out_path = os.path.join(self.work_dir, "filtered_feeds.json")
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(filtered, f_out, ensure_ascii=False, indent=2)
        print(f"[Filter] {len(feeds)} 条输入，保留 {len(filtered)} 条候选：{out_path}")
        return out_path

    def inspect(self, filtered_path: str, max_inspect: int = 20) -> str:
        """深入正文抓取实拍图片、正文与房东签约细节"""
        page = self.get_page()
        with open(filtered_path, encoding="utf-8") as f:
            candidates = json.load(f)

        inspected = []
        print(f"[Inspect] 计划深度勘查前 {min(len(candidates), max_inspect)} 条候选...")

        for idx, c in enumerate(candidates[:max_inspect]):
            target_id = c["id"]
            token = c.get("xsec_token", "")
            target_url = make_feed_detail_url(target_id, token)
            print(f"[{idx + 1}] 抓取: {c['title']} ({target_id})...")

            try:
                curr_url = page.evaluate("location.href") or ""
                if target_id not in curr_url:
                    page.navigate(target_url)
                    page.wait_for_load()
                    page.wait_dom_stable()
                    time.sleep(1.5)
                    curr_url = page.evaluate("location.href") or ""
                    if target_id not in curr_url:
                        page.evaluate(f"window.location.href = {json.dumps(target_url)}")
                        page.wait_for_load()
                        page.wait_dom_stable()
                        time.sleep(1.8)

                for _ in range(6):
                    raw = page.evaluate(EXTRACT_DETAIL_JS)
                    if raw:
                        try:
                            parsed = json.loads(raw)
                            if parsed and parsed.get("id") == target_id:
                                # 合并元信息
                                parsed["pub_time"] = c.get("pub_time", "")
                                parsed["query"] = c.get("query", "")
                                parsed["url"] = f"https://www.xiaohongshu.com/explore/{target_id}"
                                parsed["xsec_token"] = c.get("xsec_token", "")
                                inspected.append(parsed)
                                print(f"  -> 已读取 {len(parsed.get('desc', ''))} 字正文")
                                break
                        except Exception:
                            pass
                    time.sleep(0.4)
            except Exception as e:
                print(f"  -> 访问异常: {e}")

            # 防风控休眠
            time.sleep(1.2)

        out_path = os.path.join(self.work_dir, "detailed_posts.json")
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(inspected, f_out, ensure_ascii=False, indent=2)
        print(f"[Inspect] 深度勘查完成！成功获取 {len(inspected)} 条完整房源，保存至: {out_path}")
        return out_path

    def analyze(self, detailed_path: str, min_budget: int = 3000, max_budget: int = 4800) -> str:
        """Compare against constraints; supplied prose does not establish credibility."""
        with open(detailed_path, encoding="utf-8") as source:
            posts = json.load(source)
        report = compare_listings(posts, min_budget, max_budget)
        originals = {str(p.get("id")): p for p in posts}
        results = []
        for row in report["listings"]:
            post = originals.get(row["id"], {})
            results.append(
                {
                    **row,
                    "verdict": row["status"],
                    "desc": row["description"],
                    "author": post.get("user", {}).get("nickname", ""),
                    "pub_time": row["published_at"],
                    "images": post.get("images", []),
                    "xsec_token": post.get("xsec_token", ""),
                    "real_signals": [f"{e['field']}：{e['quote']}" for e in row["evidence"]],
                    "suspect_reasons": row["mismatches"] + row["unknowns"],
                }
            )
        out_path = os.path.join(self.work_dir, "analyzed_results.json")
        with open(out_path, "w", encoding="utf-8") as target:
            json.dump(results, target, ensure_ascii=False, indent=2)
        print(f"[Analyze] 已比较 {len(results)} 条；身份及可租状态未核实：{out_path}")
        return out_path

    def _fetch_img(self, url: str, fname: str) -> Image.Image | None:
        if not url:
            return None
        cache_file = os.path.join(self.cache_dir, fname)
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 1000:
            try:
                return Image.open(cache_file).convert("RGB")
            except Exception:
                pass
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
                with open(cache_file, "wb") as f_out:
                    f_out.write(data)
                return Image.open(BytesIO(data)).convert("RGB")
        except Exception:
            return None

    def render_poster(self, analyzed_path: str, output_png_path: str | None = None) -> str:
        """合成现代信息图海报报告"""
        with open(analyzed_path, encoding="utf-8") as f:
            all_results = json.load(f)

        # 选出 top 5 优质房源 + 1 典型避坑案例
        top_candidates = [r for r in all_results if "不符合" not in r["verdict"]][:5]
        warn_candidates = [
            r for r in all_results if "信息不足" in r["verdict"] or len(r["suspect_reasons"]) > 0
        ]
        warn_case = warn_candidates[0] if warn_candidates else None

        curated = list(top_candidates)
        if warn_case and warn_case["id"] not in [c["id"] for c in curated]:
            warn_case["is_warn_case"] = True
            curated.append(warn_case)

        if not curated:
            print("[Poster] 未找到有效房源数据，跳过海报渲染。")
            return ""

        if not output_png_path:
            output_png_path = os.path.join(self.work_dir, "rental_report.png")

        font_path = (
            r"C:\Windows\Fonts\msyh.ttc" if os.path.exists(r"C:\Windows\Fonts\msyh.ttc") else None
        )
        if not font_path:
            print("[Poster] 未找到 msyh.ttc 字体，请确保系统支持。")
            return ""

        font_title = ImageFont.truetype(font_path, 34)
        font_subtitle = ImageFont.truetype(font_path, 19)
        font_card_title = ImageFont.truetype(font_path, 23)
        font_price = ImageFont.truetype(font_path, 25)
        font_bold = ImageFont.truetype(font_path, 17)
        font_body = ImageFont.truetype(font_path, 16)
        font_badge = ImageFont.truetype(font_path, 15)
        font_small = ImageFont.truetype(font_path, 14)

        canvas_width = 1260
        canvas_height = 220 + len(curated) * 365 + 170
        img = Image.new("RGB", (canvas_width, canvas_height), color=(248, 250, 252))
        draw = ImageDraw.Draw(img)

        # 顶部 Banner
        draw.rectangle([(0, 0), (canvas_width, 205)], fill=(15, 23, 42))
        draw.text((50, 32), "租房候选对比 · 条件与原文依据", font=font_title, fill=(255, 255, 255))
        draw.text(
            (50, 80),
            "仅依据帖子文字整理；身份、房屋状态和当前可租状态仍待核实",
            font=font_subtitle,
            fill=(203, 213, 225),
        )

        badges = [
            ("[1] 核对预算范围", (239, 68, 68)),
            ("[2] 核对整租或合租", (249, 115, 22)),
            ("[3] 列出原文依据", (59, 130, 246)),
            ("[4] 补齐待确认事项", (16, 185, 129)),
        ]
        bx = 50
        for b_text, b_color in badges:
            draw.rounded_rectangle([(bx, 130), (bx + 270, 170)], radius=6, fill=(30, 41, 59))
            draw.rounded_rectangle([(bx, 130), (bx + 270, 170)], radius=6, outline=b_color, width=2)
            draw.text((bx + 10, 142), b_text, font=font_badge, fill=(241, 245, 249))
            bx += 285

        y_offset = 225
        for idx, c in enumerate(curated):
            card_h = 345
            is_warn = c.get("is_warn_case", False) or "信息不足" in c["verdict"]
            bg_color = (255, 255, 255)
            border_color = (239, 68, 68) if is_warn else (226, 232, 240)

            draw.rounded_rectangle(
                [(40, y_offset), (canvas_width - 40, y_offset + card_h)],
                radius=12,
                fill=bg_color,
                outline=border_color,
                width=2 if is_warn else 1,
            )

            # 标签
            tag_text = "【重点排查案例】" if is_warn else f"候选 {idx + 1}"
            tag_bg = (239, 68, 68) if is_warn else (16, 185, 129)
            draw.rounded_rectangle(
                [(60, y_offset + 16), (210, y_offset + 46)], radius=6, fill=tag_bg
            )
            draw.text((68, y_offset + 21), tag_text, font=font_badge, fill=(255, 255, 255))

            # 标题与租金
            draw.text(
                (225, y_offset + 18), c["title"][:22], font=font_card_title, fill=(15, 23, 42)
            )
            price_str = f"{c['price']}元/月" if c.get("price") else "面议/私信"
            draw.text(
                (canvas_width - 360, y_offset + 18), price_str, font=font_price, fill=(220, 38, 38)
            )

            meta_line = f"发帖人：{c['author']}  |  发布时间：{c.get('pub_time') or '未知'}"
            draw.text((60, y_offset + 55), meta_line, font=font_small, fill=(100, 116, 139))

            # 缩略图
            tx = 60
            ty = y_offset + 88
            thumb_w, thumb_h = 135, 98
            for img_idx in range(3):
                img_url = c["images"][img_idx] if img_idx < len(c.get("images", [])) else None
                loaded = self._fetch_img(img_url, f"poster_{idx}_{img_idx}.jpg")
                if loaded:
                    loaded = loaded.resize((thumb_w, thumb_h))
                    img.paste(loaded, (tx, ty))
                    draw.rectangle(
                        [(tx, ty), (tx + thumb_w, ty + thumb_h)], outline=(203, 213, 225), width=1
                    )
                else:
                    draw.rectangle(
                        [(tx, ty), (tx + thumb_w, ty + thumb_h)],
                        fill=(241, 245, 249),
                        outline=(203, 213, 225),
                    )
                    draw.text(
                        (tx + 32, ty + 40), "图片未提供", font=font_small, fill=(148, 163, 184)
                    )
                tx += thumb_w + 12

            # 右侧文字
            rx = 520
            ry = y_offset + 88

            draw.text((rx, ry), "【房源描述】", font=font_bold, fill=(30, 41, 59))
            desc_lines = textwrap.wrap(c["desc"].replace("\n", " "), width=42)
            for l_idx, line in enumerate(desc_lines[:2]):
                draw.text((rx + 118, ry + 1 + l_idx * 23), line, font=font_body, fill=(51, 65, 85))

            ry += 52
            draw.text((rx, ry), "【原文依据】", font=font_bold, fill=(30, 41, 59))
            sig_str = "、".join(c["real_signals"]) if c["real_signals"] else "暂无明确依据"
            draw.text((rx + 118, ry + 1), sig_str[:42], font=font_body, fill=(51, 65, 85))

            ry += 30
            draw.text((rx, ry), "【研判结论】", font=font_bold, fill=(30, 41, 59))
            draw.text((rx + 118, ry + 1), c["verdict"], font=font_body, fill=(14, 116, 144))

            # 诊断框
            ry += 32
            box_bg = (254, 242, 242) if is_warn else (240, 253, 244)
            box_border = (252, 165, 165) if is_warn else (187, 247, 208)
            draw.rounded_rectangle(
                [(rx, ry), (canvas_width - 60, ry + 66)],
                radius=6,
                fill=box_bg,
                outline=box_border,
                width=1,
            )

            anti_color = (185, 28, 28) if is_warn else (21, 128, 61)
            draw.text((rx + 12, ry + 7), "【待核实】", font=font_bold, fill=anti_color)
            diag_text = (
                "；".join(c["suspect_reasons"])
                if c["suspect_reasons"]
                else "帖子描述不足以确认发布者身份、房屋状况或可租状态。"
            )
            d_lines = textwrap.wrap(diag_text, width=41)
            for l_idx, line in enumerate(d_lines[:2]):
                draw.text((rx + 118, ry + 9 + l_idx * 22), line, font=font_small, fill=anti_color)

            y_offset += card_h + 18

        # 底部指南
        draw.rounded_rectangle(
            [(40, y_offset), (canvas_width - 40, y_offset + 130)],
            radius=12,
            fill=(241, 245, 249),
            outline=(203, 213, 225),
        )
        draw.text(
            (60, y_offset + 16),
            "【下一步】向对方核实，再安排看房",
            font=font_bold,
            fill=(15, 23, 42),
        )
        draw.text(
            (60, y_offset + 44),
            "1. 确认月租、押金、服务费、起租时间和可看房时间；不要把营销文案当作核验结果。",
            font=font_small,
            fill=(71, 85, 105),
        )
        draw.text(
            (60, y_offset + 68),
            "2. 核对房屋与签约资料；本报告不判断产权或代收租金。",
            font=font_small,
            fill=(71, 85, 105),
        )
        draw.text(
            (60, y_offset + 92),
            "3. 本报告没有检测房屋质量或室内空气；文字和图片不能代替现场核实。",
            font=font_small,
            fill=(71, 85, 105),
        )

        img.save(output_png_path, quality=95)
        print(f"[Poster] 海报生成成功: {output_png_path}")
        return output_png_path

    def batch_comment(
        self, analyzed_path: str, message: str = "请问目前还可租吗？", top_n: int = 3
    ) -> str:
        """Create a reviewable follow-up draft. Never publish comments automatically."""
        if top_n < 1:
            raise ValueError("top_n 必须至少为 1。")
        with open(analyzed_path, encoding="utf-8") as source:
            rows = json.load(source)
        drafts = [
            {
                "id": row["id"],
                "title": row["title"],
                "message": message,
                "questions": row.get("questions", []),
            }
            for row in rows
            if not row.get("mismatches")
        ][:top_n]
        path = os.path.join(self.work_dir, "followup_drafts.json")
        with open(path, "w", encoding="utf-8") as target:
            json.dump(drafts, target, ensure_ascii=False, indent=2)
        print(f"已保存 {len(drafts)} 条询问草稿，尚未发送：{path}")
        return path


def main():
    parser = argparse.ArgumentParser(description="小红书房源条件对比与询问草稿")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = subparsers.add_parser("search", help="多词检索小红书")
    p_search.add_argument("--keywords", nargs="+", required=True, help="搜索关键词列表")
    p_search.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # filter
    p_filter = subparsers.add_parser("filter", help="初筛过滤")
    p_filter.add_argument("--raw-file", required=True, help="raw_feeds.json 路径")
    p_filter.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="深度正文抓取")
    p_inspect.add_argument("--filtered-file", required=True, help="filtered_feeds.json 路径")
    p_inspect.add_argument("--limit", type=int, default=15, help="抓取上限")
    p_inspect.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="按预算与原文对比")
    p_analyze.add_argument("--min-budget", type=float, default=0)
    p_analyze.add_argument("--max-budget", type=float, default=5000)
    p_analyze.add_argument("--detailed-file", required=True, help="detailed_posts.json 路径")
    p_analyze.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # render-poster
    p_poster = subparsers.add_parser("render-poster", help="合成长图报告")
    p_poster.add_argument("--analyzed-file", required=True, help="analyzed_results.json 路径")
    p_poster.add_argument("--output", help="输出 PNG 路径")
    p_poster.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # comment
    p_comment = subparsers.add_parser("comment", help="生成询问草稿（不发送）")
    p_comment.add_argument("--analyzed-file", required=True, help="analyzed_results.json 路径")
    p_comment.add_argument(
        "--message", default="礼貌问价，请问方便了解下租金和起租日吗？", help="评论内容"
    )
    p_comment.add_argument("--top-n", type=int, default=3, help="评论前 N 篇")
    p_comment.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # run-all
    p_all = subparsers.add_parser("run-all", help="一键全流程执行")
    p_all.add_argument("--keywords", nargs="+", required=True, help="搜索关键词")
    p_all.add_argument("--limit", type=int, default=15, help="深度抓取上限")
    p_all.add_argument("--work-dir", default="./rental_work", help="工作目录")
    p_all.add_argument("--output-png", help="海报输出路径")

    args = parser.parse_args()
    pipeline = RentalPipeline(work_dir=args.work_dir)

    if args.command == "search":
        pipeline.search(args.keywords)
    elif args.command == "filter":
        pipeline.filter_candidates(args.raw_file)
    elif args.command == "inspect":
        pipeline.inspect(args.filtered_file, max_inspect=args.limit)
    elif args.command == "analyze":
        pipeline.analyze(args.detailed_file, args.min_budget, args.max_budget)
    elif args.command == "render-poster":
        pipeline.render_poster(args.analyzed_file, output_png_path=args.output)
    elif args.command == "comment":
        pipeline.batch_comment(args.analyzed_file, message=args.message, top_n=args.top_n)
    elif args.command == "run-all":
        raw = pipeline.search(args.keywords)
        filtered = pipeline.filter_candidates(raw)
        detailed = pipeline.inspect(filtered, max_inspect=args.limit)
        analyzed = pipeline.analyze(detailed)
        png = pipeline.render_poster(analyzed, output_png_path=args.output_png)
        print(f"\n[Run-All] 全流程执行完毕！海报路径: {png}")


if __name__ == "__main__":
    main()
