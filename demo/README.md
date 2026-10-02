# Demo

`demo.sh` walks through the full flow against a real beginner-friendly repo
(default: `mermaid-js/mermaid`, override with `DEMO_REPO=owner/repo`):

1. `buddy analyze` explains setup, tests and contribution rules
2. `buddy issues` finds unclaimed beginner issues and ranks them
3. `buddy plan` writes a copy-pasteable plan for the top issue

```bash
export GEMINI_API_KEY=...        # or BUDDY_PROVIDER=ollama
export GITHUB_TOKEN=...          # optional, avoids the 60 req/hour limit
bash demo/demo.sh                # add --no-ai to run without a model
```

## Sample output

- [`sample-output.txt`](sample-output.txt): terminal output of the three commands, recorded on 2026-10-03 with `--no-ai`. It shows only the facts and heuristics layer. With a model configured, each command also prints an "AI summary" section with cited explanations.
- [`sample-plan.md`](sample-plan.md): the generated plan for [mermaid-js/mermaid#4121](https://github.com/mermaid-js/mermaid/issues/4121) (template "Approach"; with a model it becomes issue-specific).

Issue data changes daily, so your results will differ.
