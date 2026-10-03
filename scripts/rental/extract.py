"""从房源帖文本中抽取结构化字段（租金、户型、面积、付款方式、租期、入住时间）。

设计原则：
- 城市无关：不写死任何城市/商圈名。
- 带上下文判断：押金、中介费、水电物业、年份、线路号等数字不会被误当成租金。
- 每个字段都尽量返回原文片段（evidence），方便 Claude 和用户复核。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

# ─── 文本归一化 ──────────────────────────────────────────────────────────────

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}


_CAPITAL_DIGITS = str.maketrans("零壹贰貳叁參肆伍陆陸柒捌玖〇", "01223345567890")
_OBFUSCATED_RENT = re.compile(r"(?:zu|租)\s*(?:金|j)|房\s*zu|zu\s*房", re.IGNORECASE)
_TOPIC_TAG = re.compile(r"#([^#\[\n]{1,30})\[话题\]#?")
_STICKER = re.compile(r"\[[^\[\]\n]{1,8}R\]")


def clean_post_text(text: str) -> str:
    """去掉小红书正文里的话题标记（#xx[话题]#）与表情代码（[笑哭R]），保留话题文字。"""
    if not text:
        return ""
    s = _TOPIC_TAG.sub(r" #\1 ", text)
    s = _STICKER.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\s*#\s*$", "", s).strip()


