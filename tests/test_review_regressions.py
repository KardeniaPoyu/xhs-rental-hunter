import json
import sys
from pathlib import Path

import pytest

from rental_hunter.report import analyze

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from xhs_rental_pipeline import RentalPipeline, main, select_poster_candidates


@pytest.mark.parametrize(
    "text,expected",
    [
        ("年租金36000元", None),
        ("租金36000元一年", None),
        ("年租金30,000-45,000元", None),
        ("租金3.6万一年", None),
        ("月租5,000元", 5000),
        ("月租5，000元", 5000),
        ("月租1.2万元", 12000),
        ("1.2万元/月", 12000),
        ("每月3800元", 3800),
        ("每月3-4千元", None),
        ("月租3800元，服务费另收300元/月", 3800),
        ("月租3800元，物业费每月200元", 3800),
        ("水电费另收300元/月", None),
        ("月租5,00元", None),
        ("月租3000元，押金另付4000元", 3000),
        ("月租0.8-1.2万元", None),
    ],
)
def test_monthly_prices_use_full_amount_and_correct_period(text, expected):
    assert analyze([{"title": "整租", "desc": text}])["listings"][0]["price"] == expected


@pytest.mark.parametrize("negative", ["不接受合租", "不考虑合租", "不支持合租", "不是合租"])
def test_negative_housing_terms_do_not_conflict(negative):
    row = analyze([{"title": "整租", "desc": f"月租3800元，{negative}"}])["listings"][0]
    assert row["housing"] == "entire"
    assert not row["mismatches"]


def test_reused_ids_and_template_copy_keep_distinct_metadata():
    a = {"id": "1", "title": "整租", "desc": "月租3000元", "published_at": "2026-10-01"}
    b = {**a, "published_at": "2026-10-02"}
    c = {"title": "整租", "desc": "月租3000元", "user": {"nickname": "A"}}
    d = {**c, "user": {"nickname": "B"}}
    report = analyze([a, b, c, d, a])
    assert report["count"] == 4 and report["duplicate_count"] == 1
    assert len({r["record_key"] for r in report["listings"]}) == 4


def test_pipeline_housing_source_mapping_no_token_and_warning_selection(tmp_path):
    data = [
        {
            "id": "1",
            "title": "合租",
            "desc": "每月3000元",
            "user": {"nickname": "first"},
            "xsec_token": "SECRET",
            "url": "https://www.xiaohongshu.com/explore/a?xsec_token=SECRET",
        },
        {"id": "1", "title": "整租", "desc": "月租8000元", "user": {"nickname": "second"}},
    ]
    source = tmp_path / "input.json"
    source.write_text(json.dumps(data), "utf-8")
    pipeline = RentalPipeline(work_dir=str(tmp_path))
    output = Path(pipeline.analyze(str(source), housing="shared"))
    assert "SECRET" not in output.read_text("utf-8")
    rows = json.loads(output.read_text("utf-8"))
    assert rows[0]["author"] == "first" and not rows[0]["mismatches"]
    assert rows[1]["author"] == "second" and rows[1]["mismatches"]
    chosen = select_poster_candidates(rows)
    assert len(chosen) == 2 and chosen[1]["is_warn_case"]
    assert "is_warn_case" not in rows[1]


def test_both_pipeline_commands_use_shared_defaults_and_pass_overrides(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(RentalPipeline, "search", lambda *a, **kw: "raw")
    monkeypatch.setattr(RentalPipeline, "filter_candidates", lambda *a, **kw: "filtered")
    monkeypatch.setattr(RentalPipeline, "inspect", lambda *a, **kw: "detailed")
    monkeypatch.setattr(RentalPipeline, "render_poster", lambda *a, **kw: "poster")
    monkeypatch.setattr(
        RentalPipeline, "analyze", lambda self, *args: calls.append(args) or "analyzed"
    )
    main(["analyze", "--detailed-file", "detailed", "--work-dir", str(tmp_path)])
    main(["run-all", "--keywords", "city", "--work-dir", str(tmp_path)])
    assert calls[0] == calls[1] == ("detailed", 0, 5000, "entire")
    main(
        [
            "run-all",
            "--keywords",
            "city",
            "--work-dir",
            str(tmp_path),
            "--min-budget",
            "1000",
            "--max-budget",
            "4000",
            "--housing",
            "shared",
        ]
    )
    assert calls[-1] == ("detailed", 1000, 4000, "shared")
