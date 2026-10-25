// Beginner glossary: plain-English definitions for open-source jargon, and a remark plugin
// that marks each term's first mention in AI-written Markdown (GlossaryTerm shows them).

export const GLOSSARY = {
    pr: ['Pull request (PR)', 'Your proposed change, sent to the project for review. Maintainers comment, you update it, and they merge it.'],
    fork: ['Fork', 'Your own copy of a project on GitHub. You push your changes there, then open a pull request from it.'],
    upstream: ['Upstream', "The original project you forked from. You fetch its latest changes to keep your copy current."],
    rebase: ['Rebase', 'Replaying your commits on top of the latest version of the main branch, as if you had started from it. It rewrites your branch, so you push with --force-with-lease afterwards.'],
    'force-with-lease': ['--force-with-lease', 'A safe force-push: it updates your PR branch after a rebase or amend, but refuses if someone else pushed to it meanwhile.'],
    squash: ['Squash', 'Combining several commits into one, often requested before merging.'],
    'merge conflict': ['Merge conflict', 'Git could not combine two changes to the same lines automatically. You choose the right version and remove the <<<<<<< ======= >>>>>>> markers.'],
    diff: ['Diff', 'The exact lines you added (+) and removed (−) compared with the main branch.'],
    ci: ['CI (continuous integration)', 'Automatic checks (tests, linters, builds) that run on every pull request. A red ✗ means one of them failed.'],
    dco: ['DCO (Developer Certificate of Origin)', "A statement that you're allowed to contribute your code. You agree by adding a 'Signed-off-by' line to each commit with git commit -s."],
    'signed-off-by': ['Signed-off-by', 'A line at the end of a commit message that agrees to the DCO. git commit -s adds it for you.'],
    cla: ['CLA (Contributor License Agreement)', 'A legal agreement some projects ask you to sign once, usually by clicking a link a bot posts on your PR.'],
    maintainer: ['Maintainer', 'Someone who runs the project: reviews pull requests, merges them and decides its direction.'],
    nit: ['Nit', 'A small, optional suggestion ("nitpick"), like naming or style. Usually not blocking, but nice to fix.'],
    lgtm: ['LGTM', '"Looks good to me": a reviewer approves your change.'],
    linter: ['Linter', 'A tool that checks code for style problems and likely bugs, e.g. ruff, eslint or flake8.'],
    formatter: ['Formatter', 'A tool that rewrites code into the project\'s standard layout, e.g. black or prettier. Run it rather than fixing spacing by hand.'],
    'test suite': ['Test suite', "The project's automated tests. Run them before and after your change."],
    changelog: ['Changelog', 'A file listing what changed in each release. Some projects want an entry from every PR.'],
    changeset: ['Changeset', 'A small file describing your change for the release notes, created with npx changeset.'],
    'conventional commits': ['Conventional Commits', 'A commit message format like "fix: handle empty config" or "feat(cli): add --json".'],
    'good first issue': ['Good first issue', 'A label maintainers put on issues they think suit newcomers.'],
    triage: ['Triage', 'Maintainers sorting new issues: confirming bugs, labelling, deciding priority. "Needs triage" means nobody has looked yet.'],
    'stale bot': ['Stale bot', 'A bot that closes issues or PRs after a period of inactivity. Reply to reviews promptly so yours stays open.'],
    lockfile: ['Lockfile', 'A generated file pinning exact dependency versions (package-lock.json, uv.lock…). Only commit changes to it if you changed dependencies.'],
    'draft pr': ['Draft PR', 'A pull request marked as not ready yet. Good for early feedback.'],
    flaky: ['Flaky test', "A test that sometimes fails for reasons unrelated to your change. Ask a maintainer to re-run it rather than 'fixing' it."],
    codeowners: ['CODEOWNERS', 'A file naming who must review changes to which parts of the repo.'],
};

// Phrase in text -> glossary key. Longer phrases first so "merge conflict" wins over shorter ones.
const PATTERNS = [
    ['pull requests?|\\bPRs?\\b', 'pr'],
    ['force-with-lease', 'force-with-lease'],
    ['merge conflicts?', 'merge conflict'],
    ['conventional commits', 'conventional commits'],
    ['good first issues?', 'good first issue'],
    ['stale ?bot', 'stale bot'],
    ['test suite', 'test suite'],
    ['draft PRs?', 'draft pr'],
    ['signed-off-by', 'signed-off-by'],
    ['flaky', 'flaky'],
    ['forks?|forked|forking', 'fork'],
    ['upstream', 'upstream'],
    ['rebas(?:e|ed|ing)', 'rebase'],
    ['squash(?:ed|ing)?', 'squash'],
    ['\\bCI\\b', 'ci'],
    ['\\bDCO\\b', 'dco'],
    ['\\bCLA\\b', 'cla'],
    ['maintainers?', 'maintainer'],
    ['\\bnits?\\b', 'nit'],
    ['\\bLGTM\\b', 'lgtm'],
    ['linters?|linting', 'linter'],
    ['formatters?', 'formatter'],
    ['changelogs?', 'changelog'],
    ['changesets?', 'changeset'],
    ['triage', 'triage'],
    ['lockfiles?', 'lockfile'],
    ['CODEOWNERS', 'codeowners'],
    ['\\bdiffs?\\b', 'diff'],
];
const TERM_RE = new RegExp(PATTERNS.map(([p]) => `(${p.startsWith('\\b') ? p : `\\b(?:${p})\\b`})`).join('|'), 'gi');
const SKIP = new Set(['link', 'linkReference', 'heading', 'inlineCode', 'code', 'glossaryTerm']);

function keyFor(match) {
    for (const [pattern, key] of PATTERNS) {
        if (new RegExp(`^(?:${pattern})$`, 'i').test(match)) return key;
    }
    return null;
}

function splitText(value, seen) {
    const nodes = [];
    let last = 0;
    for (const m of value.matchAll(TERM_RE)) {
        const key = keyFor(m[0]);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        if (m.index > last) nodes.push({ type: 'text', value: value.slice(last, m.index) });
        nodes.push({
            type: 'glossaryTerm',
            data: { hName: 'span', hProperties: { className: ['glossary-term'], dataTerm: key },
                hChildren: [{ type: 'text', value: m[0] }] },
        });
        last = m.index + m[0].length;
    }
    if (!nodes.length) return null;
    if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) });
    return nodes;
}

function walk(node, seen) {
    if (!node.children || SKIP.has(node.type)) return;
    node.children = node.children.flatMap(child => {
        if (child.type === 'text') return splitText(child.value, seen) || [child];
        walk(child, seen);
        return [child];
    });
}

// remark plugin: explain each glossary term once per document (the first time it appears).
export function remarkGlossary() {
    return tree => walk(tree, new Set());
}
