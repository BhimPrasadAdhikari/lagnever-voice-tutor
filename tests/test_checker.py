"""Offline tests: the sympy checker and the notebook page model (no API keys needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import checker  # noqa: E402
import tutor  # noqa: E402


def test_count_of_real_solutions_with_modulus():
    spec = {"kind": "count", "reference": "x**2 - 5*Abs(x) + 6 = 0", "student_count": 4, "var": "x"}
    assert checker.check(spec)["status"] == "correct"
    spec["student_count"] = 2
    assert checker.check(spec)["status"] == "wrong"


def test_standard_limit():
    spec = {"kind": "limit", "expr": "(1 - cos(x))/x**2", "var": "x", "point": "0", "student_value": "1/2"}
    assert checker.check(spec)["status"] == "correct"
    spec["student_value"] = "1"
    assert checker.check(spec)["status"] == "wrong"


def test_substitution_step_is_not_judged_across_variables():
    spec = {"kind": "equation", "reference": "x**2 - 5*Abs(x) + 6 = 0", "student": "t**2 - 5*t + 6 = 0", "var": "x"}
    assert checker.check(spec)["status"] == "unchecked"


def test_bad_spec_never_raises():
    assert checker.check({"kind": "limit", "expr": "((("})["status"] == "unchecked"
    assert checker.check(None)["status"] == "unchecked"


def test_page_ops_strike_and_tick():
    page = tutor.new_page()
    page = tutor.apply_ops(page, [{"op": "new_question", "text": "x^2 - 5|x| + 6 = 0"},
                                  {"op": "student_step", "text": "t = 2 or 3, so x = 2 or 3"},
                                  {"op": "strike", "correction": "|x| = 2 gives x = ±2"},
                                  {"op": "student_step", "text": "x = ±2, ±3"}, {"op": "tick"}])
    assert page["lines"][0]["struck"] and page["lines"][0]["correction"]
    assert page["lines"][1]["ticked"]
