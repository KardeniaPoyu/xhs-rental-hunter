import json
import os
import sys

import pytest

# Add scripts to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from xhs_rental_pipeline import RentalPipeline


def test_pipeline_init(tmp_path):
    p = RentalPipeline(work_dir=str(tmp_path))
    assert os.path.exists(p.work_dir)
    assert os.path.exists(p.cache_dir)


def test_filter_candidates(tmp_path):
    p = RentalPipeline(work_dir=str(tmp_path))
    sample_feeds = [
        {"id": "1", "title": "求北京个人转租", "author": "小白"},
        {"id": "2", "title": "东坝一居室个人转租", "author": "小红"},
        {"id": "3", "title": "朝阳合租单间转租", "author": "小兰"},
        {"id": "4", "title": "北京个人转租｜0中介费｜华瀚福园｜精装", "author": "地产小王"},
        {"id": "5", "title": "八里庄北里两居室转租", "author": "张三"},
    ]
    raw_path = tmp_path / "raw.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(sample_feeds, f, ensure_ascii=False)

    filtered_path = p.filter_candidates(str(raw_path))
    with open(filtered_path, encoding="utf-8") as f:
        filtered = json.load(f)

    # Note 1 has "求", Note 3 has "合租", Note 4 has author "地产" and 3 bars
    # Only Note 2 and 5 should pass!
    ids = [item["id"] for item in filtered]
    assert "2" in ids
    assert "5" in ids
    assert "1" not in ids
    # Housing type belongs in the explicit comparison step; occupation/title style
    # cannot establish whether a person is an agent.
    assert "3" in ids
    assert "4" in ids


def test_analysis_scoring(tmp_path):
    p = RentalPipeline(work_dir=str(tmp_path))
    sample_detailed = [
        {
            "id": "101",
            "title": "东坝华瀚福园两居室转租",
            "desc": (
                "因工作调动，整租华瀚福园两居室，月租4500元。"
                "合同到明年3月，和房东重新签合同，留下自买衣架。"
            ),
            "user": {"nickname": "李华"},
            "images": [],
        },
        {
            "id": "102",
            "title": "精装公寓个人转租",
            "desc": "鼎新科技创意文化产业园精装公寓loft，电费1.5水费11。",
            "user": {"nickname": "化妆师小敏"},
            "images": [],
        },
    ]
    det_path = tmp_path / "detailed.json"
    with open(det_path, "w", encoding="utf-8") as f:
        json.dump(sample_detailed, f, ensure_ascii=False)

    analyzed_path = p.analyze(str(det_path))
    with open(analyzed_path, encoding="utf-8") as f:
        results = json.load(f)

    res_map = {r["id"]: r for r in results}
    # Matching stated constraints is not proof of credibility.
    assert "符合已知条件" in res_map["101"]["verdict"]
    assert res_map["101"]["price"] == 4500
    assert res_map["101"]["evidence"]

    # A commercial apartment label does not reveal its monthly price or tenure.
    assert "信息不足" in res_map["102"]["verdict"]


def test_followup_creates_draft_without_browser(tmp_path, monkeypatch):
    p = RentalPipeline(work_dir=str(tmp_path))
    monkeypatch.setattr(p, "get_page", lambda: pytest.fail("must not connect"))
    source = tmp_path / "input.json"
    source.write_text(json.dumps([{"id": "1", "title": "test", "mismatches": []}]), "utf-8")
    result = p.batch_comment(str(source))
    with open(result, encoding="utf-8") as source:
        assert json.load(source)[0]["id"] == "1"
