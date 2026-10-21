import json

import pytest

from rental_hunter.cli import main
from rental_hunter.report import analyze, render_html, safe_url, write_report


def test_budget_price_conflict_and_deposit_not_used_as_rent():
    rows = analyze(
        [
            {"title": "整租", "desc": "押金3000元，月租4500元"},
            {"title": "整租", "desc": "月租6000元"},
            {"title": "整租", "desc": "月租4000元，短租5000元/月"},
            {"title": "整租", "desc": "押金3800元"},
        ],
        3000,
        5000,
    )["listings"]
    by_desc = {r["description"]: r for r in rows}
    assert by_desc["押金3000元，月租4500元"]["price"] == 4500
    assert by_desc["月租6000元"]["mismatches"]
    assert by_desc["月租4000元，短租5000元/月"]["price"] is None
    assert by_desc["押金3800元"]["price"] is None


def test_negations_and_personal_identity_are_not_evidence():
    report = analyze([{"title": "化妆师｜宝妈｜整租", "desc": "不是合租，无中介费，月租3.5k"}])
    row = report["listings"][0]
    assert row["housing"] == "entire"
    assert row["price"] == 3500
    assert row["status"] == "符合已知条件，待实地核实"
    assert not any(e["quote"] == "中介费" for e in row["evidence"])
    assert "可信" not in json.dumps(report, ensure_ascii=False)


def test_duplicates_and_hostile_input_do_not_execute_or_leak_tokens(tmp_path):
    item = {
        "id": "1",
        "title": "<script>alert(1)</script>",
        "desc": '<img src=x onerror="alert(2)">',
        "url": "https://www.xiaohongshu.com/explore/abc?xsec_token=SECRET",
        "xsec_token": "SECRET",
        "cookies": "PRIVATE",
    }
    report = analyze([item, item])
    assert report["count"] == 1 and report["duplicate_count"] == 1
    output = write_report(report, str(tmp_path / "report"))
    body = (output / "report.html").read_text("utf-8")
    assert "<script>" not in body and "<img" not in body
    assert "SECRET" not in body and "PRIVATE" not in body
    assert report["listings"][0]["url"].endswith("/abc")
    with pytest.raises(FileExistsError):
        write_report(report, str(output))


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://www.xiaohongshu.com.evil.test/a",
        "https://user@www.xiaohongshu.com/a",
        "https://www.xiaohongshu.com:123/a",
    ],
)
def test_invalid_sources(url):
    assert safe_url(url) == ""


@pytest.mark.parametrize(
    "low,high", [(5000, 3000), (-1, 5000), (0, float("nan")), (0, float("inf"))]
)
def test_invalid_budgets(low, high):
    with pytest.raises(ValueError):
        analyze([], low, high)


def test_source_required_and_empty_results_are_explicit():
    with pytest.raises(ValueError):
        analyze([{}])
    report = analyze([])
    assert report["count"] == 0
    assert "0 条候选" in render_html(report)


def test_installed_command_contract_and_error(tmp_path):
    assert main(["demo", "--output", str(tmp_path / "demo")]) == 0
    assert (
        main(
            ["report", "--input", str(tmp_path / "absent.json"), "--output", str(tmp_path / "bad")]
        )
        == 2
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3000-4500元/月", None),
        ("3000元-4500元/月", None),
        ("月租3000至4500元", None),
        ("月租3-4.5k", None),
        ("租金30000-45000元/年", None),
        ("租金36000元/年", None),
        ("房租300元/天", None),
        ("租金每月：3800元", 3800),
        ("月租￥3800", 3800),
    ],
)
def test_price_ranges_and_non_monthly_quotes(text, expected):
    row = analyze([{"title": "整租", "desc": text}])["listings"][0]
    assert row["price"] == expected


def test_question_markdown_does_not_embed_listing_html_or_images(tmp_path):
    report = analyze(
        [
            {
                "title": '<img src="https://example.com/x"> ![x](https://example.com/y)',
                "desc": "月租3000元，整租",
            }
        ]
    )
    output = write_report(report, str(tmp_path / "report"))
    text = (output / "questions.md").read_text("utf-8")
    assert "<img" not in text and "![x](" not in text
