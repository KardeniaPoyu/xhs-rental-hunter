"""流水线离线测试：用 monkeypatch 替换浏览器操作，覆盖 检索→初筛→详情→主页→研判→摘要→海报→评论。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from PIL import Image
from rental.pipeline import RentalPipeline, VerificationRequiredError
from rental.poster import FontNotFoundError, find_fonts, wrap

import xhs.comment
import xhs.feed_detail
import xhs.search
import xhs.user_profile
from xhs.errors import PageNotAccessibleError
from xhs.types import (
    CommentList,
    Feed,
    FeedDetail,
    FeedDetailResponse,
    UserBasicInfo,
    UserProfileResponse,
)

FIX = Path(__file__).parent / "fixtures"
NOW = 1_790_000_000_000
POSTS = {p["id"]: p for p in json.loads((FIX / "detailed_posts.json").read_text(encoding="utf-8"))}


def _has_font() -> bool:
    try:
        find_fonts()
        return True
    except FontNotFoundError:
        return False


def _card(p: dict) -> Feed:
    return Feed.from_dict(
        {
            "id": p["id"],
            "xsecToken": "tok_" + p["id"],
            "modelType": "note",
            "noteCard": {"displayTitle": p["title"], "user": p["user"], "interactInfo": {}},
        }
    )


def _detail(p: dict) -> FeedDetailResponse:
    note = FeedDetail.from_dict(
        {
            "noteId": p["id"],
            "title": p["title"],
            "desc": p["desc"],
            "time": p.get("time", 0),
            "ipLocation": p.get("ip", ""),
            "user": p["user"],
            "interactInfo": {"commentCount": p.get("comment_count", "")},
            "imageList": [{"urlDefault": u} for u in p.get("images", [])],
        }
    )
    raw_comments = [{"content": c["content"], "userInfo": c["user"]} for c in p.get("comments", [])]
    return FeedDetailResponse(note=note, comments=CommentList.from_dict({"list": raw_comments}))


@pytest.fixture
def fake_xhs(monkeypatch):
    calls: dict[str, list] = {"detail": [], "profile": [], "comment": []}

    def search_feeds(page, kw, fo=None):
        feeds = [_card(p) for p in POSTS.values()]
        feeds.append(
            _card({"id": "s1", "title": "求租朝阳一居", "user": {"nickname": "z", "userId": "z"}})
        )
        feeds.append(
            _card(
                {"id": "s2", "title": "好房推荐", "user": {"nickname": "安家好房", "userId": "y"}}
            )
        )
        return feeds

    def get_feed_detail(page, fid, tok, keyword=""):
        calls["detail"].append(fid)
        return _detail(POSTS[fid])

    def get_user_profile(page, uid, tok):
        calls["profile"].append(uid)
        titles = {"u3": ["望京一居出租", "国贸两居转租", "双井开间直租", "日常妆容"]}.get(
            uid, ["做饭", "徒步", "猫"]
        )
        feeds = [
            Feed.from_dict({"id": f"n{i}", "noteCard": {"displayTitle": t}})
            for i, t in enumerate(titles)
        ]
        return UserProfileResponse(user_basic_info=UserBasicInfo(nickname=uid), feeds=feeds)

    def post_comment(page, fid, tok, msg):
        calls["comment"].append((fid, msg))

    monkeypatch.setattr(xhs.search, "search_feeds", search_feeds)
    monkeypatch.setattr(xhs.feed_detail, "get_feed_detail", get_feed_detail)
    monkeypatch.setattr(xhs.user_profile, "get_user_profile", get_user_profile)
    monkeypatch.setattr(xhs.comment, "post_comment", post_comment)
    monkeypatch.setattr("rental.pipeline.random.uniform", lambda a, b: 0)
    return calls


def _pipeline(tmp_path: Path) -> RentalPipeline:
    return RentalPipeline(work_dir=str(tmp_path), delay=(0, 0), page_factory=object, now_ms=NOW)


def _load(tmp_path: Path, name: str):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def test_full_flow(tmp_path: Path, fake_xhs) -> None:
    pl = _pipeline(tmp_path)
    pl.save_requirements({"budget_max": 5000, "bedrooms": [1, 2], "max_age_days": 30})

    pl.search(["东坝 整租", "八里庄 整租"])
    assert len(_load(tmp_path, "raw_feeds.json")) == len(POSTS) + 2  # 两个关键词结果去重

    pl.filter_candidates()
    reasons = {e["id"]: e["excluded_reason"] for e in _load(tmp_path, "excluded_feeds.json")}
    assert "求租" in reasons["s1"]
    assert "中介" in reasons["s2"]
    assert "a4" in reasons  # loft 标题在卡片级就被排除

    pl.inspect(limit=20)
    assert (tmp_path / "notes" / "a1.json").exists()
    n_detail = len(fake_xhs["detail"])
    pl.inspect(limit=20)  # 断点续跑：命中缓存，不再访问
    assert len(fake_xhs["detail"]) == n_detail

    pl.check_authors(limit=10)
    assert "u3" in fake_xhs["profile"]
    assert "u6" not in fake_xhs["profile"]  # 作者已回复“已租出”的不浪费一次访问

    pl.analyze()
    results = _load(tmp_path, "analyzed_results.json")
    res = {r["id"]: r for r in results}
    assert res["a1"]["tier"] == "trusted"
    assert res["a2"]["tier"] == "trusted"
    assert res["a3"]["tier"] == "suspect"
    assert any(h["id"] == "multi_listing" for h in res["a3"]["risks"])
    assert res["a6"]["tier"] == "excluded"
    order = [r["id"] for r in results]
    assert order.index("a1") < order.index("a3") < order.index("a6")

    md = pl.digest().read_text(encoding="utf-8")
    assert "东坝两居室转租" in md
    assert "主页另有" in md
    assert "已排除" in md


def test_verification_stops_and_keeps_progress(tmp_path: Path, fake_xhs, monkeypatch) -> None:
    pl = _pipeline(tmp_path)
    pl.search(["x"])
    pl.filter_candidates()
    seen: list[str] = []

    def flaky(page, fid, tok, keyword=""):
        if seen:
            raise PageNotAccessibleError("触发了小红书验证，需要在浏览器中扫码完成验证后重试")
        seen.append(fid)
        return _detail(POSTS[fid])

    monkeypatch.setattr(xhs.feed_detail, "get_feed_detail", flaky)
    with pytest.raises(VerificationRequiredError):
        pl.inspect(limit=20)
    assert len(list((tmp_path / "notes").glob("*.json"))) == 1
    assert (tmp_path / "detailed_posts.json").exists()


def test_comment_requires_confirm_and_dedupes(tmp_path: Path, fake_xhs) -> None:
    pl = _pipeline(tmp_path)
    (tmp_path / "detailed_posts.json").write_text(
        json.dumps(list(POSTS.values()), ensure_ascii=False), encoding="utf-8"
    )
    pl.analyze()

    plan = pl.comment("请问还在吗？", ids=["a1"])
    assert [p["id"] for p in plan] == ["a1"]
    assert fake_xhs["comment"] == []  # 未确认不发送

    pl.comment("请问还在吗？", ids=["a1"], confirm=True)
    assert fake_xhs["comment"] == [("a1", "请问还在吗？")]

    assert pl.comment("请问还在吗？", ids=["a1"], confirm=True) == []  # 已评论过不重复
    assert len(fake_xhs["comment"]) == 1


@pytest.mark.skipif(not _has_font(), reason="no CJK font")
def test_wrap_respects_width() -> None:
    from rental.poster import Fonts, text_w

    f = Fonts().get(16)
    lines = wrap(f, "这是一段很长的中文描述" * 20, 300, 2)
    assert len(lines) == 2
    assert lines[-1].endswith("…")
    assert all(text_w(f, ln) <= 300 for ln in lines)


@pytest.mark.skipif(not _has_font(), reason="no CJK font")
def test_poster_auto_and_curated(tmp_path: Path) -> None:
    imgs = []
    for i, color in enumerate([(200, 180, 150), (120, 160, 200), (90, 140, 90)]):
        p = tmp_path / f"room{i}.jpg"
        Image.new("RGB", (800, 1000), color).save(p)
        imgs.append(str(p))
    posts = [{**p, "images": imgs} for p in POSTS.values()]
    pl = _pipeline(tmp_path)
    (tmp_path / "detailed_posts.json").write_text(
        json.dumps(posts, ensure_ascii=False), encoding="utf-8"
    )
    pl.save_requirements({"budget_max": 5000})
    pl.analyze()

    png = pl.render_poster(output=tmp_path / "auto.png")
    assert png and png.exists()
    w, h = Image.open(png).size
    assert w == 1260 and h > 600

    curated = {
        "title": "朝阳东坝 · 个人整租精选",
        "entries": [
            {
                "id": "a1",
                "headline": "东坝 · 两居室整租",
                "price_text": "4500元/月（押一付三）",
                "rows": [["转租原因", "工作调动离京"], ["签约方式", "与房东重签，押一付三"]],
                "diagnosis": "生活细节充分，主页无其他房源。",
                "tier": "trusted",
            },
            {
                "id": "a3",
                "headline": "疑似中介马甲",
                "is_warning": True,
                "diagnosis": "主页 7 套房源",
            },
        ],
    }
    (tmp_path / "curated.json").write_text(
        json.dumps(curated, ensure_ascii=False), encoding="utf-8"
    )
    png2 = pl.render_poster(output=tmp_path / "curated.png")
    assert png2 and png2.exists()
    assert Image.open(png2).size[1] < h  # 只有两张卡片，比自动版短


def test_search_falls_back_when_filters_fail(tmp_path: Path, monkeypatch) -> None:
    used: list = []

    def search_feeds(page, kw, fo=None):
        used.append(fo)
        if fo is not None:
            raise ValueError("应用筛选失败: 筛选按钮不存在")
        return [_card(POSTS["a1"])]

    monkeypatch.setattr(xhs.search, "search_feeds", search_feeds)
    pl = _pipeline(tmp_path)
    pl.search(["东坝"])
    assert used[-1] is None
    assert [f["id"] for f in _load(tmp_path, "raw_feeds.json")] == ["a1"]
