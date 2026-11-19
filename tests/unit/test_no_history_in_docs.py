"""The reference documents carry no history (CLAUDE.md).

README.md, FORK.md, OPERATIONS.md, SECURITY.md, CLAUDE.md, .github/WORKFLOWS.md
and docs/ say what is true now. What changed and when goes in CHANGELOG.md and
commit messages. This scans those files, line by line, for phrases that only
describe the past: the comment check's own "the code's past" marker, "at time
of writing", and a date tied to a change ("since" or "removed on" before a
date, or a sync named by its date).

A date on its own is not flagged. The documents cite the day a claim was
measured or verified, and that evidence belongs beside the claim. A line that matches but reads in the present tense
is listed in ALLOWED with the reason.
"""
import pathlib
import re

import pytest

from tests.unit.test_no_history_in_code import MARKERS

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCUMENTS = (
    "README.md",
    "FORK.md",
    "OPERATIONS.md",
    "SECURITY.md",
    "CLAUDE.md",
    ".github/WORKFLOWS.md",
    "docs/*.md",
)

_DATE = r"20\d\d-[01]\d-[0-3]\d"
DOC_MARKERS = {
    "the past": MARKERS["the code's past"],
    "time of writing": re.compile(r"\b(?:at (?:the )?time of writing|as of this writing)\b", re.I),
    "dated change": re.compile(
        rf"\b(?:since|until|added|removed|changed|introduced|fixed|renamed|dropped)"
        rf"(?: (?:on|in))? (?:the )?{_DATE}\b|\b(?:in|from|after|before|at) the {_DATE}\b",
        re.I,
    ),
}

# Lines that match a marker but describe the present, each with why.
ALLOWED = {
    "OPERATIONS.md": {
        "since auditing a lock file that no longer matches `pyproject.toml` would scan the":
            "a lock file out of step with pyproject.toml, a present condition",
    },
}


def _documents():
    for pattern in DOCUMENTS:
        yield from sorted(ROOT.glob(pattern))


def history_lines():
    """(file, line, marker, text) for every document line that reads as history,
    the ALLOWED lines excepted."""
    found = []
    for path in _documents():
        rel = path.relative_to(ROOT).as_posix()
        allowed = ALLOWED.get(rel, {})
        for number, line in enumerate(path.read_text().splitlines(), 1):
            text = line.strip()
            if text in allowed:
                continue
            for marker, pattern in DOC_MARKERS.items():
                if pattern.search(text):
                    found.append((rel, number, marker, text))
                    break
    return found


def test_reference_documents_carry_no_history():
    lines = history_lines()
    assert not lines, (
        "Reference documents say what is true now; history goes in CHANGELOG.md "
        "(CLAUDE.md). Reword these, or list a present-tense line in ALLOWED with "
        "the reason:\n"
        + "\n".join(f"  {rel}:{n} [{marker}] {text}" for rel, n, marker, text in lines)
    )


def test_allowed_lines_are_still_there():
    """An allowance whose line has gone is dropped, so it cannot excuse a line
    written later."""
    stale = []
    for rel, texts in ALLOWED.items():
        path = ROOT / rel
        present = {line.strip() for line in path.read_text().splitlines()} if path.exists() else set()
        stale.extend(f"{rel}: {text}" for text in sorted(set(texts) - present))
    assert not stale, "ALLOWED lists lines no longer in the documents:\n  " + "\n  ".join(stale)


@pytest.mark.parametrize(
    "text",
    [
        "- Full suite: all pass (1683 at time of writing).",
        "since 2026-10-28 their time *text* is also given its zone in place",
        "Three tools added alongside it were removed on 2026-09-19.",
        "the tool modules, which conflicted in the 2026-09-10 sync",
        "That test previously caught it by accident.",
        "The browser import was originally email-only.",
    ],
)
def test_each_marker_is_caught(text):
    assert any(pattern.search(text) for pattern in DOC_MARKERS.values()), text


@pytest.mark.parametrize(
    "text",
    [
        "~4.19 (verified 2026-10-28 against a run of known distance, time and gain).",
        "live post-run snapshot (2026-10-27, `AFTER_POST_EXERCISE_RESET`) carried",
        "Measured on plan 1789330190 over 2026-09-13..09-26: 7 calendar entries",
        "#### CodeQL baseline (first scan, 2026-09-02)",
        "  the library used to reach the Garmin Connect API.",
        "    {\"date\": \"2026-05-12\", \"workout_id\": 1234567890},",
    ],
)
def test_present_tense_and_evidence_are_not_flagged(text):
    assert not any(pattern.search(text) for pattern in DOC_MARKERS.values()), text
