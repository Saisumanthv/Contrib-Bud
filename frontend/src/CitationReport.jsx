import { STATUS, refUrl } from './citationLinks';

// "3 citations verified", or which ones couldn't be and why.
export default function CitationReport({ msg }) {
    const citations = msg.citations || [];
    if (!citations.length) return null;
    const flagged = citations.filter(c => c.status === 'unsupported' || c.status === 'not_found');
    const verified = citations.filter(c => c.status === 'verified').length;
    const unchecked = citations.length - verified - flagged.length;
    const parts = [`${verified} of ${citations.length} citation${citations.length === 1 ? '' : 's'} verified`];
    if (flagged.length) parts.push(`${flagged.length} flagged`);
    if (unchecked) parts.push(`${unchecked} not checked`);

    if (!flagged.length) {
        return <p className={`citation-report ${verified ? 'all-verified' : ''}`}>{verified ? '✓ ' : ''}{parts.join(' · ')}</p>;
    }
    return (
        <details className="citation-report has-flags">
            <summary>⚠ {parts.join(' · ')}</summary>
            <ul>
                {flagged.flatMap(c => c.refs.filter(r => r.status !== 'verified').map(r => {
                    const url = refUrl(r, msg.repo, msg.branch);
                    const better = r.suggested && refUrl({ ...r, ...r.suggested }, msg.repo, msg.branch);
                    return (
                        <li key={`${c.start}-${r.text}`}>
                            <span className={`cite cite-${r.status}`}>
                                {url ? <a href={url} target="_blank" rel="noopener noreferrer">{r.text}</a> : r.text}
                                <span className="cite-mark" aria-hidden="true">{STATUS[r.status].icon}</span>
                            </span>{' '}
                            {STATUS[r.status].label}: {r.reason}
                            {better && (
                                <> (<a href={better} target="_blank" rel="noopener noreferrer">open lines {r.suggested.start}-{r.suggested.end}</a>)</>
                            )}
                            {c.claim && <q className="citation-claim">{c.claim}</q>}
                        </li>
                    );
                }))}
            </ul>
        </details>
    );
}
