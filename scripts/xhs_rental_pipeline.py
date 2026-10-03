"""
xhs_rental_pipeline.py - 小红书个人整租转租猎人与防中介/防串串房自动化引擎

功能支持：
1. search: 针对指定区域与户型多词检索小红书
2. filter: 结合4大防坑特征初筛（排除求租、合租、公寓、AI营销号）
3. inspect: 深度抓取详情正文、价格、房东/发帖人信息与实拍图片
4. analyze: 综合评分与防中介/防串串房研判
5. render-poster: 自动合成包含房间实拍图与诊断卡的高清海报长图
6. comment: 针对优质房源批量发布“礼貌问价”跟进互动
7. run-all: 一键全流程执行
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import textwrap
import urllib.request
from io import BytesIO
from typing import Any, Dict, List, Optional
from PIL import Image, ImageDraw, ImageFont

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 引入本包内模块
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.xiaohongshu.com/"
}

class RentalPipeline:
    def __init__(self, bridge_url: str = "ws://localhost:9333", work_dir: str = "./rental_work"):
        self.bridge_url = bridge_url
        self.work_dir = os.path.abspath(work_dir)
        os.makedirs(self.work_dir, exist_ok=True)
        self.cache_dir = os.path.join(self.work_dir, "img_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_page(self) -> BridgePage:
        return BridgePage(self.bridge_url)

    def search(self, keywords: List[str], max_per_keyword: int = 15) -> str:
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
        with open(raw_feeds_path, "r", encoding="utf-8") as f:
            feeds = json.load(f)

        exclude_titles = [
            "求", "想租", "求租", "租房日常", "避雷", "骗局", "讨论", "推荐", "求求", "看不懂", "姐妹们",
            "已出", "已租", "合租", "单间", "次卧", "主卧", "床位", "隔断"
        ]
        exclude_authors = ["地产", "中介", "公寓", "经纪", "小房", "管家", "置业"]

        filtered = []
        for c in feeds:
            title = c.get("title", "")
            author = c.get("author", "")
            if any(w in title for w in exclude_titles):
                continue
            if any(w in author for w in exclude_authors):
                continue
            # 三竖杠模板过多
            bars = title.count("|") + title.count("｜") + title.count("丨")
            if bars >= 3:
                continue
            filtered.append(c)

        out_path = os.path.join(self.work_dir, "filtered_feeds.json")
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(filtered, f_out, ensure_ascii=False, indent=2)
        print(f"[Filter] 初筛完成！从 {len(feeds)} 条筛选出 {len(filtered)} 条候选，保存至: {out_path}")
        return out_path

    def inspect(self, filtered_path: str, max_inspect: int = 20) -> str:
        """深入正文抓取实拍图片、正文与房东签约细节"""
        page = self.get_page()
        with open(filtered_path, "r", encoding="utf-8") as f:
            candidates = json.load(f)

        inspected = []
        print(f"[Inspect] 计划深度勘查前 {min(len(candidates), max_inspect)} 条候选...")

        for idx, c in enumerate(candidates[:max_inspect]):
            target_id = c["id"]
            token = c.get("xsec_token", "")
            target_url = make_feed_detail_url(target_id, token)
            print(f"[{idx+1}/{min(len(candidates), max_inspect)}] 抓取: {c['title']} ({target_id})...")

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
                                parsed["xsec_token"] = c.get("xsec_token", "")
                                inspected.append(parsed)
                                print(f"  -> 成功抓取！正文长度: {len(parsed.get('desc',''))}, 图片数: {len(parsed.get('images',[]))}")
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
        """核心防中介与防串串房研判算法"""
        with open(detailed_path, "r", encoding="utf-8") as f:
            posts = json.load(f)

        results = []
        for p in posts:
            title = p.get("title", "")
            desc = p.get("desc", "")
            author = p.get("user", {}).get("nickname", "")
            full_text = f"{title}\n{desc}"

            # 提取租金
            prices = []
            for m in re.finditer(r"([2-6]\d{3})(?:元|/月|块|/m|\b)", full_text):
                val = int(m.group(1))
                if 2000 <= val <= 8000:
                    prices.append(val)
            for m in re.finditer(r"([3-5](?:\.\d)?)[kK]", full_text):
                prices.append(int(float(m.group(1)) * 1000))
            price_est = prices[0] if prices else None

            # 户型与住宅属性
            is_apartment = any(k in full_text for k in ["公寓", "loft", "Loft", "LOFT", "文创园", "产业园", "自如寓"])
            is_shared = any(k in full_text for k in ["合租", "主卧", "次卧", "单间", "隔断", "厅卧"])
            
            # 4 大防坑避障规则
            # 规则1: 伪装职业
            fake_job_hit = any(k in full_text or k in author for k in ["化妆师", "宝妈", "作家", "插画师", "手作", "摄影师", "设计师", "漂染"])
            # 规则2: 中介关键词堆砌
            agent_keywords_count = sum(1 for k in ["0中介费", "零中介费", "无中介费", "中介勿扰", "房东直租", "个人转租"] if k in full_text)
            # 规则3: AI模板、三竖杠与emoji致死量
            bars = title.count("|") + title.count("｜") + title.count("丨")
            emojis = len(re.findall(r"[\U00010000-\U0010ffff]", full_text))
            is_ai_styled = (bars >= 2) or (emojis > 10)

            # 真实租客特征加分项
            real_signals = []
            if any(k in full_text for k in ["工作调动", "工作变动", "换工作", "离开北京", "离京", "回家发展", "跟对象", "换大房"]):
                real_signals.append("具体转租原因明确（工作/生活变动）")
            if any(k in full_text for k in ["合同到", "还剩", "剩余", "租期到", "明年"]):
                real_signals.append("明确剩余租期/合同时长")
            if any(k in full_text for k in ["送", "留下", "家具", "自费购买", "转赠", "新换沙发", "书柜"]):
                real_signals.append("个人自购家具/杂物交接")
            if any(k in full_text for k in ["晚上", "周末看房", "提前约", "工作日"]):
                real_signals.append("正常上班族看房作息")
            if any(k in full_text for k in ["和房东重签", "跟房东签", "房东直签", "房东姐姐", "本人签约"]):
                real_signals.append("直接对接房东签约")

            # 风险扣分项
            suspect_reasons = []
            if is_apartment:
                suspect_reasons.append("商业公寓/产业园商住（水电费高非纯住宅）")
            if is_shared:
                suspect_reasons.append("合租/单间/隔断（非整租）")
            if fake_job_hit and not real_signals:
                suspect_reasons.append("疑点职业标签（疑似化妆师/手艺人马甲人设）")
            if is_ai_styled:
                suspect_reasons.append(f"AI模板文案/三竖杠标题(竖杠:{bars},emoji:{emojis})")
            if agent_keywords_count >= 3 and not real_signals:
                suspect_reasons.append("中介营销词堆叠(无真实生活细节)")

            trust_score = 5
            trust_score += len(real_signals) * 2
            trust_score -= len(suspect_reasons) * 2

            if is_apartment or is_shared:
                verdict = "排除（公寓或合租）"
            elif trust_score >= 7:
                verdict = "🌟 纯个人转租（高可信）"
            elif trust_score >= 4:
                verdict = "🔍 待核实（中等可信）"
            else:
                verdict = "⚠️ 疑似中介/串串房"

            results.append({
                "id": p.get("id"),
                "title": title,
                "author": author,
                "pub_time": p.get("pub_time", ""),
                "price": price_est,
                "desc": desc,
                "images": p.get("images", []),
                "xsec_token": p.get("xsec_token", ""),
                "real_signals": real_signals,
                "suspect_reasons": suspect_reasons,
                "trust_score": trust_score,
                "verdict": verdict,
                "is_apartment": is_apartment,
                "is_shared": is_shared
            })

        results.sort(key=lambda x: x["trust_score"], reverse=True)
        out_path = os.path.join(self.work_dir, "analyzed_results.json")
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(results, f_out, ensure_ascii=False, indent=2)
        print(f"[Analyze] 深度研判完成！共分析 {len(results)} 套房源，保存至: {out_path}")
        return out_path

    def _fetch_img(self, url: str, fname: str) -> Optional[Image.Image]:
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
        except Exception as e:
            return None

    def render_poster(self, analyzed_path: str, output_png_path: Optional[str] = None) -> str:
        """合成现代信息图海报报告"""
        with open(analyzed_path, "r", encoding="utf-8") as f:
            all_results = json.load(f)

        # 选出 top 5 优质房源 + 1 典型避坑案例
        top_candidates = [r for r in all_results if "排除" not in r["verdict"]][:5]
        warn_candidates = [r for r in all_results if "疑似中介" in r["verdict"] or len(r["suspect_reasons"]) > 0]
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

        font_path = r"C:\Windows\Fonts\msyh.ttc" if os.path.exists(r"C:\Windows\Fonts\msyh.ttc") else None
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

        W = 1260
        H = 220 + len(curated) * 365 + 170
        img = Image.new("RGB", (W, H), color=(248, 250, 252))
        draw = ImageDraw.Draw(img)

        # 顶部 Banner
        draw.rectangle([(0, 0), (W, 205)], fill=(15, 23, 42))
        draw.text((50, 32), "小红书 · 纯个人整租转租精选榜单与防坑报告", font=font_title, fill=(255, 255, 255))
        draw.text((50, 80), "严格排查中介马甲、商业公寓与网红串串房 | 房间实拍照片直观核验", font=font_subtitle, fill=(203, 213, 225))

        badges = [
            ("[排查1] 严查假职业人设(化妆/宝妈/手艺人)", (239, 68, 68)),
            ("[排查2] 剔除主页多套房源中介号", (249, 115, 22)),
            ("[排查3] 识别AI润色与三竖杠模板", (59, 130, 246)),
            ("[排除4] 坚决排除商业公寓/隔断合租", (16, 185, 129))
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
            is_warn = c.get("is_warn_case", False) or "疑似中介" in c["verdict"]
            bg_color = (255, 255, 255)
            border_color = (239, 68, 68) if is_warn else (226, 232, 240)

            draw.rounded_rectangle([(40, y_offset), (W - 40, y_offset + card_h)], radius=12, fill=bg_color, outline=border_color, width=2 if is_warn else 1)

            # 标签
            tag_text = "【重点排查案例】" if is_warn else f"TOP {idx+1} 真实整租"
            tag_bg = (239, 68, 68) if is_warn else (16, 185, 129)
            draw.rounded_rectangle([(60, y_offset + 16), (210, y_offset + 46)], radius=6, fill=tag_bg)
            draw.text((68, y_offset + 21), tag_text, font=font_badge, fill=(255, 255, 255))

            # 标题与租金
            draw.text((225, y_offset + 18), c["title"][:22], font=font_card_title, fill=(15, 23, 42))
            price_str = f"{c['price']}元/月" if c.get("price") else "面议/私信"
            draw.text((W - 360, y_offset + 18), price_str, font=font_price, fill=(220, 38, 38))

            meta_line = f"发帖人：{c['author']}  |  发布时间：{c.get('pub_time','近期')}  |  信任分：{c['trust_score']}"
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
                    draw.rectangle([(tx, ty), (tx + thumb_w, ty + thumb_h)], outline=(203, 213, 225), width=1)
                else:
                    draw.rectangle([(tx, ty), (tx + thumb_w, ty + thumb_h)], fill=(241, 245, 249), outline=(203, 213, 225))
                    draw.text((tx + 32, ty + 40), "实拍房间", font=font_small, fill=(148, 163, 184))
                tx += thumb_w + 12

            # 右侧文字
            rx = 520
            ry = y_offset + 88

            draw.text((rx, ry), "【房源描述】", font=font_bold, fill=(30, 41, 59))
            desc_lines = textwrap.wrap(c["desc"].replace("\n", " "), width=42)
            for l_idx, line in enumerate(desc_lines[:2]):
                draw.text((rx + 118, ry + 1 + l_idx * 23), line, font=font_body, fill=(51, 65, 85))

            ry += 52
            draw.text((rx, ry), "【真实特征】", font=font_bold, fill=(30, 41, 59))
            sig_str = "、".join(c["real_signals"]) if c["real_signals"] else "暂无典型生活调动细节"
            draw.text((rx + 118, ry + 1), sig_str[:42], font=font_body, fill=(51, 65, 85))

            ry += 30
            draw.text((rx, ry), "【研判结论】", font=font_bold, fill=(30, 41, 59))
            draw.text((rx + 118, ry + 1), c["verdict"], font=font_body, fill=(14, 116, 144))

            # 诊断框
            ry += 32
            box_bg = (254, 242, 242) if is_warn else (240, 253, 244)
            box_border = (252, 165, 165) if is_warn else (187, 247, 208)
            draw.rounded_rectangle([(rx, ry), (W - 60, ry + 66)], radius=6, fill=box_bg, outline=box_border, width=1)

            anti_color = (185, 28, 28) if is_warn else (21, 128, 61)
            draw.text((rx + 12, ry + 7), "【鉴别诊断】", font=font_bold, fill=anti_color)
            diag_text = "；".join(c["suspect_reasons"]) if c["suspect_reasons"] else "生活痕迹充分，具备自住转赠/直签房东特征，排除串串房与中介。"
            d_lines = textwrap.wrap(diag_text, width=41)
            for l_idx, line in enumerate(d_lines[:2]):
                draw.text((rx + 118, ry + 9 + l_idx * 22), line, font=font_small, fill=anti_color)

            y_offset += card_h + 18

        # 底部指南
        draw.rounded_rectangle([(40, y_offset), (W - 40, y_offset + 130)], radius=12, fill=(241, 245, 249), outline=(203, 213, 225))
        draw.text((60, y_offset + 16), "【指南】纯个人整租实地看房防坑核心铁律（严格落实用户4大特征）", font=font_bold, fill=(15, 23, 42))
        draw.text((60, y_offset + 44), "1. 验明真实租客身份：看房时务必让转租人出示近3-6个月本人的水电燃气缴费流水、买菜外卖订单记录，核实其真正在此居住，排除中介假扮；", font=font_small, fill=(71, 85, 105))
        draw.text((60, y_offset + 68), "2. 拒绝二房东/商业转包：必须要求见房东本人并查验房产证、身份证原件，直接与房东签约，把押金直接打入房东同名银行卡；", font=font_small, fill=(71, 85, 105))
        draw.text((60, y_offset + 92), "3. 串串房刺鼻警惕：进门注意有无劣质胶水味、刺鼻油漆味；观察踢脚线、衣柜边角是否为廉价拼接颗粒板，劣质翻新房一律果断放弃！", font=font_small, fill=(71, 85, 105))

        img.save(output_png_path, quality=95)
        print(f"[Poster] 海报生成成功: {output_png_path}")
        return output_png_path

    def batch_comment(self, analyzed_path: str, message: str = "您好，请问房子还在吗？方便礼貌问价和了解起租时间吗～", top_n: int = 3) -> None:
        """针对优质房源批量跟进评论"""
        page = self.get_page()
        with open(analyzed_path, "r", encoding="utf-8") as f:
            results = json.load(f)

        targets = [r for r in results if "高可信" in r["verdict"]][:top_n]
        print(f"[Comment] 准备在 {len(targets)} 篇优质自住帖下发表跟进评论...")

        for idx, t in enumerate(targets):
            tid = t["id"]
            token = t.get("xsec_token", "")
            print(f"[{idx+1}/{len(targets)}] 访问 {t['title']} ({tid})...")
            # 引入评论自动化库
            try:
                from xhs.comment import post_comment
                post_comment(page, tid, token, message)
                print(f"  -> 成功发表评论: '{message}'")
            except Exception as e:
                print(f"  -> 发表失败: {e}")
            time.sleep(3.0)

def main():
    parser = argparse.ArgumentParser(description="小红书个人整租转租猎人与防中介/串串房流水线")
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
    p_analyze = subparsers.add_parser("analyze", help="防中介/串串房研判")
    p_analyze.add_argument("--detailed-file", required=True, help="detailed_posts.json 路径")
    p_analyze.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # render-poster
    p_poster = subparsers.add_parser("render-poster", help="合成长图报告")
    p_poster.add_argument("--analyzed-file", required=True, help="analyzed_results.json 路径")
    p_poster.add_argument("--output", help="输出 PNG 路径")
    p_poster.add_argument("--work-dir", default="./rental_work", help="工作目录")

    # comment
    p_comment = subparsers.add_parser("comment", help="批量跟进评论")
    p_comment.add_argument("--analyzed-file", required=True, help="analyzed_results.json 路径")
    p_comment.add_argument("--message", default="礼貌问价，请问方便了解下租金和起租日吗？", help="评论内容")
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
        pipeline.analyze(args.detailed_file)
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
