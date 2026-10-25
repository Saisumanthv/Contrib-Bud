const FILES_SHOWN = 6;

// Issues like this one that were already fixed, and what the merged PRs that fixed them looked like.
export default function SimilarPRs({ similar }) {
    if (!similar) return null;
    const examples = similar.examples || [];
    return (
        <div className="detail-item">
            <span className="label">Learn from similar merged PRs</span>
            {similar.error ? (
                <p className="similar-empty">Couldn't look up similar PRs: {similar.error}</p>
            ) : examples.length === 0 ? (
                <p className="similar-empty">
                    No closed issue like this one with a merged fix was found. Browse the repo's recently
                    merged PRs to get a feel for its style.
                </p>
            ) : (
                <div className="similar-list">
                    {examples.map(({ issue, pr, why }) => (
                        <article key={pr.number} className="similar-card">
                            <a className="similar-issue" href={issue.url} target="_blank" rel="noopener noreferrer">
                                #{issue.number} {issue.title}
                            </a>
                            {why && <span className="similar-why">{why}</span>}
                            <div className="similar-pr">
                                Fixed by{' '}
                                <a href={pr.url} target="_blank" rel="noopener noreferrer">PR #{pr.number}</a>
                                {pr.author && <> by @{pr.author}</>}
                                <span className="similar-size">
                                    <span className="added">+{pr.additions}</span>{' '}
                                    <span className="removed">−{pr.deletions}</span>{' '}
                                    in {pr.changed_files} file{pr.changed_files === 1 ? '' : 's'}
                                </span>
                            </div>
                            <p className="similar-review">
                                {pr.review_story.charAt(0).toUpperCase() + pr.review_story.slice(1)}.
                                {pr.large && ' This one is large, so don\'t use it as a size guide for a first PR.'}
                            </p>
                            <ul className="similar-files">
                                {pr.files.slice(0, FILES_SHOWN).map(file => (
                                    <li key={file.path}><code>{file.path}</code></li>
                                ))}
                                {pr.changed_files > FILES_SHOWN && (
                                    <li className="similar-more">+{pr.changed_files - FILES_SHOWN} more</li>
                                )}
                            </ul>
                        </article>
                    ))}
                </div>
            )}
        </div>
    );
}
