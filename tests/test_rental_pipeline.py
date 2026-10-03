import json
import pytest
import os
import sys

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
    with open(filtered_path, "r", encoding="utf-8") as f:
        filtered = json.load(f)

    # Note 1 has "求", Note 3 has "合租", Note 4 has author "地产" and 3 bars
    # Only Note 2 and 5 should pass!
    ids = [item["id"] for item in filtered]
    assert "2" in ids
    assert "5" in ids
    assert "1" not in ids
    assert "3" not in ids
    assert "4" not in ids

def test_analysis_scoring(tmp_path):
    p = RentalPipeline(work_dir=str(tmp_path))
    sample_detailed = [
        {
            "id": "101",
            "title": "东坝华瀚福园两居室转租",
            "desc": "因工作调动，朋友离京，转租华瀚福园两居室4500元。合同到明年3月，和房东重新签合同，留下自买衣架。",
            "user": {"nickname": "李华"},
            "images": []
        },
        {
            "id": "102",
            "title": "精装公寓个人转租",
            "desc": "鼎新科技创意文化产业园精装公寓loft，电费1.5水费11。",
            "user": {"nickname": "化妆师小敏"},
            "images": []
        }
    ]
    det_path = tmp_path / "detailed.json"
    with open(det_path, "w", encoding="utf-8") as f:
        json.dump(sample_detailed, f, ensure_ascii=False)

    analyzed_path = p.analyze(str(det_path))
    with open(analyzed_path, "r", encoding="utf-8") as f:
        results = json.load(f)

    res_map = {r["id"]: r for r in results}
    # Note 101 should be high trust
    assert "高可信" in res_map["101"]["verdict"]
    assert res_map["101"]["price"] == 4500
    assert len(res_map["101"]["real_signals"]) >= 3

    # Note 102 should be excluded because it is an apartment
    assert "排除" in res_map["102"]["verdict"]