def normalize(text: str) -> str:
    """NFKC 归一化（全角→半角、㎡→m2 等）并还原常见的防屏蔽写法。

    - 数字 emoji（5️⃣）、大写数字（叁伍）→ 阿拉伯数字
    - zu金 / 租j / 房zu → 租金
    - 去掉千分位逗号，统一竖杠
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = s.replace("\ufe0f", "").replace("\u20e3", "")
    s = s.translate(_CAPITAL_DIGITS)
    s = _OBFUSCATED_RENT.sub("租金", s)
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)
    s = s.replace("丨", "|").replace("｜", "|")
    return s


def snippet(text: str, start: int, end: int, pad: int = 8) -> str:
    """截取命中位置附近的原文片段，单行化。"""
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return text[a:b].replace("\n", " ").strip()


# ─── 租金 ────────────────────────────────────────────────────────────────────

# 出现在数字前面时，说明这个数字不是月租
_NON_RENT_PREFIX = re.compile(
    r"(押金|押(?![零一二两三0-3]\s*付)|中介费|服务费|管理费|物业费?|物业|水费|电费|燃气|网费|宽带|停车|车位|定金|违约金|"
    r"保洁|取暖|暖气|水电|面积|建面|套内)[^\d\n]{0,5}$"
)
# 出现在数字后面时，说明这个数字不是月租（年份、线路、楼层、面积…）
_NON_RENT_SUFFIX = re.compile(
    r"^\s*(年(?!付)|号|路|米|m(?![/每])|平|室|栋|期|届|层|楼|站|个|天|人|w|万)"
)

_PRICE_UNIT = r"(?:元|块|r|rmb|¥)?"
_MONTH = r"(?:/|每|一个?)\s*(?:月|m\b|mon)"

_RE_ANCHORED = re.compile(
    r"(租金|月租|房租|租价|价格|售价|月付|每月|一个月|月租金)[^\d\n]{0,8}?"
    r"(\d{3,5}(?:\.\d+)?|\d{1,2}(?:\.\d+)?\s*[kK千])"
)
_RE_WITH_MONTH = re.compile(
    rf"(\d{{3,5}}(?:\.\d+)?|\d{{1,2}}(?:\.\d+)?\s*[kK千])\s*{_PRICE_UNIT}\s*{_MONTH}",
    re.IGNORECASE,
)
_RE_WITH_YUAN = re.compile(r"(\d{3,5})\s*(?:元|块|rmb)", re.IGNORECASE)
_RE_K = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d)?\s*[kK千])(?![mM米])")
_RE_RANGE = re.compile(
    r"(\d{3,5}|\d{1,2}(?:\.\d)?[kK])\s*(?:-|~|～|到|至)\s*(\d{3,5}|\d{1,2}(?:\.\d)?[kK])"
    rf"\s*{_PRICE_UNIT}\s*(?:{_MONTH})?",
    re.IGNORECASE,
)
_RE_BARE = re.compile(r"(?<![\d.])(\d{4,5})(?![\d.])")
_RE_LINE_PRICE = re.compile(r"(?m)^\s*(\d{4,5})\s*(?=[,，。、 /]|$)")
# “不到55张”“月租45张”：张 = 百元
_RE_ZHANG = re.compile(
    r"(?:不到|约|大概|租金|房租|月租)?\s*(?<![\d.])(\d{2,3})\s*张(?![图照床桌椅卡纸票])"
)
# “5xxx”“5k+”：只知道千位
_RE_FUZZY = re.compile(r"(?<![\d.])([1-9])\s*(?:xxx|XXX|千多|k\+|K\+)")

PRICE_MIN, PRICE_MAX = 500, 60000


def _to_int_price(token: str) -> int | None:
    t = token.strip().lower().replace(" ", "")
    try:
        v = float(t[:-1]) * 1000 if t.endswith(("k", "千")) else float(t)
    except ValueError:
        return None
    n = round(v)
    return n if PRICE_MIN <= n <= PRICE_MAX else None


def _is_non_rent_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 10) : start]
    after = text[end : end + 3]
    return bool(_NON_RENT_PREFIX.search(before) or _NON_RENT_SUFFIX.match(after))


@dataclass
class PriceInfo:
    price: int | None = None  # 最可信的月租估计
    low: int | None = None  # 区间下限（若文案给了区间）
    high: int | None = None
    confidence: str = "none"  # high / medium / low / none
    evidence: str = ""
    candidates: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def extract_price(text: str) -> PriceInfo:
    """抽取月租。优先级：区间 > 关键词锚定 > 带“/月” > 带“元” > k 写法 > 标题裸数字。"""
    s = normalize(text)
    found: list[tuple[int, int, str]] = []  # (priority, value, evidence)

    for m in _RE_RANGE.finditer(s):
        if _is_non_rent_context(s, m.start(), m.end()):
            continue
        lo, hi = _to_int_price(m.group(1)), _to_int_price(m.group(2))
        if lo and hi and lo < hi <= lo * 1.6:
            info = PriceInfo(
                price=lo,
                low=lo,
                high=hi,
                confidence="medium",
                evidence=snippet(s, m.start(), m.end()),
                candidates=[lo, hi],
            )
            return info

    for prio, regex, grp in (
        (0, _RE_ANCHORED, 2),
        (1, _RE_WITH_MONTH, 1),
        (2, _RE_WITH_YUAN, 1),
        (3, _RE_K, 1),
    ):
        for m in regex.finditer(s):
            start, end = m.span(grp)
            if prio > 0 and _is_non_rent_context(s, start, end):
                continue
            v = _to_int_price(m.group(grp))
            if v:
                found.append((prio, v, snippet(s, m.start(), m.end())))

    if not found:
        for m in _RE_ZHANG.finditer(s):
            v = int(m.group(1)) * 100
            if PRICE_MIN <= v <= PRICE_MAX:
                found.append((3, v, snippet(s, m.start(), m.end())))

    if not found:
        m = _RE_FUZZY.search(s)
        if m:
            lo = int(m.group(1)) * 1000
            return PriceInfo(
                price=lo,
                low=lo,
                high=lo + 999,
                confidence="low",
                evidence=snippet(s, m.start(), m.end()),
                candidates=[lo],
            )

    if not found:
        # 兜底：仅在第一行（通常是标题）里找裸 4~5 位数
        first_line = s.split("\n", 1)[0]
        for m in _RE_BARE.finditer(first_line):
            if _is_non_rent_context(first_line, m.start(), m.end()):
                continue
            v = _to_int_price(m.group(1))
            if v and not (1990 <= v <= 2035):
                found.append((4, v, snippet(first_line, m.start(), m.end())))
        # 正文中独占行首的价格：“2300，房东直租”
        for m in _RE_LINE_PRICE.finditer(s):
            v = _to_int_price(m.group(1))
            if v and not (1990 <= v <= 2035):
                found.append((4, v, snippet(s, m.start(1), m.end(1))))

    if not found:
        return PriceInfo()

    found.sort(key=lambda x: x[0])
    prio, value, ev = found[0]
    conf = {0: "high", 1: "high", 2: "medium", 3: "medium"}.get(prio, "low")
    cands = sorted({v for _, v, _ in found})
    return PriceInfo(price=value, confidence=conf, evidence=ev, candidates=cands)


# ─── 户型 / 面积 / 付款方式 ──────────────────────────────────────────────────

_RE_ROOM_HALL = re.compile(r"([1-6一二两三四五六])\s*室\s*([0-3零一二两三])?\s*厅?")
_RE_JU = re.compile(r"([1-6一二两三四五六])\s*(?:居室?|房(?!东|源|租|子|间|主|本))")
_RE_STUDIO = re.compile(r"开间|一室户|studio|单身公寓", re.IGNORECASE)
_RE_AREA = re.compile(r"(\d{2,3}(?:\.\d)?)\s*(?:平米|平方米?|平(?![台方米层])|m2|㎡|m²)", re.I)
_RE_PAYMENT = re.compile(
    r"押\s*([零一二两三0-3])\s*付\s*(十二|[一二三六]|1[02](?!\d)|[1-9](?!\d))|(年付|半年付|季付|月付)"
)


def _cn_num(ch: str) -> int | None:
    if ch.isdigit():
        return int(ch)
    return _CN_DIGITS.get(ch)


def extract_bedrooms(text: str) -> int | None:
    """返回卧室数：0=开间，1=一居，2=两居……；识别不到返回 None。"""
    s = normalize(text)
    m = _RE_ROOM_HALL.search(s)
    if m:
        return _cn_num(m.group(1))
    m = _RE_JU.search(s)
    if m:
        return _cn_num(m.group(1))
    if _RE_STUDIO.search(s):
        return 0
    return None


def bedrooms_label(n: int | None) -> str:
    if n is None:
        return "户型未知"
    if n == 0:
        return "开间"
    return f"{'一两三四五六'[n - 1] if n <= 6 else n}居"


def extract_area(text: str) -> float | None:
    s = normalize(text)
    for m in _RE_AREA.finditer(s):
        v = float(m.group(1))
        if 8 <= v <= 300:
            return v
    return None


def extract_payment(text: str) -> str:
    s = normalize(text)
    m = _RE_PAYMENT.search(s)
    if not m:
        return ""
    if m.group(3):
        return m.group(3)
    return f"押{m.group(1)}付{m.group(2)}"


# ─── 租期 / 入住时间 / 交通 ──────────────────────────────────────────────────

_RE_LEASE = re.compile(
    r"((?:合同|租期|租约|房租)\s*(?:到|至|截止到?)\s*"
    r"(?:(?:明年|后年)\s*\d{1,2}\s*月(?:底|初|中)?"
    r"|(?:\d{2,4}\s*年)?\s*\d{1,2}\s*月(?:\d{1,2}\s*[日号])?(?:底|初|中)?"
    r"|\d{2,4}\s*[./-]\s*\d{1,2}(?:\s*[./-]\s*\d{1,2})?)"
    r"|(?:还剩|剩余|剩下?)\s*(?:\d{1,2}|[一二两三四五六七八九十]{1,3})\s*个?\s*月"
    r"|(?:租|签)到\s*(?:明年|后年|\d{2,4}\s*年)\s*\d{1,2}\s*月(?:底|初)?)"
)
_RE_MOVE_IN = re.compile(
    r"((?:\d{1,2}\s*月\s*\d{1,2}\s*[日号]?|\d{1,2}\s*[./]\s*\d{1,2})\s*(?:之?后|以后|起)?\s*(?:即?可|随时)?\s*(?:入住|搬入|起租)"
    r"|随时(?:可以)?入住|即可入住|立即入住|月底(?:可|就能)?入住|下个?月(?:初|中|底)?(?:可)?入住)"
)
_RE_METRO = re.compile(
    r"(\d{1,2}\s*号线|[一-龥]{1,6}站\s*(?:步行|走路)?\s*\d{1,2}\s*分钟|距离?[一-龥]{1,6}站\s*\d{2,4}\s*米)"
)


def _first(regex: re.Pattern[str], text: str) -> str:
    m = regex.search(normalize(text))
    return m.group(1).replace(" ", "") if m else ""


def extract_lease(text: str) -> str:
    return _first(_RE_LEASE, text)


def extract_move_in(text: str) -> str:
    return _first(_RE_MOVE_IN, text)


def extract_metro(text: str) -> list[str]:
    s = normalize(text)
    out: list[str] = []
    for m in _RE_METRO.finditer(s):
        v = m.group(1).replace(" ", "")
        if v not in out:
            out.append(v)
    return out[:4]


# ─── 汇总 ────────────────────────────────────────────────────────────────────


@dataclass
class ListingFacts:
    price: PriceInfo
    price_conflict: str
    bedrooms: int | None
    layout: str
    area_sqm: float | None
    payment: str
    lease: str
    move_in: str
    metro: list[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["price"] = self.price.to_dict()
        return d


def _price_conflict(title: str, body: str) -> str:
    """标题价与正文价相差超过 5%：常见的“低价引流”写法。"""
    t, b = extract_price(title), extract_price(body)
    if not (t.price and b.price) or t.low or b.low:
        return ""
    if abs(t.price - b.price) / max(t.price, b.price) > 0.05:
        return f"标题 {t.price} / 正文 {b.price}"
    return ""


def extract_facts(title: str, body: str) -> ListingFacts:
    body = clean_post_text(body)
    full = f"{title}\n{body}"
    bedrooms = extract_bedrooms(full)
    return ListingFacts(
        price=extract_price(full),
        price_conflict=_price_conflict(title, body),
        bedrooms=bedrooms,
        layout=bedrooms_label(bedrooms),
        area_sqm=extract_area(full),
        payment=extract_payment(full),
        lease=extract_lease(full),
        move_in=extract_move_in(full),
        metro=extract_metro(full),
    )
