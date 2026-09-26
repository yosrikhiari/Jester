"""In Reddit's hiring rooms, most posts are the wrong side of the deal.

MEASURED on the archive: 11 [for hire] + 1 [forhire] + 1 [available] against
7 [hiring]. Sellers outnumber clients nearly two to one, so a classifier that
cannot tell them apart reports mostly noise -- and this one could not. It
scored a freelancer's advert 1.00 buyer:

    [FOR HIRE] I'm a Web Developer - WordPress, WooCommerce, Shopify - $20/hr

Owner voice, named work, a stated price. Every feature the scorer looks at
said buyer. The only thing separating that post from a real lead is which
side of the deal the "I" is on, and that lives in the tag.

Three rules, in the order they fire. Two are structural (the tag, the
question mark) because structure does not depend on guessing which words a
job-seeker happens to use; one is lexical (seller_voice) for untagged posts,
and is written to catch self-description rather than "looking for", which is
exactly what a client writes.
"""
from jester.signals.filters import classify, load_rules
from jester.signals.from_archive import (NOT_AN_ADVERT, SELLER_TAGS,
                                         leading_tag)

RULES = load_rules("config/hiring_rules.yaml")

SELLER = ("[FOR HIRE] I'm a Web Developer - WordPress, WooCommerce, "
          "Shopify - $20/hr. Contract or hourly, remote.")
CLIENT = ("[Hiring] Senior Full-Stack Engineer ($100-$180/hr) - AI Dataset & "
          "App Building (Next.js / Redis / SQL) - high-paying remote contract")


# ---- the tag decides, and it is positional ---------------------------------

def test_the_seller_tag_is_recognised_however_it_is_written():
    for raw in ("[For Hire] dev", "[FOR HIRE] dev", "[forhire] dev",
                "(for hire) dev", "  [For-Hire] dev"):
        assert leading_tag(raw) in SELLER_TAGS, raw


def test_a_client_advert_keeps_its_tag():
    assert leading_tag(CLIENT) == "hiring"
    assert leading_tag(CLIENT) not in SELLER_TAGS


def test_discussion_posts_are_not_adverts():
    assert leading_tag("[Discussion] - devops engineer") in NOT_AN_ADVERT


def test_the_tag_must_lead_not_merely_appear():
    """"We are not for hire" is prose, not a declaration. Only a post that
    OPENS with the tag is making the statement the room enforces."""
    assert leading_tag("We are not for hire, we are hiring") == ""


# ---- untagged sellers, which the tag rule cannot reach ----------------------

def test_an_untagged_seller_is_no_longer_a_buyer():
    """The post that scored 1.00 on the old rules."""
    seller = ("Eight years building embedded ML, finally putting myself out "
              "there. I've spent eight years doing full-stack and IoT work. "
              "I'm now looking for edge AI and TinyML contract roles, remote.")
    assert classify(seller, RULES).audience != "buyer"


def test_the_real_advert_is_untouched():
    """The whole point. A rule that also removes clients is not a fix."""
    v = classify(CLIENT, RULES)
    assert v.audience == "buyer"
    assert v.confidence >= 0.9


def test_a_client_saying_looking_for_is_not_a_seller():
    """seller_voice must not fire on "looking for", which is precisely what a
    company writes. This is the regression the phrase list is shaped around."""
    v = classify("We are looking for a Python developer, contract, $90/hr, "
                 "remote. Our team ships weekly.", RULES)
    assert v.audience == "buyer"


def test_seekers_in_the_hn_format_are_rejected_too():
    """HN's "Who wants to be hired?" format. Fifteen of these were filed as
    practitioners until seller_voice landed."""
    seeker = ("Location: Lahore, Pakistan Remote: Yes Willing to relocate: "
              "Yes Technologies: Python, React. I'm looking for work.")
    assert classify(seeker, RULES).audience == "none"


# ---- the HN seeker template ------------------------------------------------

def test_the_hn_who_wants_to_be_hired_template_is_not_a_buyer():
    """HN runs "Who is hiring?" and "Who wants to be hired?" in the same
    collector, and the second reads as a buyer: it names the work, the stack
    and a location. Three of 153 hn/hiring buyers were candidates filling in
    this template."""
    seeker = ("Location: Chicago, Illinois, US Remote: Yes Willing to "
              "relocate: No Technologies: Python, Go, Kubernetes, AWS. "
              "Contract or full-time, $90/hr.")
    assert classify(seeker, RULES).audience != "buyer"


def test_an_advert_offering_relocation_can_still_be_a_buyer():
    """Weighted, not hard-rejected. A company willing to relocate the right
    person is a real advert and must survive on its other evidence -- which is
    why "willing to relocate" is a -0.65 signal and not a veto."""
    advert = ("Acme Corp | Senior Backend Engineer | REMOTE | contract, "
              "$120-160/hr | We are looking for a Python engineer to build "
              "internal tooling and data pipelines. We are willing to "
              "relocate the right candidate. Start ASAP. Apply: jobs@acme.com")
    assert classify(advert, RULES).audience == "buyer"
