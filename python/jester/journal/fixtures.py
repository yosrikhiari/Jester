"""The fixture gates for the proof checks (plan gates G2, G3, G4).

A checker nobody has tested against known answers is a guess with a
confident voice. Each checker has a set of labelled cases, run offline:

    G2 citations.json  31 cases: real quotes, dead links, invented links,
                       wrong quotes, paywalls, redirects, PDFs, manual copies.
                       Gate: every case gets the expected verdict.
    G3 numbers.json    a clean draft that must pass, 12 drafts with one
                       planted unbacked number each that must all fail,
                       and the edge cases (run values, sources, dates).
                       Gate: every case gets the expected verdict.
    G4 lint.json       15 paragraphs written by local models (unedited) and
                       15 human paragraphs from public-domain PEPs.
                       Gate: at least 12 AI flagged, at most 2 human flagged.
       lint_holdout    15 + 15 more, generated and picked AFTER the rules were
                       fixed (half the AI prompts ask for a first-person
                       "what I did" voice). Not a gate: it is the honest
                       number. Measured 2026-10-01: 10/15 AI flagged, 0/15
                       human. Known gap: the human side is formal PEP prose,
                       so false alarms on casual first-person writing are
                       not measured yet.

The web is faked: a case lists what each URL answers, and the snapshot code
runs against that, so the real fetch -> store -> check path is what is tested.
"""

from __future__ import annotations

import io
import json
import tempfile
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional

from jester import journal as J
from jester.journal import checks as C
from jester.journal import snapshots as S

CASES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "journal"
LINT_MIN_AI_FLAGGED = 12
LINT_MAX_HUMAN_FLAGGED = 2
MIN_PLANTED = 10


class FixtureError(Exception):
    pass


class _Response(io.BytesIO):
    def __init__(self, url: str, answer: Dict):
        super().__init__(answer.get("body", "").encode("utf-8"))
        self.status = answer.get("status", 200)
        self._url = answer.get("final_url", url)
        self.headers = {"Content-Type": answer.get("content_type", "text/html; charset=utf-8")}

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status


class FakeWeb:
    """An opener that answers from a case's `web` table and nothing else.

    A URL the case does not list is treated like an invented one: it does
    not resolve. That is the right default for a test of a citation checker.
    """

    def __init__(self, web: Dict[str, Dict]):
        self.web = web

    def __call__(self, req, timeout=None):
        url = req.full_url
        answer = self.web.get(url)
        if answer is None or answer.get("unreachable"):
            raise urllib.error.URLError("name or service not known")
        if answer.get("status", 200) >= 400:
            raise urllib.error.HTTPError(url, answer["status"], answer.get("reason", "error"), {}, None)
        return _Response(url, answer)


def _load(name: str, directory: Optional[Path]) -> List[Dict]:
    path = Path(directory or CASES_DIR) / name
    if not path.exists():
        raise FixtureError(f"missing fixture file {path}")
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise FixtureError(f"{path} must be a non-empty list of cases")
    return cases


def _snapshot_all(db, root: Path, case: Dict) -> None:
    web = FakeWeb(case.get("web", {}))
    for url in case.get("web", {}):
        S.take(db, root, url, opener=web)
    for url in case.get("fetch_only", []):
        S.take(db, root, url, opener=web)
    for url, text in case.get("manual", {}).items():
        src = root / "manual.txt"
        src.write_text(text, encoding="utf-8")
        S.take(db, root, url, from_file=src)


def _verdict(case: Dict, outcome: C.Outcome) -> Dict:
    want = case["want"]
    got = "pass" if outcome.ok else "fail"
    messages = [f["message"] for f in outcome.findings]
    ok = got == want
    reason = case.get("reason")
    if ok and want == "fail" and reason:
        ok = any(reason in m for m in messages)
    return {"id": case["id"], "want": want, "got": got, "pass": ok,
            "reason": reason or "", "findings": messages}


def check_citations_gate(directory: Optional[Path] = None) -> Dict:
    results = []
    for case in _load("citations.json", directory):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = J.open_journal(str(root / "j.db"))
            try:
                _snapshot_all(db, root, case)
                results.append(_verdict(case, C.check_citations(db, root, case["article"])))
            finally:
                db.close()
    return {"gate": "G2 citations", "cases": results,
            "pass": all(r["pass"] for r in results)}


def check_numbers_gate(directory: Optional[Path] = None) -> Dict:
    results = []
    for case in _load("numbers.json", directory):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = J.open_journal(str(root / "j.db"))
            try:
                jid = J.create_journal(db, "Fixture")["id"]
                other = J.create_journal(db, "Someone else's")["id"]
                for run in case.get("runs", []):
                    owner = other if run.get("other_journal") else jid
                    exp = J.add_experiment(db, owner)
                    J.add_run(db, exp, status=run.get("status", "ok"), result=run.get("result", {}))
                _snapshot_all(db, root, case)
                results.append(_verdict(case, C.check_numbers(db, root, jid, case["article"])))
            finally:
                db.close()
    planted = [r for r in results if r["id"].startswith("planted-")]
    enough = len(planted) >= MIN_PLANTED
    return {"gate": "G3 numbers", "cases": results, "planted": len(planted),
            "pass": enough and all(r["pass"] for r in results)}


def check_lint_gate(directory: Optional[Path] = None, name: str = "lint.json") -> Dict:
    """G4 on `lint.json`. With `name="lint_holdout.json"`, the same count on
    paragraphs that were NOT looked at while the rules were chosen -- the
    number to quote, since a rule set always scores best on the cases that
    shaped it."""
    results = []
    for case in _load(name, directory):
        score, findings, _ = C.lint_prose(case["text"])
        results.append({"id": case["id"], "origin": case["origin"], "score": score,
                        "flagged": score >= C.FAIL_AT,
                        "findings": [f["message"] for f in findings]})
    ai = [r for r in results if r["origin"] == "ai"]
    human = [r for r in results if r["origin"] == "human"]
    ai_flagged = sum(r["flagged"] for r in ai)
    human_flagged = sum(r["flagged"] for r in human)
    return {"gate": "G4 lint", "cases": results, "ai": len(ai), "human": len(human),
            "ai_flagged": ai_flagged, "human_flagged": human_flagged,
            "pass": ai_flagged >= LINT_MIN_AI_FLAGGED and human_flagged <= LINT_MAX_HUMAN_FLAGGED}


def check_all(directory: Optional[Path] = None) -> List[Dict]:
    return [check_citations_gate(directory), check_numbers_gate(directory), check_lint_gate(directory)]
