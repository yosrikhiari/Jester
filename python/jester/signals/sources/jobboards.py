"""Remote job boards with public APIs â€” companies advertising contract work.

**Why these exist.** The Hacker News "Who is hiring?" thread is the best buyer
source measured so far (~5% of adverts are buyers), and it changes once a
month. Between the 1st and the next 1st every run re-read the same ~490
adverts and stored none. These boards post every day, and each can be asked
for contract-shaped work specifically, which is the half of the market the
hiring rules call a buyer.

Measured on 2026-09-27, one request each:

* Himalayas â€” 1,243 live listings matching "developer" with employment type
  Contractor; of a 78-listing sample, 35 were in the Developer or Data Science
  categories (the rest hardware, content, sales, education â€” filtered out).
* Remotive â€” 18 software listings, 7 of them contract or freelance.
* Jobicy â€” 100 engineering listings, 6 contract.

Considered and NOT built: RemoteOK (its contract-tagged listings were crypto
trading and data annotation), HN "Freelancer? Seeking freelancer?" (99 sellers
to 2 buyers over six months), and Freelancer.com (its API terms require cached
data to be refreshed every 24 hours, which an archive cannot honour).

**The authority each runs on** is quoted in `access`, from the operator's own
API page. All three ask for a link back and a credit; every record keeps the
listing's own URL as `source_url`, and nothing here republishes a listing to
another site, which Himalayas and Remotive both forbid.

**A record is a job advert**, shaped like the HN one so the same hiring rules
read it the same way: a headline in the thread's convention
(COMPANY | ROLE | LOCATION | ENGAGEMENT | RATE) followed by the description.
`company` and `buyer_intent` are read off the listing, never inferred.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Iterator, List

from .. import Signal
from . import SourceError, register
from .hackernews import BACKOFF, RETRIES, _plain

#: Talent marketplaces. They post "contract" roles all day, and every one is
#: them recruiting freelancers to place with THEIR clients -- the same supply
#: side the hiring rules reject as "seeking work", wearing a company name. The
#: dry run on 2026-09-27 scored lemon.io and A.Team 1.00 buyers. Matched on the
#: company field only, never the description, so "ex-Toptal" in an advert
#: rejects nothing.
MARKETPLACES = ("lemon.io", "a.team", "toptal", "turing", "andela", "braintrust",
                "arc.dev", "gun.io", "mindrift", "outlier", "micro1", "mercor",
                "alignerr", "dataannotation", "crossing hurdles", "x-team",
                # Rating and annotation vendors: same shape, contract work for
                # freelancers they then resell (Welo Global's "Ads Quality
                # Rater" roles scored as thin buyers in the second dry run).
                "welo global", "appen", "telus digital", "lionbridge", "remotasks",
                "toloka",
                # Staffing consultancies placing contractors with THEIR clients:
                # supply side again. Three "buyers" on 2026-09-28. By full name,
                # so GoPro the camera company is not caught with it.
                "gopro consultancy",
                # Job platforms posting other employers' roles under their own
                # name: 13 "buyers" on 2026-09-28. Named, because the words in
                # their names ("jobs", "careers") are also how a company names
                # its own hiring page.
                "jobs for humanity", "thehivecareers")

#: Recruiters and staffing firms, known by what they call themselves. The
#: deep Himalayas pass on 2026-09-28 stored 1,126 new "buyers"; 50 were
#: XKTalent's, and Source Code Staffing, Boardroom Appointments and a dozen
#: "... Talent" firms followed. Each is placing a person with a client it
#: does not name: the buyer is that client, and the advert cannot reach it.
#: A pattern, not a list, because there are thousands of them. Matched on the
#: company field only. Deliberately NOT here: "careers", "jobs" and
#: "workforce" -- "Acme Careers" is Acme hiring for itself, and Workforce.com
#: sells HR software. The adverts' own wording ("Our client is ...") is
#: caught by the hiring rules' hard_reject, for every source.
_STAFFING_NAME = re.compile(r"(staffing|recruit\w*|talent|headhunt\w*|appointments)\b")

#: The engagement types worth storing when a board states the type itself.
#: A full-time listing is not a lead for fractional engineering, and long
#: board descriptions carry boilerplate ("employees and contractors") that
#: made the rules score Autodesk's full-time architect role a 1.00 buyer.
CONTRACT_SHAPED = ("contract", "freelance", "part-time", "part_time", "temporary")


#: Role titles that are gig tasks, not engineering: the first live run stored
#: Toloka AI's "Voice Recording - AI Trainer" listings as buyers, because the
#: board filed them under Developer. A company list cannot keep up with every
#: data vendor; the shape of the task can.
GIG_TASKS = ("ai trainer", "ai tutor", "annotat", "rater", "voice recording",
             "transcription", "data labeling", "data labelling", "online tutor")


def _is_gig_task(role: str) -> bool:
    r = (role or "").lower()
    return any(t in r for t in GIG_TASKS)


def _is_marketplace(company: str) -> bool:
    c = (company or "").strip().lower()
    return any(c == m or c.startswith(m + " ") or c.startswith(m + ",") for m in MARKETPLACES)


def _is_staffing_firm(company: str) -> bool:
    return bool(_STAFFING_NAME.search((company or "").lower()))


def _contract_shaped(kind: str) -> bool:
    k = (kind or "").lower()
    return any(t in k for t in CONTRACT_SHAPED)


#: Statuses worth waiting out; anything else will not change on a retry.
RETRYABLE = frozenset({429, 500, 502, 503, 504})
USER_AGENT = "jester-signals/1.0 (problem-signal collector)"


class _JobBoard:
    """Pacing, retries and the advert-to-record shape the boards share."""

    name = ""
    platform = ""
    access = ""
    terms_url = ""
    #: Seconds between requests. Each board sets its own, from its own terms.
    pace = 2.0
    #: What `collect` searches when it is not told: each board's own notion of
    #: a query (keywords, a category, an industry). The hiring rules' "12"
    #: means "twelve months" to Hacker News and nothing to a job board.
    default_queries: tuple = ()

    def __init__(self, opener=None, sleep=time.sleep, timeout: int = 30,
                 user_agent: str = USER_AGENT):
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep
        self._timeout = timeout
        self._user_agent = user_agent
        self._last_call = 0.0

    #: What `jester signals sources` prints. A plain string, because
    #: `available()` reads it off the class, not an instance.
    rate = ""

    def _wait_turn(self) -> None:
        gap = self.pace - (time.monotonic() - self._last_call)
        if gap > 0:
            self._sleep(gap)
        self._last_call = time.monotonic()

    def _get(self, url: str) -> dict:
        """Fetch JSON, paced, retrying only what is worth retrying."""
        req = urllib.request.Request(url, headers={"User-Agent": self._user_agent,
                                                   "Accept": "application/json"})
        last = ""
        status = ""
        for attempt in range(RETRIES):
            self._wait_turn()
            try:
                with self._opener(req, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                last, status = f"HTTP {exc.code} from {self.name}", str(exc.code)
                if exc.code not in RETRYABLE:
                    raise SourceError(last, status=status) from exc
            except urllib.error.URLError as exc:
                last, status = f"cannot reach {self.name}: {exc.reason}", ""
            except json.JSONDecodeError as exc:
                # A non-JSON body is usually a block page. Read as "no results"
                # it turns a ban into a quiet day nobody investigates.
                raise SourceError(f"{self.name} returned non-JSON: {exc}") from exc
            if attempt < RETRIES - 1:
                self._sleep(BACKOFF * (attempt + 1))
        raise SourceError(f"{last} (after {RETRIES} attempts)", status=status)

    def _advert(self, *, source_id: str, url: str, company: str, role: str,
                location: str, engagement: str, rate: str, description: str,
                created: str, query: str, community: str) -> Signal | None:
        # Boards return names HTML-escaped ("Zone &#038; Co"); the marketplace
        # check and the headline both want the words.
        company, role, location = (html.unescape(v or "").strip()
                                   for v in (company, role, location))
        if (not source_id or not (role or description)
                or _is_marketplace(company) or _is_staffing_firm(company)
                or _is_gig_task(role)):
            return None
        headline = " | ".join(p for p in (company, role, location, engagement, rate) if p)
        body = _plain(description or "")
        return Signal(
            source_id=f"{self.name}:{source_id}",
            platform=self.platform,
            source_url=url,
            community=community,
            kind="post",
            author="",
            title=headline[:150],
            text=f"{headline}\n{body}".strip(),
            created_utc=created,
            query=query,
            mode="live",
            company=(company or "").strip() or "unknown",
            buyer_intent=(engagement or "").strip().lower() or "unknown",
        )


def _iso_from_epoch(value) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def _money(lo, hi, currency="", period="") -> str:
    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    lo, hi = num(lo), num(hi)
    if not lo and not hi:
        return ""
    if lo and hi and lo != hi:
        span = f"{lo:,}-{hi:,}"
    else:
        span = f"{lo or hi:,}"   # one figure, or the same figure twice
    return " ".join(p for p in (currency or "", span, f"/{period}" if period else "") if p).strip()


@register
class Himalayas(_JobBoard):
    name = "himalayas"
    platform = "himalayas"
    access = ("free public jobs API, no key; Himalayas asks for a link back to the "
              "listing and a credit, and forbids resubmitting its jobs to other job sites")
    terms_url = "https://himalayas.app/api"
    # Rate-limited without a published number, and "data refreshes daily, so
    # frequent polling offers no benefit" -- so slow, and few pages.
    pace = 2.0
    rate = "2.0s between requests, 3 backoff retries; data refreshes daily"
    default_queries = ("developer", "engineer", "automation", "data")
    SEARCH_URL = "https://himalayas.app/jobs/api/search"
    PAGE = 20  # the API's own maximum
    #: Himalayas' own top-level categories that are our kind of work. Keyword
    #: search alone matched "Content Developers" for a university; its category
    #: is Education, and the category is what says so.
    TECH = frozenset({"Developer", "Data Science", "DevOps", "IT", "Software Engineering"})

    def search(self, query: str, *, since_utc: str = "", limit: int = 100) -> Iterator[Signal]:
        del since_utc  # the API has no date filter; dedupe does the work
        page = 1
        # `limit` counts listing SLOTS asked for (page x 20), not kept: it
        # bounds the requests even when pages come back short.
        while (page - 1) * self.PAGE < limit:
            body = self._page(query, page)
            jobs = body.get("jobs") or []
            for job in jobs:
                sig = self._keep(job, query)
                if sig is not None:
                    yield sig
            if not jobs or self._last_page(body, page, len(jobs)):
                return
            page += 1

    def _last_page(self, body: dict, page: int, got: int) -> bool:
        """Himalayas returns SHORT pages mid-results (4 of 20 on page 1 of
        "engineer", totalCount 2258), so a short page is not the end when the
        total says otherwise. Without a total, a short page is the last."""
        total = body.get("totalCount")
        if isinstance(total, int):
            return page * self.PAGE >= total
        return got < self.PAGE

    def _page(self, query: str, page: int) -> dict:
        q = urllib.parse.urlencode({"q": query, "employment_type": "Contractor",
                                    "sort": "recent", "page": page})
        return self._get(f"{self.SEARCH_URL}?{q}")

    def _keep(self, job: dict, query: str) -> Signal | None:
        if not set(job.get("parentCategories") or []) & self.TECH:
            return None
        return self._to_signal(job, query)

    def _to_signal(self, job: dict, query: str) -> Signal | None:
        where = ", ".join(job.get("locationRestrictions") or []) or "Remote"
        return self._advert(
            source_id=str(job.get("guid") or "").rsplit("/", 1)[-1],
            url=job.get("guid") or job.get("applicationLink") or "",
            company=job.get("companyName") or "",
            role=job.get("title") or "",
            location=f"Remote ({where})" if where != "Remote" else where,
            engagement=job.get("employmentType") or "",
            rate=_money(job.get("minSalary"), job.get("maxSalary"),
                        job.get("currency") or "", job.get("salaryPeriod") or ""),
            description=job.get("description") or job.get("excerpt") or "",
            created=_iso_from_epoch(job.get("pubDate")),
            query=f"himalayas {query}",
            community="himalayas/contract",
        )


@register
class Remotive(_JobBoard):
    name = "remotive"
    platform = "remotive"
    access = ("free public remote-jobs API, no key; Remotive asks for a link back and "
              "a credit, at most 4 calls a day, and forbids resubmitting its jobs to "
              "other job sites")
    terms_url = "https://github.com/remotive-io/remote-jobs-api"
    # "More than 2x per minute will be blocked." One call per run anyway.
    pace = 31.0
    rate = "one request per run (asks: at most 2 a minute, 4 a day)"
    #: ONE request for the whole board, filtered here. A call per category
    #: would spend the four-a-day allowance in a single run.
    default_queries = ("all",)
    API = "https://remotive.com/api/remote-jobs"
    TECH = frozenset({"Software Development", "Data", "DevOps / Sysadmin", "QA",
                      "Data Analysis", "Product"})

    def search(self, query: str, *, since_utc: str = "", limit: int = 100) -> Iterator[Signal]:
        # One request for the whole board: there is nothing to search by, so
        # the shared signature's query and window are both unused here.
        del query, since_utc
        body = self._get(self.API)
        seen = 0
        for job in body.get("jobs") or []:
            if job.get("category") not in self.TECH:
                continue
            if not _contract_shaped(job.get("job_type")):
                continue
            sig = self._to_signal(job)
            if sig is None:
                continue
            yield sig
            seen += 1
            if seen >= limit:
                return

    def _to_signal(self, job: dict) -> Signal | None:
        return self._advert(
            source_id=str(job.get("id") or ""),
            url=job.get("url") or "",
            company=job.get("company_name") or "",
            role=job.get("title") or "",
            location=f"Remote ({job['candidate_required_location']})"
                     if job.get("candidate_required_location") else "Remote",
            engagement=(job.get("job_type") or "").replace("_", "-"),
            rate=job.get("salary") or "",
            description=job.get("description") or "",
            created=job.get("publication_date") or "",
            query="remotive all",
            community="remotive/jobs",
        )


@register
class Jobicy(_JobBoard):
    name = "jobicy"
    platform = "jobicy"
    access = ("free public remote-jobs API, no key; Jobicy permits commercial use "
              "for tools and AI products, asks that the listing URL and a credit "
              "are kept, and automated checks at most hourly")
    terms_url = "https://jobicy.com/jobs-rss-feed"
    pace = 2.0
    rate = "2.0s between requests; asks: automated checks at most hourly"
    default_queries = ("engineering", "data-science")
    API = "https://jobicy.com/api/v2/remote-jobs"

    def search(self, query: str, *, since_utc: str = "", limit: int = 100) -> Iterator[Signal]:
        del since_utc
        q = urllib.parse.urlencode({"count": min(max(limit, 1), 100), "industry": query})
        body = self._get(f"{self.API}?{q}")
        seen = 0
        for job in body.get("jobs") or []:
            sig = self._to_signal(job, query)
            if sig is None:
                continue
            yield sig
            seen += 1
            if seen >= limit:
                return

    def _to_signal(self, job: dict, query: str) -> Signal | None:
        kinds = job.get("jobType") or []
        if isinstance(kinds, str):
            kinds = [kinds]
        if not any(_contract_shaped(k) for k in kinds):
            return None
        return self._advert(
            source_id=str(job.get("id") or ""),
            url=job.get("url") or "",
            company=job.get("companyName") or "",
            role=job.get("jobTitle") or "",
            location=f"Remote ({job['jobGeo']})" if job.get("jobGeo") else "Remote",
            engagement=", ".join(k for k in kinds if k),
            rate=_money(job.get("salaryMin"), job.get("salaryMax"),
                        job.get("salaryCurrency") or "", job.get("salaryPeriod") or ""),
            description=job.get("jobDescription") or job.get("jobExcerpt") or "",
            created=job.get("pubDate") or "",
            query=f"jobicy {query}",
            community="jobicy/jobs",
        )


def names() -> List[str]:
    return [Himalayas.name, Remotive.name, Jobicy.name]
