"""防中介 / 防二房东 / 防串串房研判规则。

所有规则都是数据（``Signal``），打分结果带“命中片段”作为证据，便于 Claude 二次复核、
也便于用户理解“为什么这么判”。

信任分 0~100，基准 50：
- 真实个人租客特征加分（转租原因、合同剩余期限、直签房东、自购家具转让、坦白缺点……）
- 中介 / 二房东 / 营销号特征扣分（收中介费、“房源多”、主页多套房、模板标题……）
- 硬排除（求租、已租出、合租单间、商业公寓/品牌公寓）单独给出 ``exclusion``，不参与排序。

规则本身只做“初筛与提示”，最终结论由 Claude 读原文后给出（见 SKILL.md）。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .extract import ListingFacts, extract_facts, normalize, snippet

# ─── 规则定义 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Signal:
    id: str
    label: str
    weight: int  # 正数=真实个人特征，负数=风险特征
    pattern: str
    rule: str = ""  # 对应 SKILL 中的鉴别法则编号，便于报告引用
    title_only: bool = False


POSITIVE_SIGNALS: tuple[Signal, ...] = (
    Signal(
        "reason",
        "转租原因具体（工作/生活变动）",
        12,
        r"工作调动|调岗|换工作|跳槽|离职|外派|去外地|回老家|回家发展|离开[一-龥]{1,3}(?:了|发展)?"
        r"|离[京沪深杭蓉]|出国|留学|读研|考研|结婚|领证|和对象|跟对象|男朋友|女朋友|同居|买了?房|换大房"
        r"|换小房|公司搬|通勤太远|上班太远|单位(?:分|安排)",
        "R3",
    ),
    Signal(
        "lease_term",
        "写明合同剩余期限",
        8,
        r"(?:合同|租期|租约)\s*(?:到|至|截止)|还剩\s*\S{1,3}\s*个?月|剩余\s*\S{1,3}\s*个?月",
        "R3",
    ),
    Signal(
        "landlord_sign",
        "可直接与房东签约",
        8,
        r"房东直签|(?:跟|和|与)房东(?:重新|直接)?签|房东本人签|直接(?:跟|和|与)房东|房东(?:人很?好|很好说话|好沟通|阿姨|叔叔|大爷|大姐)",
        "R3",
    ),
    Signal(
        "belongings",
        "自购家具/电器转让或赠送",
        8,
        r"(?:家具|电器|沙发|书柜|书桌|床垫|冰箱|洗衣机|空调|烘干机|洗碗机|投影|绿植|猫爬架|衣柜)"
        r"[^。\n]{0,10}(?:送|赠|转让|留给|低价|免费|折价|带不走)"
        r"|(?:送|赠送|留下|白送)[^。\n]{0,8}(?:家具|电器|沙发|书柜|床垫|冰箱|洗衣机|绿植|衣架)",
        "R3",
    ),
    Signal(
        "schedule",
        "上班族看房时间（下班后/周末）",
        5,
        r"下班后|晚上\s*\d{1,2}\s*点|周末(?:可以)?看房|工作日晚|提前(?:约|预约|联系|说)",
        "R3",
    ),
    Signal(
        "lived_in",
        "有真实居住细节",
        8,
        r"我住了|住了\s*\S{1,3}\s*年|本人(?:在)?住|自住|入住以来|住得很?舒服|邻居|楼下(?:就有|有个|就是)|"
        r"物业(?:很|挺|还)|采光(?:是真|真的)|半夜|养了?猫|养了?狗",
        "R3",
    ),
    Signal(
        "honest_flaws",
        "主动说明缺点（营销号极少这么写）",
        6,
        r"缺点|不足之处|美中不足|唯一不好|不太好的是|介意(?:的|勿)|老小区|没有电梯|无电梯|隔音一般|有点吵|西晒",
        "R3",
    ),
)

RISK_SIGNALS: tuple[Signal, ...] = (
    Signal(
        "agent_fee",
        "收取中介费/服务费",
        -35,
        r"中介费\s*(?:半个?月|一个?月|\d+\s*%|按|收|需)|服务费\s*(?:\d|半|一|收)|(?:收|需付?)\s*中介费",
        "R2",
    ),
    Signal(
        "agent_talk",
        "中介话术（房源多/帮找房/看主页）",
        -25,
        r"房源(?:很?多|充足|还有)|更多房源|多套|其他房源|各区域|全城|帮(?:你|您)?找房|找房(?:找我|私|戳)"
        r"|带看|看主页|主页(?:还有|更多)|整租合租都有|可以帮(?:你|您)?找|需要的(?:姐妹|宝子)?(?:私|滴|戳)",
        "R2",
    ),
    Signal(
        "contact_redirect",
        "引导加微信/私下联系",
        -8,
        r"加\s*[vV微]|[vV]\s*[xX信]|[wW]\s*[xX]|微信号?\s*[:：]|薇\s*信|➕\s*[vV]|看简介|看置顶",
        "R2",
    ),
    Signal(
        "renovation",
        "全新装修/网红风格（警惕串串房甲醛）",
        -12,
        r"全新装修|新装修|刚装修(?:好|完)?|首次出租|首租|全新家具|网红装修|ins风|奶油风|原木风|"
        r"精装修?\s*(?:未住|没人住|无人住)",
        "R1",
    ),
    Signal(
        "template_title",
        "三竖杠/【】模板标题",
        -8,
        r"(?:[^|]*\|){2,}|【[^】]+】[^【]*【",
        "R3",
        title_only=True,
    ),
)

# 职业人设：只看昵称，单独出现扣分很轻，与“主页多套房”同时出现时由组合规则加重
PERSONA_PATTERN = re.compile(
    r"化妆|美甲|美睫|宝妈|插画|手作|手工|摄影|理发|发型|漂染|造型|烘焙|花艺|调香|瑜伽|普拉提|设计师|作家"
)
AGENT_NAME_PATTERN = re.compile(
    r"房产|地产|找房|租房|房源|经纪|管家|置业|公寓|房屋|好房|安家|租赁|\d{3,}房"
)

LABEL_SPAM_TERMS = (
    "0中介费",
    "零中介",
    "无中介",
    "免中介",
    "房东直租",
    "个人直租",
    "个人转租",
    "中介勿扰",
    "非中介",
    "拎包入住",
    "押一付一",
)

# ─── 硬排除 ──────────────────────────────────────────────────────────────────

_SEEKING = re.compile(
    r"^\s*求|求租|求一个|求个|想租|蹲一个|蹲个|找室友|找房子|求推荐|有没有.{0,8}(?:出租|转租)"
)
_RENTED = re.compile(r"已租|已出|已转|已经?租出|租出去了|已签约|已被预定|暂停出租")
_SHARED = re.compile(r"合租|单间|隔断|床位|室友|厅卧|一间房")
_ROOM_ONLY = re.compile(r"主卧|次卧")
_WHOLE = re.compile(r"整租|整套")
_NOT_SHARED = re.compile(r"(?:不是|非|不|拒绝|无需|不要)\s*合租|整租不合租")
_COMMERCIAL = re.compile(
    r"商水商电|商住|loft|产业园|文创园|科技园|创意园|酒店式|长租公寓|服务式公寓|自如|蛋壳|相寓|泊寓|魔方公寓|冠寓|"
    r"城家|乐乎|朗诗寓|红璞",
    re.IGNORECASE,
)
_APARTMENT_WORD = re.compile(r"公寓(?!小区|楼|社区)")


def detect_exclusion(title: str, body: str) -> str:
    """返回硬排除原因；为空表示不排除。title 判断更严格（标题即帖子主旨）。"""
    t, full = normalize(title), normalize(f"{title}\n{body}")
    if _SEEKING.search(t):
        return "求租/找房帖"
    if _RENTED.search(t):
        return "标题显示已租出"
    shared = _SHARED.search(t) or (_ROOM_ONLY.search(t) and not _WHOLE.search(t))
    if shared and not _NOT_SHARED.search(t):
        return "合租/单间（非整租）"
    if _COMMERCIAL.search(full):
        return "商业公寓/品牌长租（非民用住宅，水电成本高）"
    return ""


def prefilter_card(title: str, author: str) -> str:
    """只看搜索卡片（标题+昵称）的快速初筛，返回排除原因；空串表示保留。"""
    reason = detect_exclusion(title, "")
    if reason:
        return reason
    if AGENT_NAME_PATTERN.search(normalize(author)):
        return f"昵称像中介/机构（{author}）"
    return ""


# ─── 作者主页（法则 2：多套房历史） ──────────────────────────────────────────

_LISTING_TITLE = re.compile(
    r"出租|转租|整租|租房|房源|直租|[一两二三1-3]\s*(?:居|室)|开间|押一付|找室友|看房"
)


@dataclass
class AuthorHistory:
    checked: bool = False
    total_notes: int = 0
    listing_notes: int = 0
    listing_titles: list[str] = field(default_factory=list)
    profile_text: str = ""  # 昵称 + 简介
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_author_history(
    nickname: str, desc: str, note_titles: list[str], current_title: str = ""
) -> AuthorHistory:
    """统计作者主页中“像房源帖”的笔记数量（不含当前帖）。"""
    cur = normalize(current_title).strip()
    listing = []
    for t in note_titles:
        nt = normalize(t).strip()
        if not nt or nt == cur:
            continue
        if _LISTING_TITLE.search(nt) and not _SEEKING.search(nt):
            listing.append(t)
    return AuthorHistory(
        checked=True,
        total_notes=len(note_titles),
        listing_notes=len(listing),
        listing_titles=listing[:8],
        profile_text=f"{nickname} {desc}".strip(),
    )


# ─── 打分 ────────────────────────────────────────────────────────────────────


@dataclass
class Hit:
    id: str
    label: str
    weight: int
    rule: str
    evidence: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Requirements:
    budget_min: int | None = None
    budget_max: int | None = None
    bedrooms: list[int] | None = None  # 允许的卧室数，0=开间
    max_age_days: int | None = None
    must_have: list[str] = field(default_factory=list)  # 任一命中即可，如地铁站/小区名

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Requirements:
        d = d or {}
        return cls(
            budget_min=d.get("budget_min"),
            budget_max=d.get("budget_max"),
            bedrooms=d.get("bedrooms"),
            max_age_days=d.get("max_age_days"),
            must_have=list(d.get("must_have") or []),
        )


@dataclass
class Assessment:
    trust_score: int
    verdict: str
    tier: str  # trusted / verify / suspect / excluded
    exclusion: str
    positives: list[Hit]
    risks: list[Hit]
    mismatches: list[str]  # 不满足用户需求的条目（预算/户型/时效/关键词）
    facts: ListingFacts
    age_days: float | None
    open_questions: list[str]  # 建议看房前追问的问题

    def to_dict(self) -> dict:
        return {
            "trust_score": self.trust_score,
            "verdict": self.verdict,
            "tier": self.tier,
            "exclusion": self.exclusion,
            "positives": [h.to_dict() for h in self.positives],
            "risks": [h.to_dict() for h in self.risks],
            "mismatches": self.mismatches,
            "facts": self.facts.to_dict(),
            "age_days": self.age_days,
            "open_questions": self.open_questions,
        }


_EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff⭐⭕㊗㊙]")
_DOUBT_COMMENT = re.compile(r"中介|二房东|串串|骗|假的|同一个人|又是你|到处发|别信|收费")
_RENTED_REPLY = re.compile(r"已租|已出|租出去了|已经?定了|已签")


def _match(sig: Signal, title: str, full: str) -> Hit | None:
    target = title if sig.title_only else full
    m = re.search(sig.pattern, target, re.IGNORECASE)
    if not m:
        return None
    return Hit(sig.id, sig.label, sig.weight, sig.rule, snippet(target, m.start(), m.end(), pad=6))


def assess(
    title: str,
    body: str,
    *,
    author: str = "",
    tags: list[str] | None = None,
    comments: list[dict] | None = None,
    author_id: str = "",
    history: AuthorHistory | None = None,
    publish_ts_ms: int | None = None,
    now_ts_ms: int | None = None,
    req: Requirements | None = None,
) -> Assessment:
    """对单条房源做完整研判。"""
    req = req or Requirements()
    t = normalize(title)
    full = normalize(f"{title}\n{body}\n{' '.join(tags or [])}")
    facts = extract_facts(title, body)

    positives = [h for s in POSITIVE_SIGNALS if (h := _match(s, t, full))]
    risks = [h for s in RISK_SIGNALS if (h := _match(s, t, full))]

    # 营销标签堆叠（法则 2/3）：≥3 个且没有任何真实细节
    spam = [w for w in LABEL_SPAM_TERMS if w in full]
    if len(spam) >= 3 and not positives:
        risks.append(Hit("label_spam", "营销标签堆叠且无生活细节", -10, "R2", "、".join(spam[:5])))

    emoji_count = len(_EMOJI.findall(full))
    if emoji_count >= 12:
        risks.append(
            Hit("emoji_flood", "emoji 过量（批量营销文案特征）", -5, "R3", f"{emoji_count} 个")
        )

    if len(normalize(body).strip()) < 40:
        risks.append(
            Hit("thin_body", "正文过短，缺少可核验信息", -6, "R3", f"{len(body.strip())} 字")
        )

    # 法则 1：职业人设（看昵称）
    persona = PERSONA_PATTERN.search(normalize(author))
    if AGENT_NAME_PATTERN.search(normalize(author)):
        risks.append(Hit("agent_name", "昵称含房产/中介/机构字样", -30, "R2", author))
    elif persona:
        risks.append(Hit("persona", "职业人设昵称（需结合主页核验）", -4, "R1", author))

    # 法则 2：主页多套房历史
    if history and history.checked:
        if AGENT_NAME_PATTERN.search(normalize(history.profile_text)):
            risks.append(
                Hit(
                    "agent_profile", "主页简介含房产/找房字样", -25, "R2", history.profile_text[:30]
                )
            )
        n = history.listing_notes
        if n >= 3:
            w = -30 if not persona else -40
            risks.append(
                Hit(
                    "multi_listing",
                    f"主页另有 {n} 篇房源帖",
                    w,
                    "R2",
                    "；".join(history.listing_titles[:3]),
                )
            )
        elif n == 2:
            risks.append(
                Hit(
                    "multi_listing",
                    "主页另有 2 篇房源帖",
                    -12,
                    "R2",
                    "；".join(history.listing_titles[:2]),
                )
            )
        elif n == 0 and history.total_notes >= 3:
            positives.append(
                Hit("normal_profile", "主页为正常生活内容", 6, "R2", f"共 {history.total_notes} 篇")
            )

    # 评论区线索
    for c in comments or []:
        content = normalize(str(c.get("content", "")))
        uid = (c.get("user") or {}).get("userId", "")
        if author_id and uid == author_id and _RENTED_REPLY.search(content):
            risks.append(Hit("rented_reply", "作者在评论区回复已租出", -50, "", content[:30]))
            break
    doubt = [
        c for c in comments or [] if _DOUBT_COMMENT.search(normalize(str(c.get("content", ""))))
    ]
    if doubt:
        risks.append(
            Hit(
                "comment_doubt",
                "评论区有人质疑是中介/二房东",
                -10,
                "R2",
                str(doubt[0].get("content", ""))[:30],
            )
        )

    score = 50 + sum(h.weight for h in positives) + sum(h.weight for h in risks)
    score = max(0, min(100, score))

    exclusion = detect_exclusion(title, body)
    if any(h.id == "rented_reply" for h in risks):
        exclusion = exclusion or "作者已回复租出"

    # 时效
    age_days: float | None = None
    if publish_ts_ms and now_ts_ms:
        age_days = round(max(0.0, (now_ts_ms - publish_ts_ms) / 86_400_000), 1)

    mismatches = _check_requirements(req, facts, full, age_days)

    if exclusion:
        tier, verdict = "excluded", f"排除：{exclusion}"
    elif score >= 70:
        tier, verdict = "trusted", "🌟 高可信个人转租"
    elif score >= 45:
        tier, verdict = "verify", "🔍 待核实"
    else:
        tier, verdict = "suspect", "⚠️ 疑似中介/二房东/串串房"

    return Assessment(
        trust_score=score,
        verdict=verdict,
        tier=tier,
        exclusion=exclusion,
        positives=positives,
        risks=risks,
        mismatches=mismatches,
        facts=facts,
        age_days=age_days,
        open_questions=_open_questions(facts, positives, risks),
    )


def _check_requirements(
    req: Requirements, facts: ListingFacts, full: str, age_days: float | None
) -> list[str]:
    out: list[str] = []
    p = facts.price
    lo = p.low or p.price
    hi = p.high or p.price
    if p.price is not None:
        if req.budget_max and lo and lo > req.budget_max:
            out.append(f"租金 {p.price} 超出预算上限 {req.budget_max}")
        if req.budget_min and hi and hi < req.budget_min * 0.75:
            out.append(f"租金 {p.price} 远低于预算下限，警惕低价引流")
    if (
        req.bedrooms is not None
        and facts.bedrooms is not None
        and facts.bedrooms not in req.bedrooms
    ):
        out.append(f"户型 {facts.layout} 不在需求范围")
    if req.max_age_days is not None and age_days is not None and age_days > req.max_age_days:
        out.append(f"发布于 {age_days:.0f} 天前，可能已租出")
    if req.must_have and not any(normalize(k) in full for k in req.must_have):
        out.append("未提到目标区域/地铁站/小区")
    return out


def _open_questions(facts: ListingFacts, positives: list[Hit], risks: list[Hit]) -> list[str]:
    q: list[str] = []
    ids = {h.id for h in positives} | {h.id for h in risks}
    if facts.price.price is None:
        q.append("月租多少？押几付几？")
    elif not facts.payment:
        q.append("押几付几？能否月付/季付？")
    if "lease_term" not in ids:
        q.append("原合同到哪天？到期后能否和房东续签？")
    if "landlord_sign" not in ids:
        q.append("是和房东重新签约还是转租合同？能否见房东本人？")
    if "renovation" in ids:
        q.append("装修/家具是什么时候做的？有没有做过甲醛检测？")
    if "agent_fee" in ids or "agent_talk" in ids:
        q.append("是否收取中介费或服务费？具体金额？")
    if not facts.move_in:
        q.append("最早什么时候可以入住？")
    q.append("水电燃气是民用价吗？物业/取暖/网费怎么算？")
    return q[:5]
