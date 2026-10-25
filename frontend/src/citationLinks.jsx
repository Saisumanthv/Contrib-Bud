// Citation markers for chat answers. The backend checks each [path:12-40] / [#12] source
// (see buddy/citations.py); here every one becomes a link with its result.
export const STATUS = {
    verified: { icon: '✓', label: 'Verified' },
    unsupported: { icon: '?', label: "Couldn't confirm" },
    not_found: { icon: '✗', label: 'Not found' },
    unchecked: { icon: '', label: 'Not checked' },
};
const PREFIX = '#cite-';

function escapeMarkdown(text) {
    return text.replace(/[\\[\]*_`]/g, ch => `\\${ch}`);
}

// Turn each checked citation into a Markdown link "[src/app.py:3](#cite-0)" so the
// renderer below can draw it. Offsets come from the backend in UTF-16 units, like JS strings.
export function markCitations(content, citations) {
    if (!citations?.length) return content;
    let out = content;
    citations.map((c, i) => [c, i]).reverse().forEach(([c, i]) => {
        if (out.slice(c.start, c.end) !== c.raw) return; // content changed: leave it as text
        out = `${out.slice(0, c.start)}[${escapeMarkdown(c.raw.slice(1, -1))}](${PREFIX}${i})${out.slice(c.end)}`;
    });
    return out;
}

export function refUrl(ref, repo, branch) {
    if (ref.kind === 'folder') return `https://github.com/${repo}/tree/${branch}/${ref.path}`;
    if (ref.kind === 'file') {
        const lines = ref.start ? `#L${ref.start}${ref.end && ref.end !== ref.start ? `-L${ref.end}` : ''}` : '';
        return `https://github.com/${repo}/blob/${branch}/${ref.path}${lines}`;
    }
    if (ref.kind === 'issue') return `https://github.com/${repo}/issues/${ref.number}`;
    return null;
}

function tooltip(citation) {
    return citation.refs
        .map(r => `${r.text}: ${STATUS[r.status].label}${r.reason ? `, ${r.reason}` : ''}`)
        .join('\n');
}

// react-markdown `components` for one message: citation links become checked chips.
export function citationComponents(msg) {
    return {
        a({ href, children }) {
            const citation = href?.startsWith(PREFIX) ? msg.citations?.[Number(href.slice(PREFIX.length))] : null;
            if (!citation) {
                return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
            }
            const { icon, label } = STATUS[citation.status];
            const url = refUrl(citation.refs[0], msg.repo, msg.branch);
            return (
                <a
                    className={`cite cite-${citation.status}`}
                    href={url || undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={tooltip(citation)}
                    aria-label={`${citation.raw.slice(1, -1)}: ${label}`}
                >
                    {children}
                    {icon && <span className="cite-mark" aria-hidden="true">{icon}</span>}
                </a>
            );
        },
    };
}
