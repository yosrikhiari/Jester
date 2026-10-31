"""M1.4 extractor agent: turns raw comments into candidate nuggets."""
import datetime
from typing import List, Optional

from jester.config import Thresholds
from jester.llm import ALLOWED_CATEGORIES, ExtractorLLM, FakeLLM
from jester.models import Nugget

#: Fields the Go adapters publish under a comment's `detail` key, mapped onto
#: the Nugget attribute of the same meaning. One list, so a field added to the
#: capture side reaches the archive by being named here once.
_DETAIL_FIELDS = (
    ("author", "author"),
    ("author_url", "author_url"),
    ("permalink", "comment_url"),
    ("platform_id", "comment_id"),
    ("parent_id", "parent_id"),
    ("depth", "depth"),
    ("created_at", "created_utc"),
    ("created_raw", "created_raw"),
    ("upvotes", "upvotes"),
    ("downvotes", "downvotes"),
    ("likes", "likes"),
    ("dislikes", "dislikes"),
    ("replies", "replies"),
    ("awards", "awards"),
    ("reads", "reads"),
    ("edited", "edited"),
    ("pinned", "pinned"),
    ("author_is_op", "author_is_op"),
    ("distinguished", "distinguished"),
    ("accepted_answer", "accepted_answer"),
)

#: Same, for the thread/video/topic envelope stored in ingest_batch.thread_meta.
_POST_FIELDS = (
    ("title", "post_title"),
    ("url", "post_url"),
    ("author", "post_author"),
    ("created_at", "post_created_utc"),
    ("score", "post_score"),
    ("upvote_ratio", "post_upvote_ratio"),
    ("comment_count", "post_comment_count"),
    ("views", "post_views"),
    ("community", "community"),
)


#: Boolean observations, as opposed to published counts. The adapters omit a
#: false flag to keep the payload small, so an absent one inside a detail block
#: that EXISTS means "checked, not set" — false — while a missing detail block
#: entirely means nobody looked.
_FLAG_ATTRS = (
    "edited",
    "pinned",
    "author_is_op",
    "distinguished",
    "accepted_answer",
)


def _apply(nugget: Nugget, src: dict, fields) -> None:
    """Copy published fields across, and ONLY published ones.

    A key the adapter omitted is left at the dataclass default (None / "")
    rather than written as 0 or False: the whole point of the omission is that
    the platform did not publish the figure, and overwriting that with a
    concrete value invents data (R29).
    """
    if not isinstance(src, dict) or not src:
        return
    for key, attr in fields:
        if key in src and src[key] is not None:
            setattr(nugget, attr, src[key])


def _seed_flags(nugget: Nugget, detail: dict) -> None:
    """Turn the flags from None ("nobody looked") into False ("looked, not
    set") once we know a detail block was actually captured."""
    if not isinstance(detail, dict) or not detail:
        return
    for attr in _FLAG_ATTRS:
        if getattr(nugget, attr) is None:
            setattr(nugget, attr, False)


def _to_nugget(c: dict, draft, *, platform: str, thread_id: str,
               source_url: str, run_id: str, now: str) -> Nugget:
    """Build one archive row from a comment and its draft. Shared by the
    single and batch paths so the two cannot disagree on provenance."""
    body = (c.get("body") or c.get("text") or "").strip()
    cid = c.get("id") or c.get("fingerprint") or c.get("comment_id")
    category = draft.category if draft.category in ALLOWED_CATEGORIES else "pain_point"
    nugget = Nugget(
        unique_key=f"{platform}:{thread_id}:{cid}",
        platform=platform,
        source_url=source_url,
        thread_id=thread_id,
        raw_text=body,
        extracted_insight=draft.extracted_insight or body[:200],
        category=category,
        engagement_score=float(c.get("score", c.get("upvotes", 0)) or 0),
        # Provenance travels WITH the row. A run-level count of fallbacks
        # cannot say which of 9,000 nuggets was one of them.
        extractor_model=draft.model,
        timestamp=now,
        synthesized_at="",  # R41/R50: NULL until a synthesizer claims this nugget
        needs_reembed=False,
        run_id=run_id,
    )
    # Everything the scraper captured beyond the body: who said it, when,
    # where to read it, and how it was received. Absent for a comment that
    # predates the widened capture, which is why every field is optional.
    detail = c.get("detail") or {}
    _apply(nugget, detail, _DETAIL_FIELDS)
    _apply(nugget, c.get("post") or {}, _POST_FIELDS)
    _seed_flags(nugget, detail)
    extra = detail.get("extra")
    if isinstance(extra, dict) and extra:
        nugget.extra = extra
    # A comment permalink is more precise than the thread URL a reviewer
    # was previously sent to; keep the thread URL when there is no
    # per-comment link to improve on it.
    if nugget.comment_url:
        nugget.source_url = nugget.comment_url
    return nugget


def extract(
    comments: List[dict],
    llm: ExtractorLLM,
    *,
    platform: str,
    thread_id: str,
    source_url: str,
    run_id: str,
) -> List[Nugget]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    nuggets: List[Nugget] = []
    for c in comments:
        body = (c.get("body") or c.get("text") or "").strip()
        if not body:
            continue
        draft = llm.extract(body)
        nuggets.append(_to_nugget(c, draft, platform=platform,
                                  thread_id=thread_id, source_url=source_url,
                                  run_id=run_id, now=now))
    return nuggets


def extract_batch(
    comments: List[dict],
    llm: ExtractorLLM,
    *,
    platform: str,
    thread_id: str,
    source_url: str,
    run_id: str,
    batch_size: int = 5,
) -> List[Nugget]:
    """Batched extraction (F-05): one model call per `batch_size` comments.

    Empty bodies are skipped exactly as in extract(), so the two paths yield
    the same rows for the same input — the batch path just spends fewer
    requests. `batch_size <= 1` is extract() spelled differently.
    """
    batch_size = max(1, int(batch_size or 1))
    kept = [c for c in comments
            if ((c.get("body") or c.get("text") or "").strip())]
    if batch_size == 1:
        return extract(kept, llm, platform=platform, thread_id=thread_id,
                       source_url=source_url, run_id=run_id)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    batch_fn = getattr(llm, "extract_batch", None)
    nuggets: List[Nugget] = []
    for i in range(0, len(kept), batch_size):
        chunk = kept[i:i + batch_size]
        bodies = [(c.get("body") or c.get("text") or "").strip() for c in chunk]
        drafts = None
        if callable(batch_fn):
            try:
                drafts = batch_fn(bodies)
            except Exception:  # noqa: BLE001 - a batch call must never lose comments
                drafts = None
        if drafts is None or len(drafts) != len(chunk):
            # Never misattribute: a failed or short reply degrades to
            # per-comment calls rather than zipping drafts onto the wrong
            # comments.
            drafts = [llm.extract(b) for b in bodies]
        for c, draft in zip(chunk, drafts):
            nuggets.append(_to_nugget(c, draft, platform=platform,
                                      thread_id=thread_id,
                                      source_url=source_url, run_id=run_id,
                                      now=now))
    return nuggets
