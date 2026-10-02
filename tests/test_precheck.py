"""Pure checks used by the pre-PR checklist."""

import precheck_pr as pp


def test_commit_message_checks():
    assert pp.check_commit_messages(["fix: handle null (#3)"], conventional=True)["status"] == "pass"
    res = pp.check_commit_messages(["WIP stuff", "x" * 80], conventional=False)
    assert res["status"] == "warn"
    assert "WIP" in res["detail"] and "72" in res["detail"]
    assert pp.check_commit_messages(["Update README"], conventional=True)["status"] == "warn"


def test_signoff_check():
    signed = "fix: x\n\nSigned-off-by: Dev <dev@example.com>"
    assert pp.check_signoff([signed], required=True)["status"] == "pass"
    assert pp.check_signoff(["fix: x"], required=True)["status"] == "fail"
    assert pp.check_signoff(["fix: x"], required=False)["status"] == "skip"


def test_issue_link_check():
    assert pp.check_issue_link("Fixes #12", [], required=True)["status"] == "pass"
    assert pp.check_issue_link(None, ["fix: x (#4)"], required=True)["status"] == "pass"
    assert pp.check_issue_link("No link here", [], required=True)["status"] == "fail"
    assert pp.check_issue_link("No link here", [], required=False)["status"] == "warn"


def test_pr_body_check():
    assert pp.check_pr_body(None, has_template=True)["status"] == "skip"
    filled = "## What\nFixes the crash on empty config.\n\n## Testing\nRan pytest, all green.\n"
    assert pp.check_pr_body(filled, True)["status"] == "pass"
    unfilled = "<!-- describe -->\n## What\n\n## Testing\n- [ ] tests pass\nFixes #\n"
    res = pp.check_pr_body(unfilled, True)
    assert res["status"] == "warn"
    for fragment in ("comments", "no issue number", "empty section", "unchecked"):
        assert fragment in res["detail"]


def test_diff_size_and_shortstat():
    assert pp.parse_shortstat(" 3 files changed, 10 insertions(+), 2 deletions(-)") == (3, 10, 2)
    assert pp.parse_shortstat(" 1 file changed, 1 insertion(+)") == (1, 1, 0)
    assert pp.check_diff_size(0, 0, 0)["status"] == "fail"
    assert pp.check_diff_size(2, 30, 5)["status"] == "pass"
    assert pp.check_diff_size(10, 500, 100)["status"] == "warn"
    assert pp.check_diff_size(50, 2000, 100)["status"] == "fail"
