"""The /api/post/ route, exercised over real HTTP.

api.post() is unit-tested elsewhere. This covers the part that only exists
in the server: pulling a thread id out of a URL path, deciding whether it
could name a real post, and choosing a status code. Sonar flagged the first
version of this route as a reflected-XSS taint flow -- request text reaching
a response body -- so the boundary is worth testing at the boundary.
"""
import json
import threading
import urllib.error
import urllib.request
from argparse import Namespace
from http.server import ThreadingHTTPServer

import pytest

from jester.console.server import Handler
from jester.models import Nugget
from jester.store import insert_nugget, open_db


@pytest.fixture
def base(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    insert_nugget(db, Nugget(
        unique_key="a", platform="reddit", thread_id="1wpoevs",
        category="pain_point", extracted_insight="deploys take all afternoon",
        run_id="r", community="r/devops", post_title="Our deploys are slow",
        post_comment_count=4,
    ))
    db.close()
    Handler.args = Namespace(db=str(tmp_path / "j.db"), config="config",
                             signals_db=None, read_only=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_a_real_post_comes_back_with_its_comments(base):
    code, body = _get(base + "/api/post/1wpoevs")
    assert code == 200
    assert body["metrics"]["title"] == "Our deploys are slow"
    assert len(body["comments"]) == 1


def test_a_missing_post_is_404_not_500(base):
    code, _ = _get(base + "/api/post/doesnotexist")
    assert code == 404


def test_an_id_that_cannot_exist_is_refused_as_400(base):
    """Refused at the boundary. 400 rather than 404 because the request was
    malformed, not merely unlucky."""
    code, _ = _get(base + "/api/post/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert code == 400


def test_the_refusal_does_not_repeat_the_input(base):
    """The regression Sonar caught: request text must not reach the response
    body, whatever the Content-Type says."""
    _, body = _get(base + "/api/post/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert "script" not in json.dumps(body)


def test_the_posts_list_groups_by_thread(base):
    code, body = _get(base + "/api/posts?limit=10")
    assert code == 200
    assert body["total"] == 1
    assert body["posts"][0]["held"] == 1
