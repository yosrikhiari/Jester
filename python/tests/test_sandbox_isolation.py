"""The docker sandbox must not share the host's vector space.

compose says the stack "gets its own throwaway archive and never touches the
host's". That was true of SQLite — separate volume — and false of vectors.

`collection_for` namespaces by the database file's STEM, so every database
called jester.db anywhere maps to `nuggets__jester`. The sandbox's throwaway
archive was also called jester.db, so it pointed at the host's collection:
105,471 points at 768 dimensions from real runs, while the sandbox produces
32-dimension fake vectors. The console said "collection is 768-dim but fake
produces 32", which was true and was the only reason nothing was written —
incompatible vectors are rejected. Make the dimensions agree and the sandbox
would have written straight into the production dedup space.

That is the failure `collection_for`'s own docstring describes: "running a
cycle against a throwaway --db wrote 240 vectors straight into the production
dedup space, where they stayed and influenced dedup for real runs". The
isolation was by filename, and the sandbox picked the same filename.

Renaming the sandbox database is the fix, so these tests guard the name.
"""
import re
from pathlib import Path

import pytest

from jester.cli import collection_for

REPO = Path(__file__).resolve().parents[2]
CONSOLE_DOCKERFILE = REPO / "docker" / "Dockerfile.python"
WORKER_DOCKERFILE = REPO / "docker" / "go.Dockerfile"


def _db_paths(dockerfile: Path):
    """Every --db / -db argument the image launches with."""
    text = dockerfile.read_text(encoding="utf-8")
    return re.findall(r'-?-db"?,?\s+"?([^"\s\\]+\.db)', text)


def test_the_namespacing_is_by_filename_and_therefore_collides():
    """Stated as a test because it is the trap, not an accident. Two unrelated
    databases with the same name share one collection, and nothing warns."""
    assert collection_for("data/jester.db") == collection_for("/app/data/jester.db")
    assert collection_for("/tmp/scratch/jester.db") == collection_for("data/jester.db")


@pytest.mark.parametrize("dockerfile", [CONSOLE_DOCKERFILE, WORKER_DOCKERFILE],
                         ids=["console", "go-worker"])
def test_the_sandbox_image_does_not_use_the_default_database_name(dockerfile):
    paths = _db_paths(dockerfile)
    assert paths, f"no --db argument found in {dockerfile.name}"
    for path in paths:
        assert Path(path).name != "jester.db", (
            f"{dockerfile.name} launches with {path}. That is the host's "
            f"database name, so this container shares the host's Qdrant "
            f"collection and the isolation compose promises is not real."
        )


@pytest.mark.parametrize("dockerfile", [CONSOLE_DOCKERFILE, WORKER_DOCKERFILE],
                         ids=["console", "go-worker"])
def test_the_sandbox_gets_its_own_collection(dockerfile):
    """The property that actually matters. The name is a means to it."""
    for path in _db_paths(dockerfile):
        assert collection_for(path) != collection_for("data/jester.db"), (
            f"{path} resolves to {collection_for(path)}, the same collection "
            f"as the host archive"
        )


def test_both_sandbox_images_share_one_database_with_each_other():
    """They must be isolated from the HOST, not from each other: the handoff
    queue between the console and the worker is the cross-process contract,
    and it only works if both open the same file."""
    console = _db_paths(CONSOLE_DOCKERFILE)
    worker = _db_paths(WORKER_DOCKERFILE)
    assert console and worker
    assert set(console) == set(worker), (
        f"console uses {console} and the worker uses {worker}; the handoff "
        f"queue needs one database"
    )
