"""Deterministic maths checker for the Lagnever voice tutor.

The tutor brain proposes a verdict on the student's step AND a machine-checkable spec;
sympy re-checks it in milliseconds. If they disagree, sympy wins and the
tutor is asked to redo the turn, so a correct step is never struck in red
and a wrong one is never ticked.

Spec kinds (all expressions in sympy syntax, e.g. "3*x - 5 = 16", "Abs(x)",
"sin(x)/(2*x)", "sqrt(2)", "pi"):
  equation   {"kind","reference","student","var"}   same real solution set?
  solutions  {"kind","reference","student_values","var"}
  count      {"kind","reference","student_count","var"}
  limit      {"kind","expr","var","point","student_value"}
  value      {"kind","expr","student_value"}         expr == value?
  none       not machine-checkable (concepts, words, physics reasoning...)
"""
from __future__ import annotations

import sys as _sys
import os as _os
_USER_SITE = _os.path.expanduser("~/.local/lib/python3.11/site-packages")
if _os.path.isdir(_USER_SITE) and _USER_SITE not in _sys.path:  # course labs install extras here
    _sys.path.append(_USER_SITE)

import concurrent.futures as cf
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application,
                                        parse_expr, standard_transformations)

_T = standard_transformations + (implicit_multiplication_application, convert_xor)
_POOL = cf.ThreadPoolExecutor(max_workers=2)


def _p(s: str, var: sp.Symbol | None = None) -> sp.Expr:
    local = {"e": sp.E, "pi": sp.pi, "Abs": sp.Abs, "abs": sp.Abs}
    if var is not None:
        local[str(var)] = var
    return parse_expr(str(s).replace("−", "-").replace("×", "*").replace("÷", "/"),
                      local_dict=local, transformations=_T)


def _sols(eq: str, var: sp.Symbol) -> sp.Set:
    if "=" in eq:
        lhs, rhs = eq.split("=", 1)
        f = _p(lhs, var) - _p(rhs, var)
    else:
        f = _p(eq, var)
    return sp.solveset(sp.simplify(f), var, sp.S.Reals)


def _num(v: Any) -> sp.Expr:
    return sp.nsimplify(_p(v)) if not isinstance(v, (int, float)) else sp.nsimplify(v)


def _check(spec: dict[str, Any]) -> dict[str, Any]:
    kind = (spec or {}).get("kind", "none")
    if kind == "none":
        return {"status": "unchecked"}
    var = sp.Symbol(spec.get("var") or "x", real=True)
    if kind == "equation":
        # A substitution (x -> t) changes the variable, so solution sets are not
        # comparable: never judge across different variables, just don't override.
        names = lambda e: {str(x) for x in _p(e.replace("=", "-(") + (")" if "=" in e else "")).free_symbols}
        if names(spec["reference"]) != names(spec["student"]):
            return {"status": "unchecked", "why": "different variables (substitution step)"}
        a, b = _sols(spec["reference"], var), _sols(spec["student"], var)
        if isinstance(a, sp.ConditionSet) or isinstance(b, sp.ConditionSet):
            return {"status": "unchecked", "why": "could not solve"}
        ok = a == b
        return {"status": "correct" if ok else "wrong",
                "why": f"solutions of reference {a} vs student's line {b}"}
    if kind in ("solutions", "count"):
        ref = _sols(spec["reference"], var)
        if not isinstance(ref, sp.FiniteSet):
            return {"status": "unchecked", "why": f"solution set {ref}"}
        if kind == "count":
            ok = int(spec["student_count"]) == len(ref)
            return {"status": "correct" if ok else "wrong",
                    "why": f"{len(ref)} real solutions {ref}, student said {spec['student_count']}"}
        stu = sp.FiniteSet(*[_num(v) for v in spec.get("student_values", [])])
        ok = stu == ref
        return {"status": "correct" if ok else "wrong", "why": f"real solutions {ref}, student gave {stu}"}
    if kind == "limit":
        point = spec.get("point", "0")
        pt = sp.oo if str(point) in ("oo", "inf", "infinity") else _num(point)
        L = sp.limit(_p(spec["expr"], var), var, pt)
        ok = sp.simplify(L - _num(spec["student_value"])) == 0
        return {"status": "correct" if ok else "wrong", "why": f"limit is {L}, student said {spec['student_value']}"}
    if kind == "value":
        v = sp.simplify(_p(spec["expr"]) - _num(spec["student_value"]))
        ok = v == 0
        return {"status": "correct" if ok else "wrong",
                "why": f"{spec['expr']} = {sp.nsimplify(_p(spec['expr']))}, student said {spec['student_value']}"}
    return {"status": "unchecked", "why": f"unknown kind {kind}"}


def check(spec: dict[str, Any] | None, timeout: float = 1.5) -> dict[str, Any]:
    """Never raises; returns {"status": correct|wrong|unchecked, "why": ...}."""
    if not spec:
        return {"status": "unchecked"}
    fut = _POOL.submit(_check, spec)
    try:
        return fut.result(timeout=timeout)
    except cf.TimeoutError:
        return {"status": "unchecked", "why": "timeout"}
    except Exception as exc:  # bad spec from the model -> just don't override
        return {"status": "unchecked", "why": f"spec error: {exc}"}
