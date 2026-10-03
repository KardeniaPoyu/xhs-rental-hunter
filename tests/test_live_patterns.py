"""回归测试：来自 2026-10 真实小红书数据中观察到的写法（文本已改写，不含真实用户内容）。"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from rental.extract import clean_post_text, extract_facts, extract_lease, extract_price
from rental.pipeline import find_cross_account_duplicates
from rental.rules import (
    Requirements,
    analyze_author_history,
    assess,
    ip_matches_city,
    prefilter_card,
)

# ─── 求租帖的各种写法（真实数据中 6/20 条是求租帖） ─────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "北京朝阳｜预算4500-5000左右求业主直租或转",
        "急求房",
        "朝阳区求房东个人直租转租",
        "求租｜三元桥整租一居｜可养宠物",
    ],
)
def test_seeking_variants_excluded(title: str) -> None:
    assert "求租" in prefilter_card(title, "someone")


@pytest.mark.parametrize(
    "title", ["求个靠谱下家｜一居转租", "只求一个好租客 一居转租", "急求接手 一居转租"]
)
def test_seeking_successor_not_excluded(title: str) -> None:
    assert prefilter_card(title, "someone") == ""


@pytest.mark.parametrize("nick", ["北京小景直租", "北漂租房小帮手", "阿猫租房CQ"])
def test_agent_nicknames(nick: str) -> None:
    assert "中介" in prefilter_card("朝阳一居转租", nick)


# ─── 防屏蔽的价格写法 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("zu金 5️⃣叁伍0️⃣", 5350),  # 数字 emoji + 大写数字
        ("房租实惠，不到55张，尽快入住还能谈", 5500),  # 张 = 百元
        ("【租金】押一付三5500。", 5500),  # “押一付三”不是押金
        ("押一付三 4500元/月", 4500),
        ("拍了5张图 月租4000", 4000),  # “5张图”不是价格
    ],
)
def test_obfuscated_prices(text: str, price: int) -> None:
    assert extract_price(text).price == price


def test_fuzzy_price_range() -> None:
    p = extract_price("法式复古风 5xxx月租价格 押一付三")
    assert (p.low, p.high, p.confidence) == (5000, 5999, "low")


def test_title_body_price_conflict() -> None:
    f = extract_facts("北京某地转租，一居室2500", "南向一居室\n2300，房东直租")
    assert f.price_conflict == "标题 2500 / 正文 2300"
    assert extract_facts("一居转租4500", "租金4500元/月").price_conflict == ""


@pytest.mark.parametrize(
    ("text", "lease"),
    [
        ("租期到27.4.30，到期可与房东续约", "租期到27.4.30"),
        ("租期到明年6月", "租期到明年6月"),
        ("合同到2027年3月底", "合同到2027年3月底"),
    ],
)
def test_lease_formats(text: str, lease: str) -> None:
    assert extract_lease(text) == lease


def test_clean_post_text() -> None:
    s = clean_post_text("采光好 #民用水民用电[话题]# #一居室就好[话题]##南北[话题]# 好[吧唧R]")
    assert "[话题]" not in s and "R]" not in s
    assert "#民用水民用电" in s


# ─── 第三方口吻 / 中介签约 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "客户工作变动诚意转租，真实实拍，有意后台滴滴看房",
        "房东阿姨人很好，稳定国外，需要和正规中介签约",
        "地铁站旁，小姐姐刚刚搬走，南向一居室",
    ],
)
def test_third_party_voice(body: str) -> None:
    a = assess("朝阳一居转租", body)
    assert any(h.id == "third_party" for h in a.risks)


def test_tenant_history_not_renovation_flag() -> None:
    a = assess("开间转租", "我租房时是新房首次出租，到现在只住了四个月，屋内整体很新")
    assert not any(h.id == "renovation" for h in a.risks)
    assert any(h.id == "lived_in" for h in a.positives)


def test_work_change_is_reason() -> None:
    a = assess("花家地一居转租", "租期到27.4.30，到期可与房东续约，工作变动所以转")
    ids = {h.id for h in a.positives}
    assert {"reason", "lease_term", "landlord_sign"} <= ids


# ─── 主页：同一套房重复发布 ≠ 多套房 ────────────────────────────────────────


def test_reposts_of_same_flat_not_multi_listing() -> None:
    h = analyze_author_history(
        "x",
        "",
        [
            "望京融新一居 房东直租 无隐形费用",
            "望京/阜通一居室房东直租",
            "望京超绝阳光一居室个人转租",
            "房东直租 望京一居",
        ],
        "望京超绝阳光一居室个人转租",
        "望京一居转租 近14号线",
    )
    assert h.distinct_listings == 0
    assert h.repost_notes == 3
    a = assess("望京超绝阳光一居室个人转租", "工作变动转租，和房东直签合同", history=h)
    assert not any(r.id == "multi_listing" for r in a.risks)


def test_identical_reposts_and_hobby_posts() -> None:
    titles = ["三楼朝南大开间转租"] * 4 + ["三楼能看阳光的房间转租", "拼豆", "拼豆日常"]
    h = analyze_author_history("x", "", titles, "三楼朝南大开间转租", "整租大开间")
    assert h.distinct_listings == 0
    assert h.repost_notes >= 4


def test_different_layout_or_price_is_distinct() -> None:
    h = analyze_author_history(
        "x",
        "",
        [
            "全北京朝阳，紧邻地铁全南一居5.5k",
            "全北京朝阳，房东直租南北两居6.8k",
            "全北京朝阳，地铁全南一居",
        ],
        "全北京朝阳，地铁全南一居",
        "押一付三5500",
    )
    assert h.distinct_listings == 1  # 两居 6.8k 是另一套
    assert h.repost_notes == 1


def test_different_areas_are_distinct() -> None:
    h = analyze_author_history(
        "x", "", ["望京一居出租", "国贸两居转租", "双井开间直租"], "朝阳精装一居转租", ""
    )
    assert h.distinct_listings == 3


# ─── 跨账号雷同文案 ──────────────────────────────────────────────────────────

_TEMPLATE = (
    "【位置】某区某某家园。【出租日期】随时入住，随时看房，紧邻地铁站步行10分钟内。"
    "【卧室情况】采光很好，冰箱空调洗衣机都有。【租金】押一付三{}。"
)


def test_cross_account_duplicates() -> None:
    posts = [
        {
            "id": "a",
            "desc": _TEMPLATE.format(5300) + " #房屋出租",
            "user": {"userId": "u1", "nickname": "甲"},
        },
        {
            "id": "b",
            "desc": _TEMPLATE.format(5500) + " #转租 #朝南",
            "user": {"userId": "u2", "nickname": "乙"},
        },
        {"id": "c", "desc": _TEMPLATE.format(5300), "user": {"userId": "u1", "nickname": "甲"}},
        {
            "id": "d",
            "desc": "完全不同的一段正文，讲的是另一套两居室的情况，合同到明年五月。",
            "user": {"userId": "u3"},
        },
    ]
    d = find_cross_account_duplicates(posts)
    assert d["a"] == ["乙"]
    assert set(d["b"]) == {"甲"}
    assert "d" not in d
    a = assess("某家园一居", posts[0]["desc"], duplicates=d["a"])
    assert any(h.id == "cross_account_dup" for h in a.risks)


# ─── IP 属地 ─────────────────────────────────────────────────────────────────


def test_ip_matches_city() -> None:
    assert ip_matches_city("北京", "北京")
    assert not ip_matches_city("北京", "山东")
    assert ip_matches_city("杭州", "浙江")
    assert ip_matches_city("北京", "")
    a = assess("一居转租", "客厅很大", ip="山东", req=Requirements(city="北京"))
    assert any(h.id == "ip_mismatch" for h in a.risks)
    a = assess("一居转租", "客厅很大", ip="山东")
    assert not any(h.id == "ip_mismatch" for h in a.risks)


def test_payment_not_glued_to_price() -> None:
    from rental.extract import extract_payment

    assert extract_payment("【租金】押一付三5300。") == "押一付三"
    assert extract_payment("押一付12") == "押一付12"


def test_public_housing_flag() -> None:
    a = assess("朝阳燕保某湾转租4200 一室一厅", "采光很好 转租没有中介费")
    assert any(h.id == "public_housing" for h in a.risks)
