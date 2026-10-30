from datetime import date

import pytest
from starlette.datastructures import QueryParams

from hr_resume_hub.dashboard import auth, repository
from hr_resume_hub.dashboard.app import ApiError, parse_filters
from hr_resume_hub.dashboard.repository import Filters, build_where, like_pattern


def test_no_filters_means_no_where_clause():
    assert build_where(Filters()) == ("", [])


def test_search_words_are_and_combined_and_wildcards_escaped():
    where, params = build_where(Filters(q="sap 100%"))
    assert where.count("ILIKE") == 2
    assert params == ["%sap%", "%100\\%%"]
    assert like_pattern("a_b") == "%a\\_b%"


def test_position_terms_require_every_word_in_any_order():
    where, params = build_where(Filters(position_terms=("sap pp",)))
    assert "c.applied_position ILIKE ALL (%s)" in where
    assert params == [["%sap%", "%pp%"]]


def test_exact_positions_and_terms_are_or_combined():
    where, params = build_where(Filters(positions=("Design Engineer",), position_terms=("quality",)))
    assert "c.applied_position = ANY(%s) OR c.applied_position ILIKE ALL (%s)" in where
    assert params == [["Design Engineer"], ["%quality%"]]


def test_star_filters_use_null_checks_without_parameters():
    where, params = build_where(Filters(hr_star=True, hod_star=False))
    assert "hr.candidate_key IS NOT NULL" in where
    assert "hod.candidate_key IS NULL" in where
    assert params == []


def test_numeric_and_date_filters_are_parameterised():
    where, params = build_where(
        Filters(exp_min=2, exp_max=8, score_min=70, received_from=date(2026, 6, 1), received_to=date(2026, 6, 30))
    )
    assert where.count("%s") == 5
    assert params == [2, 8, 70, date(2026, 6, 1), date(2026, 6, 30)]


def test_sort_keys_are_a_closed_whitelist():
    assert all(expression.startswith(("c.", "hr.", "hod.")) for expression in repository.SORT_COLUMNS.values())
    assert "received" in repository.SORT_COLUMNS


def test_parse_filters_reads_repeated_params_and_tri_state():
    params = QueryParams(
        "position=A&position=B&position_term=sap+pp&hr_star=yes&hod_star=no&exp_min=2.5&received_from=2026-06-01"
    )
    filters = parse_filters(params)
    assert filters.positions == ("A", "B")
    assert filters.position_terms == ("sap pp",)
    assert filters.hr_star is True and filters.hod_star is False
    assert filters.exp_min == 2.5
    assert filters.received_from == date(2026, 6, 1)


@pytest.mark.parametrize("query", ["exp_min=abc", "received_from=31-06-2026", "score_min=1.5"])
def test_parse_filters_rejects_bad_numbers_and_dates(query):
    with pytest.raises(ApiError) as error:
        parse_filters(QueryParams(query))
    assert error.value.status == 400


def test_password_hash_round_trip():
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("wrong password", stored)
    assert not auth.verify_password("anything", "not-a-hash")


def test_username_is_normalised_and_validated():
    assert auth.normalise_username("  Meena.S ") == "meena.s"
    for bad in ("a", "has space", "x" * 41, "", None, 5):
        with pytest.raises(ValueError):
            auth.normalise_username(bad)


def test_password_length_is_enforced_both_ways():
    assert auth.check_password("a" * 10) == "a" * 10
    for bad in ("short", "a" * 201, None, 1234567890):
        with pytest.raises(ValueError):
            auth.check_password(bad)


def test_display_name_is_trimmed_and_required():
    assert auth.clean_display_name("  Meena   S ") == "Meena S"
    for bad in ("", "   ", "x" * 81, None):
        with pytest.raises(ValueError):
            auth.clean_display_name(bad)


def test_admin_is_a_user_role_but_never_a_star_role():
    assert "admin" in repository.USER_ROLES
    assert "admin" not in repository.STAR_ROLES


def test_login_limiter_blocks_after_repeated_failures_and_resets():
    limiter = auth.LoginLimiter(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert not limiter.blocked("ip|user")
        limiter.record_failure("ip|user")
    assert limiter.blocked("ip|user")
    assert not limiter.blocked("ip|other")
    limiter.reset("ip|user")
    assert not limiter.blocked("ip|user")
