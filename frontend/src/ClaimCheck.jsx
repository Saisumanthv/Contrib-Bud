import CopyButton from './CopyButton';

const ICONS = { closed: '🔒', taken: '🛑', ask_first: '✋', clear: '✅' };

// "Is it safe to claim this?": overlapping work found on GitHub, questions to ask first,
// and the comment to post (a check-in when someone may already be on it).
export default function ClaimCheck({ check, comment }) {
    return (
        <div className="detail-item">
            <span className="label">Before you claim</span>
            {check?.verdict ? (
                <div className={`claim-check claim-${check.verdict}`}>
                    <p className="claim-verdict">
                        <span className="claim-badge">{ICONS[check.verdict]} {check.label}</span>
                        <span>{check.advice}</span>
                    </p>
                    <ul className="claim-findings">
                        {check.findings.map(finding => <li key={finding}>{finding}</li>)}
                    </ul>
                    {check.overlapping_prs?.length > 0 && (
                        <ul className="claim-prs">
                            {check.overlapping_prs.map(pr => (
                                <li key={pr.number}>
                                    <a href={pr.url} target="_blank" rel="noopener noreferrer">
                                        #{pr.number} {pr.title}
                                    </a>
                                    {pr.draft && <span className="tag">draft</span>}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            ) : check?.error && (
                <p className="similar-empty">Couldn't check for overlapping work: {check.error}</p>
            )}

            {comment && (
                <>
                    <div className="claim-comment-head">
                        <span className="label">
                            {check?.verdict === 'taken' ? 'Comment to check in first' : 'Comment to post on the issue'}
                        </span>
                        <CopyButton text={comment} />
                    </div>
                    <pre className="claim-comment">{comment}</pre>
                    <p className="similar-empty">
                        Post it yourself, only if the project expects claiming or you need answers first.
                        Replace any &lt;placeholder&gt; before posting.
                    </p>
                </>
            )}
        </div>
    );
}
