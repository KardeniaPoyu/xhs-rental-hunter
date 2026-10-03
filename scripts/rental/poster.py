"""长图报告渲染（Pillow）。

- 跨平台中文字体自动发现（Windows/macOS/Linux，可用 XHS_FONT 环境变量指定）
- 按像素宽度换行，超长自动省略，卡片高度自适应，不会再出现文字溢出画布
- 缩略图等比裁切（cover），不拉伸变形；并发下载并缓存
- 既能直接吃规则研判结果，也能吃 Claude 复核后写的 curated.json（字段更丰富）
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .rules import Requirements

# ─── 字体 ────────────────────────────────────────────────────────────────────

_FONT_CANDIDATES: list[tuple[str, str | None]] = [
    # (regular, bold)
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", None),
    ("/System/Library/Fonts/PingFang.ttc", None),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", None),
    ("/System/Library/Fonts/STHeiti Medium.ttc", None),
    ("/Library/Fonts/Arial Unicode.ttf", None),
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ),
    (
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    ),
    (
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    ),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", None),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None),
]

FONT_HELP = (
    "未找到可用的中文字体。请任选其一：\n"
    "  1) 设置环境变量 XHS_FONT=/path/to/中文字体.ttc\n"
    "  2) Linux: sudo apt install fonts-noto-cjk（或 fonts-wqy-microhei）\n"
    "  3) macOS/Windows 自带苹方/微软雅黑，通常无需处理"
)


class FontNotFoundError(RuntimeError):
    pass


def find_fonts() -> tuple[str, str]:
    env = os.environ.get("XHS_FONT")
    if env and Path(env).exists():
        return env, os.environ.get("XHS_FONT_BOLD") or env
    for reg, bold in _FONT_CANDIDATES:
        if Path(reg).exists():
            return reg, bold if bold and Path(bold).exists() else reg
    if shutil.which("fc-match"):
        try:
            path = subprocess.run(
                ["fc-match", "-f", "%{file}", ":lang=zh"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if path and Path(path).exists():
                return path, path
        except (OSError, subprocess.SubprocessError):
            pass
    raise FontNotFoundError(FONT_HELP)


class Fonts:
    def __init__(self) -> None:
        reg, bold = find_fonts()
        self._reg, self._bold = reg, bold
        self._cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(self._bold if bold else self._reg, size)
        return self._cache[key]


# ─── 颜色 ────────────────────────────────────────────────────────────────────

INK = (15, 23, 42)
BODY = (51, 65, 85)
MUTED = (100, 116, 139)
LINE = (226, 232, 240)
BG = (248, 250, 252)
GREEN = (16, 150, 100)
AMBER = (217, 119, 6)
RED = (220, 38, 38)
BLUE = (37, 99, 235)

TIER_STYLE = {
    "trusted": ("真实整租", GREEN, (240, 253, 244), (187, 247, 208)),
    "verify": ("待核实", AMBER, (255, 251, 235), (253, 230, 138)),
    "suspect": ("避坑案例", RED, (254, 242, 242), (252, 165, 165)),
    "excluded": ("已排除", MUTED, (241, 245, 249), LINE),
}

W = 1260
PAD = 40
CARD_X0, CARD_X1 = PAD, W - PAD
THUMB_W, THUMB_H, THUMB_GAP = 150, 112, 10
TEXT_X = CARD_X0 + 24 + 3 * THUMB_W + 2 * THUMB_GAP + 28
TEXT_W = CARD_X1 - 24 - TEXT_X
LABEL_W = 106
_NO_LINE_START = set("，。；：、！？）」』”’》,.;:!?)")


# ─── 文本工具 ────────────────────────────────────────────────────────────────


def text_w(font: ImageFont.FreeTypeFont, s: str) -> int:
    return int(font.getlength(s))


def wrap(font: ImageFont.FreeTypeFont, text: str, max_w: int, max_lines: int = 99) -> list[str]:
    """按像素宽度逐字换行（中文无空格），超出 max_lines 时末行加省略号。"""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    lines: list[str] = []
    cur = ""
    for ch in text:
        if text_w(font, cur + ch) <= max_w or (ch in _NO_LINE_START and cur):
            cur += ch  # 标点不出现在行首（允许轻微超出）
            continue
        lines.append(cur)
        cur = ch
        if len(lines) == max_lines:
            break
    else:
        if cur:
            lines.append(cur)
        return lines
    last = lines[-1]
    while last and text_w(font, last + "…") > max_w:
        last = last[:-1]
    lines[-1] = last + "…"
    return lines


def fit(font: ImageFont.FreeTypeFont, text: str, max_w: int) -> str:
    out = wrap(font, text, max_w, 1)
    return out[0] if out else ""


# ─── 图片 ────────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
}


def fetch_image(url: str, cache_dir: Path) -> Image.Image | None:
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    cache = cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".img")
    data: bytes | None = None
    if cache.exists() and cache.stat().st_size > 1000:
        data = cache.read_bytes()
    elif url.startswith(("http://", "https://")):
        import requests

        for attempt in range(2):
            try:
                r = requests.get(url, headers=_HEADERS, timeout=10)
                if r.ok and len(r.content) > 1000:
                    data = r.content
                    cache.write_bytes(data)
                    break
            except requests.RequestException:
                time.sleep(0.5 * (attempt + 1))
    elif Path(url).exists():
        data = Path(url).read_bytes()
    if not data:
        return None
    try:
        return Image.open(BytesIO(data)).convert("RGB")
    except Exception:
        return None


def prefetch(urls: Iterable[str], cache_dir: Path) -> dict[str, Image.Image | None]:
    urls = list(dict.fromkeys(u for u in urls if u))
    with ThreadPoolExecutor(max_workers=6) as ex:
        imgs = list(ex.map(lambda u: fetch_image(u, cache_dir), urls))
    return dict(zip(urls, imgs, strict=True))


# ─── 数据 → 卡片条目 ─────────────────────────────────────────────────────────


def _price_text(r: dict) -> str:
    if r.get("price_text"):
        return str(r["price_text"])
    p = (r.get("facts") or {}).get("price") or {}
    if p.get("low") and p.get("high"):
        return f"{p['low']}-{p['high']}元/月"
    if p.get("price"):
        pay = (r.get("facts") or {}).get("payment")
        return f"{p['price']}元/月" + (f"（{pay}）" if pay else "")
    return "价格私询"


def to_entry(r: dict) -> dict:
    """把 analyzed_results 的一条（或 curated 条目）规范成卡片字段。curated 中的字段优先。"""
    f = r.get("facts") or {}
    meta_parts = [
        f.get("layout") if f.get("layout") and f.get("layout") != "户型未知" else "",
        f"{f['area_sqm']:g}㎡" if f.get("area_sqm") else "",
        "、".join(f.get("metro") or [])[:20],
    ]
    age = r.get("age_days")
    auto_meta = " · ".join(x for x in meta_parts if x)
    rows: list[tuple[str, str]] = []
    if r.get("rows"):
        rows = [(str(k), str(v)) for k, v in r["rows"]]
    else:
        if r.get("config"):
            rows.append(("房源配置", r["config"]))
        reason = r.get("reason") or "、".join(h["label"] for h in r.get("positives", [])[:3])
        rows.append(("真实特征", reason or "暂无明确的个人转租细节"))
        signing = r.get("signing") or (f.get("lease") and f"租期：{f['lease']}") or ""
        if signing:
            rows.append(("签约/租期", signing))
    diagnosis = r.get("diagnosis") or (
        "；".join(h["label"] for h in sorted(r.get("risks", []), key=lambda h: h["weight"]))
        or "未发现中介/二房东特征，仍需按看房铁律线下核验。"
    )
    tier = r.get("tier") or "verify"
    if r.get("is_warning"):
        tier = "suspect"
    return {
        "id": r.get("id", ""),
        "headline": r.get("headline") or r.get("title") or "(无标题)",
        "price_text": _price_text(r),
        "meta": r.get("meta") or auto_meta,
        "author": r.get("author", ""),
        "age": f"{age:.0f} 天前" if isinstance(age, (int, float)) else (r.get("age") or ""),
        "score": r.get("trust_score", r.get("score")),
        "verdict": r.get("verdict_text") or r.get("verdict", ""),
        "tier": tier,
        "tag": r.get("tag") or "",
        "rows": rows,
        "diagnosis": diagnosis,
        "images": list(r.get("images") or [])[:3],
    }


def select_entries(results: list[dict], top_n: int = 5) -> list[dict]:
    """没有 curated.json 时的自动选择：最多 top_n 条可推荐 + 1 条典型避坑案例。"""
    good = [r for r in results if r["tier"] in ("trusted", "verify")]
    good.sort(key=lambda r: (bool(r["mismatches"]), r["tier"] != "trusted", -r["trust_score"]))
    picks = [to_entry(r) for r in good[:top_n]]
    bad = [r for r in results if r["tier"] == "suspect"]
    if bad:
        bad.sort(key=lambda r: (-len(r["risks"]), r["trust_score"]))
        e = to_entry(bad[0])
        e["tier"] = "suspect"
        picks.append(e)
    return picks


# ─── 渲染 ────────────────────────────────────────────────────────────────────


@dataclass
class _Ctx:
    fonts: Fonts
    images: dict[str, Image.Image | None]


def _paste_thumb(
    canvas: Image.Image, img: Image.Image | None, x: int, y: int, fonts: Fonts
) -> None:
    d = ImageDraw.Draw(canvas)
    if img is not None:
        thumb = ImageOps.fit(img, (THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
        mask = Image.new("L", (THUMB_W, THUMB_H), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, THUMB_W - 1, THUMB_H - 1], 8, fill=255)
        canvas.paste(thumb, (x, y), mask)
    else:
        d.rounded_rectangle([x, y, x + THUMB_W, y + THUMB_H], 8, fill=(241, 245, 249), outline=LINE)
        f = fonts.get(14)
        label = "暂无实拍"
        d.text(
            (x + (THUMB_W - text_w(f, label)) // 2, y + THUMB_H // 2 - 9),
            label,
            font=f,
            fill=(148, 163, 184),
        )


def _draw_card(e: dict, idx: int, ctx: _Ctx) -> Image.Image:
    ff = ctx.fonts
    name, color, soft_bg, soft_line = TIER_STYLE.get(e["tier"], TIER_STYLE["verify"])
    is_warn = e["tier"] == "suspect"
    tmp = Image.new("RGB", (W, 1200), (255, 255, 255))
    d = ImageDraw.Draw(tmp)
    x0, y = CARD_X0 + 24, 20

    # 标签 + 标题 + 价格
    tag = e["tag"] or (f"【{name}】" if is_warn else f"TOP {idx} · {name}")
    ft = ff.get(15, True)
    tw = text_w(ft, tag) + 22
    d.rounded_rectangle([x0, y + 2, x0 + tw, y + 32], 6, fill=color)
    d.text((x0 + 11, y + 7), tag, font=ft, fill=(255, 255, 255))

    fp = ff.get(24, True)
    price = fit(fp, e["price_text"], 330)
    pw = text_w(fp, price)
    d.text((CARD_X1 - 24 - pw, y + 2), price, font=fp, fill=RED)

    fh = ff.get(23, True)
    hx = x0 + tw + 14
    d.text((hx, y + 2), fit(fh, e["headline"], CARD_X1 - 24 - pw - 20 - hx), font=fh, fill=INK)
    y += 44

    meta = [e["meta"], f"发帖人：{e['author']}" if e["author"] else "", e["age"]]
    if e.get("score") is not None:
        meta.append(f"规则分 {e['score']}")
    fm = ff.get(14)
    d.text(
        (x0, y), fit(fm, "  |  ".join(m for m in meta if m), CARD_X1 - 24 - x0), font=fm, fill=MUTED
    )
    y += 30
    top = y

    # 缩略图
    tx = x0
    for i in range(3):
        url = e["images"][i] if i < len(e["images"]) else ""
        _paste_thumb(tmp, ctx.images.get(url), tx, top, ff)
        tx += THUMB_W + THUMB_GAP

    # 右侧信息行
    fl, fb = ff.get(16, True), ff.get(16)
    ty = top
    for label, value in e["rows"]:
        d.text((TEXT_X, ty), f"【{label}】", font=fl, fill=INK)
        lines = wrap(fb, value, TEXT_W - LABEL_W, 2)
        for j, ln in enumerate(lines or [""]):
            d.text((TEXT_X + LABEL_W, ty + 1 + j * 23), ln, font=fb, fill=BODY)
        ty += max(1, len(lines)) * 23 + 8

    # 诊断框
    fd = ff.get(15)
    dl = wrap(fd, e["diagnosis"], TEXT_W - LABEL_W - 24, 3)
    box_h = 16 + max(1, len(dl)) * 22
    ty += 2
    d.rounded_rectangle([TEXT_X, ty, CARD_X1 - 24, ty + box_h], 6, fill=soft_bg, outline=soft_line)
    d.text((TEXT_X + 10, ty + 8), "【鉴别诊断】", font=ff.get(15, True), fill=color)
    for j, ln in enumerate(dl):
        d.text((TEXT_X + 12 + LABEL_W, ty + 9 + j * 22), ln, font=fd, fill=color)
    ty += box_h

    bottom = max(top + THUMB_H, ty) + 22
    card = Image.new("RGB", (W, bottom + 1), BG)
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([CARD_X0, 0, CARD_X1, bottom], 14, fill=255)
    card.paste(tmp.crop((0, 0, W, bottom + 1)), (0, 0), mask)
    ImageDraw.Draw(card).rounded_rectangle(
        [CARD_X0, 0, CARD_X1, bottom],
        14,
        outline=color if is_warn else LINE,
        width=2 if is_warn else 1,
    )
    return card


def _draw_header(title: str, subtitle: str, chips: list[str], ff: Fonts) -> Image.Image:
    h = 196 if chips else 140
    img = Image.new("RGB", (W, h), INK)
    d = ImageDraw.Draw(img)
    d.text(
        (PAD + 10, 30),
        fit(ff.get(34, True), title, W - 2 * PAD),
        font=ff.get(34, True),
        fill=(255, 255, 255),
    )
    d.text(
        (PAD + 10, 84),
        fit(ff.get(18), subtitle, W - 2 * PAD),
        font=ff.get(18),
        fill=(203, 213, 225),
    )
    x = PAD + 10
    colors = [RED, AMBER, BLUE, GREEN]
    fc = ff.get(15)
    for i, c in enumerate(chips):
        cw = text_w(fc, c) + 24
        if x + cw > W - PAD:
            break
        d.rounded_rectangle(
            [x, 128, x + cw, 164], 6, fill=(30, 41, 59), outline=colors[i % 4], width=2
        )
        d.text((x + 12, 137), c, font=fc, fill=(241, 245, 249))
        x += cw + 12
    return img


FOOTER_TIPS = [
    "核验身份：请转租人出示近 3~6 个月本人名下的水电燃气缴费记录或在此地址的快递/外卖订单，"
    "确认真的住在这里。",
    "直签房东：见房东本人，核对房产证与身份证原件；押金、租金只转入房东同名账户，不给“代收人”。",
    "警惕串串房：进门关窗 3 分钟闻气味，看踢脚线、柜体是否为廉价颗粒板或新贴地板革；"
    "新装修可要求甲醛检测报告。",
]


def _draw_footer(ff: Fonts) -> Image.Image:
    fb = ff.get(15)
    lines: list[str] = []
    for i, tip in enumerate(FOOTER_TIPS, 1):
        for j, ln in enumerate(wrap(fb, tip, W - 2 * PAD - 80, 3)):
            lines.append(f"{i}. {ln}" if j == 0 else f"    {ln}")
    h = 64 + len(lines) * 24 + 40
    img = Image.new("RGB", (W, h), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [CARD_X0, 0, CARD_X1, h - 30], 14, fill=(241, 245, 249), outline=(203, 213, 225)
    )
    d.text((CARD_X0 + 24, 18), "【线下看房三条铁律】", font=ff.get(17, True), fill=INK)
    for i, ln in enumerate(lines):
        d.text((CARD_X0 + 24, 52 + i * 24), ln, font=fb, fill=(71, 85, 105))
    d.text(
        (CARD_X0, h - 22),
        "规则分为关键词初判，仅供参考；结论以线下核验为准。",
        font=ff.get(13),
        fill=(148, 163, 184),
    )
    return img


def _req_chips(req: Requirements | None) -> list[str]:
    if not req:
        return []
    chips = []
    if req.budget_min or req.budget_max:
        chips.append(f"预算 {req.budget_min or '不限'}–{req.budget_max or '不限'} 元/月")
    if req.bedrooms is not None:
        chips.append("户型 " + "/".join("开间" if b == 0 else f"{b}居" for b in req.bedrooms))
    if req.max_age_days:
        chips.append(f"{req.max_age_days} 天内发布")
    if req.city:
        chips.append(f"城市 {req.city}")
    if req.must_have:
        chips.append("区域 " + "、".join(req.must_have[:4]))
    return chips


def render(
    entries: list[dict],
    output: Path,
    cache_dir: Path,
    title: str = "",
    subtitle: str = "",
    req: Requirements | None = None,
) -> Path:
    fonts = Fonts()
    cards_in = [to_entry(e) for e in entries]
    images = prefetch((u for e in cards_in for u in e["images"]), cache_dir)
    ctx = _Ctx(fonts, images)

    n_good = sum(e["tier"] != "suspect" for e in cards_in)
    title = title or "小红书个人整租精选 · 防中介避坑报告"
    subtitle = subtitle or (
        f"{time.strftime('%Y-%m-%d')} 生成 · 精选 {n_good} 套"
        + (" + 1 个避坑案例" if n_good < len(cards_in) else "")
        + " · 已排查中介马甲、二房东、商业公寓与合租"
    )
    parts = [_draw_header(title, subtitle, _req_chips(req), fonts)]
    k = 0
    for e in cards_in:
        if e["tier"] != "suspect":
            k += 1
        parts.append(Image.new("RGB", (W, 18), BG))
        parts.append(_draw_card(e, k, ctx))
    parts.append(Image.new("RGB", (W, 18), BG))
    parts.append(_draw_footer(fonts))

    total_h = sum(p.height for p in parts)
    canvas = Image.new("RGB", (W, total_h), BG)
    y = 0
    for p in parts:
        canvas.paste(p, (0, y))
        y += p.height
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, optimize=True)
    return output


__all__ = ["FontNotFoundError", "find_fonts", "render", "select_entries", "to_entry", "wrap"]
