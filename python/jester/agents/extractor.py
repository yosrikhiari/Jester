"""M1.4 extractor agent: turns raw comments into candidate nuggets."""
import datetime
from typing import List, Optional

from jester.config import Thresholds
from jester.llm import ALLOWED_CATEGORIES, ExtractorLLM, FakeLLM
from jester.models import Nugget


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
        cid = c.get("id") or c.get("fingerprint") or c.get("comment_id")
        draft = llm.extract(body)
        category = draft.category if draft.category in ALLOWED_CATEGORIES else "pain_point"
        nuggets.append(
            Nugget(
                unique_key=f"{platform}:{thread_id}:{cid}",
                platform=platform,
                source_url=source_url,
                thread_id=thread_id,
                raw_text=body,
                extracted_insight=draft.extracted_insight or body[:200],
                category=category,
                engagement_score=float(c.get("score", c.get("upvotes", 0)) or 0),
                timestamp=now,
                synthesized_at="",  # R41/R50: NULL until a synthesizer claims this nugget
                needs_reembed=False,
                run_id=run_id,
            )
        )
    return nuggets
