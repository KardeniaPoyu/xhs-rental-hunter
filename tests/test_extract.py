from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from rental.extract import (
    extract_area,
    extract_bedrooms,
    extract_facts,
    extract_lease,
    extract_move_in,
    extract_payment,
    extract_price,
    normalize,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("两居室 4500元/月 押一付三", 4500),
        ("月租：3800 电费1.5 物业费200", 3800),
        ("房租3k，押一付一", 3000),
        ("一居室转租 4.5k 年付优先 2025年装修", 4500),
        ("开间 15㎡ 2,800元 还剩8个月", 2800),
        ("租金4500 押金4500 中介费2250", 4500),
        ("押金3000，租金4200元", 4200),  # 押金在前也不能被当成租金
        ("步行5km 房租6千", 6000),
        ("朝阳 个人转租 5200", 5200),  # 只在标题出现的裸数字（低置信）
        ("ＸＸ小区　４５００元／月", 4500),  # 全角
    ],
)
def test_extract_price(text: str, expected: int) -> None:
    assert extract_price(text).price == expected


@pytest.mark.parametrize(
    "text",
    [
        "10号线 3号楼 2026年 无价格",
        "建于2008年的老小区，1200米到地铁",
        "",
    ],
)
def test_extract_price_none(text: str) -> None:
    assert extract_price(text).price is None


def test_price_range() -> None:
    p = extract_price("八里庄 2室1厅 约4200-4800/月 面议")
    assert (p.low, p.high, p.price) == (4200, 4800, 4200)


def test_price_confidence_levels() -> None:
    assert extract_price("租金 4000").confidence == "high"
    assert extract_price("朝阳一居 5200").confidence == "low"


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("两居室整租", 2),
        ("2室1厅", 2),
        ("一居室", 1),
        ("三居", 3),
        ("开间公寓", 0),
        ("整租 1房", 1),
        ("房东直租", None),
        ("小区环境好", None),
    ],
)
def test_bedrooms(text: str, n: int | None) -> None:
    assert extract_bedrooms(text) == n


def test_area_payment_lease_move_in() -> None:
    t = "62平 押一付三 合同到27年3月 10月5日后可入住"
    assert extract_area(t) == 62
    assert extract_payment(t) == "押一付三"
    assert extract_lease(t) == "合同到27年3月"
    assert extract_move_in(t) == "10月5日后可入住"
    assert extract_payment("年付优先") == "年付"
    assert extract_lease("还剩8个月") == "还剩8个月"
    assert extract_move_in("随时看房") == ""


def test_area_ignores_platform() -> None:
    assert extract_area("小红书平台 1200米") is None


def test_normalize_fullwidth_and_bars() -> None:
    assert normalize("４５００｜丨") == "4500||"


def test_extract_facts_layout_label() -> None:
    f = extract_facts("东坝两居室转租", "4500元/月")
    assert f.layout == "两居"
    assert f.to_dict()["price"]["price"] == 4500
