import collect


def test_month_range_handles_leap_february():
    assert collect.month_range("2024-02") == ("2024-02-01", "2024-02-29")
    assert collect.month_range("2025-02") == ("2025-02-01", "2025-02-28")


def test_months_between_crosses_year_boundary():
    assert collect.months_between("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_search_terms_only_count_public_authored_items():
    terms = collect.search_terms("2026-01")
    assert set(terms) == {"prs_opened", "prs_merged", "prs_closed", "issues_opened", "issues_closed"}
    assert all("author:ErikBjare" in q and "is:public" in q for q in terms.values())
    assert "-is:merged" in terms["prs_closed"]
    assert "merged:2026-01-01..2026-01-31" in terms["prs_merged"]


def test_csv_roundtrip_keeps_months_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "DATA", tmp_path)
    monkeypatch.setattr(collect, "MONTHLY_CSV", tmp_path / "monthly.csv")
    row = lambda m: {f: "1" for f in collect.FIELDS} | {"month": m}  # noqa: E731
    collect.write_rows({"2026-02": row("2026-02"), "2026-01": row("2026-01")})
    assert list(collect.read_rows()) == ["2026-01", "2026-02"]
