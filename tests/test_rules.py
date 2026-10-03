from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from rental.rules import (
    Requirements,
    analyze_author_history,
    assess,
    detect_exclusion,
    prefilter_card,
)

DAY = 86_400_000
NOW = 1_790_000_000_000

GENUINE = (
    "因为工作调动要离开北京了，转租住了两年的两居室。4500元/月，押一付三，和房东重新签合同。"
    "合同到27年3月。沙发、书柜都送给下一任。缺点是老小区没有电梯。下班后或周末看房，提前约。"
)
AGENT = (
    "✨✨全新装修首次出租✨✨🥰🔥🏠 0中介费 房东直租 押一付一 拎包入住 4200/月 🥰🔥🏠✨✨🥰🔥🏠✨ "
    "房源多，各区域都有，需要的姐妹私，加v看更多"
)


@pytest.mark.parametrize(
    ("title", "reason_part"),
    [
        ("求北京个人转租", "求租"),
        ("求租朝阳一居", "求租"),
        ("望京一居已租出", "已租"),
        ("朝阳合租单间转租", "合租"),
        ("次卧转租", "合租"),
        ("双井 loft 转租", "商业公寓"),
        ("自如一居转租", "商业公寓"),
    ],
)
def test_exclusions(title: str, reason_part: str) -> None:
    assert reason_part in detect_exclusion(title, "")


@pytest.mark.parametrize(
    "title",
    [
        "主卧朝南的两居整租",  # 整租 + 主卧描述，不应误杀
        "东坝两居室转租",
        "整租不合租 一居室",
        "XX公寓小区一居室转租",
    ],
)
def test_no_false_exclusion(title: str) -> None:
    assert detect_exclusion(title, "") == ""


def test_prefilter_agent_nickname() -> None:
    assert "中介" in prefilter_card("好房转租", "安家好房小王")
    assert prefilter_card("八里庄两居室转租", "张三") == ""
    # 旧版的“姐妹们”“推荐”等过宽排除词已移除
    assert prefilter_card("姐妹们 东坝一居转租", "小红") == ""


def test_genuine_post_is_trusted() -> None:
    a = assess("东坝两居室转租", GENUINE, author="李华", publish_ts_ms=NOW - 2 * DAY, now_ts_ms=NOW)
    assert a.tier == "trusted"
    ids = {h.id for h in a.positives}
    assert {"reason", "lease_term", "landlord_sign", "belongings", "honest_flaws"} <= ids
    assert a.facts.price.price == 4500
    assert a.age_days == 2.0
    assert all(h.evidence for h in a.positives)


def test_agent_post_is_suspect() -> None:
    hist = analyze_author_history(
        "化妆师小敏", "", ["望京一居出租", "国贸两居转租", "双井开间直租", "日常妆容"]
    )
    a = assess("朝阳个人转租|0中介费|拎包入住|精装", AGENT, author="化妆师小敏", history=hist)
    assert a.tier == "suspect"
    ids = {h.id for h in a.risks}
    assert {
        "agent_talk",
        "contact_redirect",
        "renovation",
        "template_title",
        "multi_listing",
        "persona",
    } <= ids
    # 人设 + 多套房组合时扣分更重
    ml = next(h for h in a.risks if h.id == "multi_listing")
    assert ml.weight == -40


def test_agent_fee_is_strong_signal() -> None:
    a = assess("石佛营一居室出租", "一居室出租 4300元 中介费半个月 随时看房 合同到明年5月")
    assert any(h.id == "agent_fee" for h in a.risks)
    assert a.tier != "trusted"


def test_zero_agent_fee_is_not_agent_fee() -> None:
    a = assess("一居转租", "无中介费，0中介费，" + GENUINE)
    assert not any(h.id == "agent_fee" for h in a.risks)


def test_persona_alone_is_light() -> None:
    a = assess("东坝两居室转租", GENUINE, author="插画师阿紫")
    assert a.tier == "trusted"
    assert any(h.id == "persona" and h.weight > -10 for h in a.risks)


def test_author_history_counts_only_listings() -> None:
    h = analyze_author_history(
        "x",
        "",
        ["今天做了蛋糕", "求租朝阳一居", "东坝两居转租", "望京一居出租"],
        current_title="东坝两居转租",
    )
    assert h.listing_notes == 1  # 当前帖与求租帖不计入
    assert h.total_notes == 4


def test_normal_profile_bonus() -> None:
    h = analyze_author_history("x", "爱做饭", ["做饭日记", "周末徒步", "猫猫日常"])
    a = assess("东坝两居室转租", GENUINE, history=h)
    assert any(p.id == "normal_profile" for p in a.positives)


def test_author_replied_rented_excludes() -> None:
    a = assess(
        "姚家园两居整租",
        "买房了要搬走，7800元/月",
        author_id="u6",
        comments=[{"content": "已租出，谢谢大家", "user": {"userId": "u6"}}],
    )
    assert a.tier == "excluded"


def test_comment_doubt() -> None:
    a = assess(
        "一居转租", GENUINE, comments=[{"content": "又是你，到处发", "user": {"userId": "z"}}]
    )
    assert any(h.id == "comment_doubt" for h in a.risks)


def test_requirements_mismatch() -> None:
    req = Requirements(
        budget_min=3500, budget_max=4000, bedrooms=[1], max_age_days=7, must_have=["望京"]
    )
    a = assess("东坝两居室转租", GENUINE, publish_ts_ms=NOW - 20 * DAY, now_ts_ms=NOW, req=req)
    joined = " ".join(a.mismatches)
    assert "超出预算" in joined
    assert "户型" in joined
    assert "天前" in joined
    assert "目标区域" in joined


def test_suspiciously_cheap() -> None:
    req = Requirements(budget_min=4000, budget_max=5000)
    a = assess("一居转租", "月租1500 " + GENUINE, req=req)
    assert any("低价" in m for m in a.mismatches)


def test_open_questions_ask_missing_info() -> None:
    a = assess("一居转租", "转租一居室，有意私聊")
    qs = " ".join(a.open_questions)
    assert "月租" in qs
    assert "房东" in qs


def test_score_is_clamped() -> None:
    a = assess("朝阳个人转租|0中介费|拎包入住", AGENT + " 中介费一个月")
    assert 0 <= a.trust_score <= 100
