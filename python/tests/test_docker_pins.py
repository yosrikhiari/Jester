"""Docker image pinning (F-07 remainder): the SHAPE of the pin, as text.

No Docker daemon, no registry. The digests themselves were resolved once by
hand through the Docker Hub registry HTTP API (token -> manifest, verified
against the Docker-Content-Digest header, 2026-10-29) — that job is done
outside the repo and then it is Dependabot's. What this guards is the shape
that keeps the pin meaningful: every image reference in the compose file
names a tag and carries a digest (or, if it was ever resolved that way, an
explicit pending note on that line), no compose image is untagged or bare
`:latest`, and every Dockerfile base image names an explicit version. This
is exactly the shape the dependabot weekly schedule can act on — an untagged
reference would silently stop being updated at all.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"
PYTHON_DOCKERFILE = REPO / "docker" / "Dockerfile.python"

DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


def _compose_image_values():
    out = []
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("image:"):
            continue
        value = line.split("image:", 1)[1].strip()
        reference, _, note = value.partition("#")
        out.append((reference.strip(), note.strip()))
    return out


def test_every_compose_image_is_tagged_and_digested():
    images = _compose_image_values()
    assert images, "no image: lines found — docker-compose.yml changed shape?"
    for reference, note in images:
        # repo:tag@sha256:... is the reference Dependabot understands and the
        # one docker pull resolves byte-exactly. A repo@sha256:... WITHOUT a
        # tag is legal compose and invisible to Dependabot — so the tag is
        # asserted independently of the digest.
        tagged, _, digest = reference.partition("@")
        if digest:
            assert DIGEST.match(digest), f"digest not sha256:<hex>: {reference}"
            assert ":" in tagged and tagged.rsplit(":", 1)[1], \
                f"digest-pinned but no runnable tag: {reference}"
        else:
            # The registry was reachable at pin time; the only sanctioned
            # state without a digest is an explicit pending note on the line.
            assert "digest pending" in note.lower(), \
                f"no digest and no pending note: {reference}"


def test_no_compose_image_is_bare_latest_or_untagged():
    for reference, _ in _compose_image_values():
        before_digest = reference.partition("@")[0]
        assert before_digest.rsplit(":", 1)[0] != before_digest, \
            f"untagged image: {reference}"
        # A bare-`latest` line is one WITHOUT a digest; :latest@sha256:...
        # is pinned and passes the digest test above.


def test_dockerfile_python_bases_are_versioned_not_floating():
    text = PYTHON_DOCKERFILE.read_text(encoding="utf-8")
    bases = re.findall(r"^\s*FROM\s+(\S+)", text, re.I | re.M)
    assert bases, "no FROM lines found — Dockerfile.python changed shape?"
    for base in bases:
        assert "latest" not in base, f"floating base image: {base}"
        tagged, _, digest = base.partition("@")
        assert ":" in tagged and tagged.rsplit(":", 1)[1], f"untagged base: {base}"
        assert any(c.isdigit() for c in tagged.rsplit(":", 1)[1]), \
            f"base tag carries no version: {tagged}"
        # Both stages are digest-pinned like the compose services; a rebuild
        # must not change bytes nobody decided on.
        assert DIGEST.match(digest), f"base not digest-pinned: {base}"
