#!/usr/bin/env python
"""Fail if a user-facing backend string literal contains Han characters.

**What this catches.** A `detail=` (or any string sent to a client) written in
Traditional Chinese. The frontend has a typed zh-HK/en dictionary, and
`frontend/scripts/check-i18n.mjs` enforces that no user-facing string lives
outside it — but that script only walks `frontend/src`, so a sentence baked into
a backend response is invisible to it. An English-locale user then reads Chinese
out of an error toast, and nothing in CI notices.

**Why a lint and not a test.** The failure is not a behaviour, it is a *literal
in a diff that looks fine in the reviewer's own language*. Only a scanner over
the source can see it, and only at the moment it is introduced.

**What it deliberately allows** (each, or the rule would be unusable):

* **Comments and docstrings.** A Chinese comment is a note to the next reader,
  not text a user receives. Also: this codebase's documentation is bilingual.
* **`seed.py`.** Sample data. A seeded nickname is meant to look like a real
  zh-HK user's, and localising it would make the dev fixtures useless for the
  UI that renders them.
* **Log calls.** A log line is for an operator, not a user.

The heuristic is "is this Han text in a position that reaches a client?", not
"does this file contain Han". A blunt "no Han anywhere in app/" would fail on the
seed data and on every Chinese comment, and a rule that has to be disabled is a
rule that gets disabled.

**Known limit.** A Han string that is assigned to a variable and passed to
`detail=` later is not caught, because the linter does not track dataflow. The
common shape — a literal in the call — is caught, and that is where this defect
has actually appeared.

Run:  python scripts/check_no_hardcoded_cjk.py
Exit: 0 clean, 1 on any finding (so it can gate CI).
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

#: Names whose string value reaches a client.
#:
#: Checked both as a call keyword (`detail="…"`) and as a dict key
#: (`{"detail": "…"}`), because both shapes are in use here — `HTTPException`
#: takes the former, a raw response dict the latter.
_USER_FACING = frozenset({"detail", "title", "body", "message"})

#: Files exempt from the rule, with the reason it is safe.
_EXEMPT = {
    # Dev fixtures: a seeded nickname is *meant* to read like a real zh-HK user,
    # and translating it would make the fixtures useless for testing the UI that
    # renders them.
    "seed.py": "sample data, not a response",
}

#: Callables that do not produce client-visible strings even when the value looks
#: like a message. A log line is for an operator.
_NOT_USER_FACING_CALLS = frozenset(
    {"warning", "info", "debug", "error", "critical", "log", "exception"}
)


def _literal_strings(node: ast.AST):
    """String constants inside `node`, including implicitly concatenated parts."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub


def _callee_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def check_file(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        # A file that does not parse is a different problem, reported elsewhere.
        return [f"{path}: cannot parse ({exc})"]

    findings: list[str] = []
    for node in ast.walk(tree):
        # Shape 1: keyword argument — `HTTPException(detail="…")`.
        if isinstance(node, ast.Call) and _callee_name(node) not in _NOT_USER_FACING_CALLS:
            for kw in node.keywords:
                if kw.arg not in _USER_FACING:
                    continue
                findings.extend(_han_in(path, kw.value, f"`{kw.arg}=` keyword"))

        # Shape 2: dict literal — `{"detail": "…"}`. Both a returned response
        # body and a WS frame are written this way.
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value in _USER_FACING):
                    continue
                findings.extend(
                    _han_in(path, value, f"dict key {key.value!r}")
                )
    return findings


def _han_in(path: pathlib.Path, value: ast.AST, where: str) -> list[str]:
    out: list[str] = []
    for literal in _literal_strings(value):
        if HAN.search(literal.value):
            out.append(
                f"{path}:{literal.lineno}: Han literal in {where} — "
                f"send a language-neutral code and let the client translate it\n"
                f"    {literal.value[:90]!r}"
            )
    return out


def main() -> int:
    findings: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if path.name in _EXEMPT:
            continue
        findings.extend(check_file(path))

    if findings:
        print("Hard-coded Chinese in a user-facing backend string:\n")
        for item in findings:
            print(f"  {item}")
        print(
            f"\n{len(findings)} finding(s). Send a language-neutral code and let the "
            "client resolve it — see docs/AUDIT-2026-09-26.md (B6)."
        )
        return 1

    print("check_no_hardcoded_cjk: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
