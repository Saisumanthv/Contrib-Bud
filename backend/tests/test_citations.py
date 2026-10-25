"""Citation checking: parsing sources, finding claims and matching them to the repo."""

from buddy import citations as cit
from buddy import rag

APP = """from flask import Flask

app = Flask(__name__)


@app.route("/health")
def health():
    return {"status": "ok"}


def create_user(name, email):
    user = User(name=name, email=email)
    db.session.add(user)
    db.session.commit()
    return user
"""
INDEX = rag.RepoIndex.build("o/r", "main", [
    ("src/app.py", APP),
    ("README.md", "Run the tests with pytest. Install with pip install -e .\n"),
    (".github/workflows/ci.yml", "jobs:\n  test:\n    runs-on: ubuntu-latest\n"),
])


def check(reply, known=None):
    return cit.verify(reply, INDEX, known)


def test_parse_ref_kinds():
    assert cit.parse_ref("src/app.py:12-40") == {"kind": "file", "text": "src/app.py:12-40",
                                                 "path": "src/app.py", "start": 12, "end": 40}
    assert cit.parse_ref("app.py:L5–L9")["start"] == 5
    assert cit.parse_ref("README.md")["end"] is None
    assert cit.parse_ref("Makefile")["kind"] == "file"
    assert cit.parse_ref("#123") == {"kind": "issue", "text": "#123", "number": 123}
    assert cit.parse_ref("comment by @Alice")["author"] == "Alice"
    for not_a_source in ("x", "optional", "1.2", "GitHub API", "TODO"):
        assert cit.parse_ref(not_a_source) is None, not_a_source


def test_matching_claim_is_verified():
    (c,) = check("The `/health` route returns a status of ok [src/app.py:6-8].")
    assert c["status"] == "verified" and c["refs"][0]["reason"] == ""


def test_claim_about_other_lines_is_unsupported_with_a_suggestion():
    (c,) = check("Users are saved to the database session and committed [src/app.py:1-3].")
    ref = c["refs"][0]
    assert c["status"] == "unsupported"
    assert ref["suggested"]["start"] <= 13 <= ref["suggested"]["end"]


def test_missing_quoted_code_is_unsupported():
    (c,) = check("It calls `delete_user()` to clean up [src/app.py:11-16].")
    assert c["status"] == "unsupported" and "delete_user()" in c["refs"][0]["reason"]


def test_quoted_code_block_is_checked():
    good = "Here is how users are created in [src/app.py:11-16]:\n\n```python\n" \
           "user = User(name=name, email=email)\ndb.session.add(user)\n```\n"
    bad = good.replace("db.session.add(user)", "cache.put(user)\nlog(user)")
    assert check(good)[0]["status"] == "verified"
    assert check(bad)[0]["status"] == "unsupported"


def test_missing_files_and_lines_are_not_found():
    (c,) = check("Settings are loaded in [src/settings.py:1-5].")
    assert c["status"] == "not_found" and "no file" in c["refs"][0]["reason"]
    (c,) = check("The app is created here [src/app.py:400-410].")
    assert c["status"] == "not_found" and "15 lines" in c["refs"][0]["reason"]


def test_paths_resolve_by_unique_suffix_and_dot_folders():
    (c,) = check("A Flask app is created [app.py:3].")
    assert c["status"] == "verified" and c["refs"][0]["path"] == "src/app.py"
    (c,) = check("Tests run on ubuntu-latest [.github/workflows/ci.yml].")
    assert c["status"] == "verified"


def test_location_only_claim_and_trailing_citation():
    (c,) = check("It lives in [src/app.py:11-16].")
    assert c["status"] == "verified"
    reply = "Run the tests with pytest. [README.md]"
    (c,) = check(reply)  # a citation alone after a sentence backs that sentence
    assert c["status"] == "verified" and "pytest" in c["claim"]


def test_multiple_sources_take_the_worst_status():
    (c,) = check("Install with pip and test with pytest [README.md, src/missing.py].")
    assert c["status"] == "not_found"
    assert [r["status"] for r in c["refs"]] == ["verified", "not_found"]


def test_links_checkboxes_and_code_are_ignored():
    reply = ("See [the docs](https://x.y/README.md). - [x] done\n"
             "```\narr[src/app.py:1]\n```\nAnd `[README.md]` inline.")
    assert check(reply) == []


def test_issue_and_comment_refs_use_known_facts():
    known = cit.Known(issue_numbers={7, 40}, comment_authors={"Maint"})
    reply = "Asked in [#7], fixed like [#40] and [#999]; see [comment by @maint] and [comment by @bob]."
    statuses = [c["status"] for c in check(reply, known)]
    assert statuses == ["verified", "verified", "not_found", "verified", "not_found"]
    assert check("Mentioned in [#7].")[0]["status"] == "unchecked"  # repo chat: nothing to check


def test_without_an_index_files_are_unchecked():
    (c,) = cit.verify("Created in [src/app.py:3].", None)
    assert c["status"] == "unchecked"


def test_offsets_are_utf16_for_the_page():
    reply = "🚀 Health check [src/app.py:6-8]"
    (c,) = check(reply)
    utf16 = reply.encode("utf-16-le")
    assert utf16[c["start"] * 2:c["end"] * 2].decode("utf-16-le") == "[src/app.py:6-8]"


def test_summary_counts_statuses():
    found = check("Health route [src/app.py:6-8]. Settings in [nope.py].")
    assert cit.summary(found) == {"verified": 1, "unchecked": 0, "unsupported": 0, "not_found": 1}


def test_citation_after_a_code_block_checks_that_code():
    reply = ("The code looks like this:\n```python\ndb.session.add(user)\ndb.session.commit()\n```\n"
             "[src/app.py:13-14]")
    (c,) = check(reply)
    assert c["status"] == "verified" and "looks like this" in c["claim"]
    assert check(reply.replace("db.session.commit()", "db.flush()\ndb.close()"))[0]["status"] == "unsupported"


def test_quoted_file_names_are_not_treated_as_code():
    (c,) = check("The health route is defined in `src/app.py` [src/app.py:6-8].")
    assert c["status"] == "verified"


def test_each_citation_backs_its_own_clause():
    reply = "Users go through `create_user` [src/app.py:11-15] and the health route returns ok [src/app.py:6-8]."
    first, second = check(reply)
    assert first["claim"] == "Users go through `create_user`"
    assert second["claim"] == "and the health route returns ok"
    assert first["status"] == second["status"] == "verified"


def test_commands_derived_from_a_file_count_as_its_content():
    reply = "Run the linter with `ruff check .` [README.md]."
    assert check(reply)[0]["status"] == "unsupported"
    known = cit.Known.from_stack({"lint_commands": [{"command": "ruff check .", "source": "README.md"}]})
    assert check(reply, known)[0]["status"] == "verified"
