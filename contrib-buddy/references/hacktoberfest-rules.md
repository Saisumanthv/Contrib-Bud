# Hacktoberfest & spam-PR avoidance

> Rules change every year. Anything here may be out of date — **verify on hacktoberfest.com** before giving the user definitive answers.

## What changed in 2026 (checked 2026-10-03 on hacktoberfest.com)

- "Pull requests and merge requests will no longer count toward Hacktoberfest rewards." (hacktoberfest.com FAQ)
- Rewards come from other activities instead (MyMLH sign-up, in-person Fests, online challenges, livestreams). Details: verify on hacktoberfest.com.
- The organizers still "encourage you to work on open source" during October; the change exists to reduce "the burden of low-effort contributions on maintainers".

**Implication for the agent:** never frame a PR as "counting" toward anything. The only reason to open a PR is that it genuinely helps the project.

## Labels you may still see

- `hacktoberfest` (label or repo topic) — historically signalled that maintainers welcome October contributions. Treat it as a "beginner-friendly" hint, nothing more.
- `hacktoberfest-accepted` — historically marked a PR as valid for the event. Whether it still has any meaning in 2026: verify on hacktoberfest.com.

## What maintainers consider spam (applies all year)

- Whitespace, typo-only, or "added my name to README" PRs to repos that did not ask for them.
- Auto-generated PRs (including AI-generated) the author has not run, tested, or understood.
- PRs that ignore the PR template, CONTRIBUTING rules, or an issue's existing assignee.
- Duplicate PRs for an issue someone else is already working on.
- Large unrequested refactors or reformatting.

## What a valid, welcome PR looks like

- Linked to an open issue the maintainers agreed should be fixed (or a clear bug you reproduced).
- Small and focused: one logical change, tests updated or added.
- Passes the project's lint and tests locally before opening.
- Follows the PR template and commit conventions; includes DCO sign-off / CLA if required.
- Written and understood by the human submitting it. If AI helped, the human has reviewed every line and can explain it.
