import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { API_URL } from './api';
import CitationReport from './CitationReport';
import { citationComponents, markCitations } from './citationLinks';
import CopyButton from './CopyButton';
import { markdownProps } from './markdown';

const PR_LINK = /^(https?:\/\/)?(www\.)?github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+\/?([?#].*)?$/i;
const KIND_LABEL = { review: 'Review', ci: 'CI', merge: 'Merge', other: 'Other' };

// Markdown with checked citation chips, linking into the PR's own code.
function Cited({ text, citations, repo, branch }) {
    const msg = { citations, repo, branch };
    return (
        <div className="markdown-body">
            <ReactMarkdown {...markdownProps(citationComponents(msg))}>{markCitations(text, citations)}</ReactMarkdown>
        </div>
    );
}

function Commands({ commands }) {
    if (!commands?.length) return null;
    const text = commands.join('\n');
    return (
        <div className="coach-commands">
            <pre><code>{text}</code></pre>
            <CopyButton text={text} />
        </div>
    );
}

function Guidance({ items }) {
    return (
        <div className="coach-items">
            {items.map(g => (
                <article key={`${g.kind}-${g.title}`} className="coach-item">
                    <header>
                        <span className={`coach-kind kind-${g.kind}`}>{g.kind.replace('_', ' ')}</span>
                        <h4>{g.title}</h4>
                    </header>
                    <p>{g.explanation}</p>
                    {g.where?.length > 0 && <p className="coach-where">Where: {g.where.join(', ').replaceAll('`', '')}</p>}
                    <Commands commands={g.commands} />
                    <p className="coach-source">From {g.source}</p>
                </article>
            ))}
        </div>
    );
}

function Threads({ threads, repo }) {
    if (!threads?.length) return null;
    return (
        <details className="coach-threads">
            <summary>What reviewers wrote ({threads.length} thread{threads.length === 1 ? '' : 's'})</summary>
            {threads.map(t => (
                <div key={t.url} className="coach-thread">
                    <a href={t.url} target="_blank" rel="noopener noreferrer">
                        <code>{t.path}{t.line ? `:${t.line}` : ''}</code>
                    </a>
                    {t.outdated && <span className="tag">outdated</span>}
                    {t.waiting_on_you && !t.outdated && <span className="tag">waiting on you</span>}
                    {t.comments.map(c => (
                        <blockquote key={c.at + c.author}>
                            <strong>@{c.author}</strong>
                            <ReactMarkdown>{c.body}</ReactMarkdown>
                        </blockquote>
                    ))}
                </div>
            ))}
            <p className="similar-empty">Reply in these threads on GitHub yourself{repo ? ` (${repo})` : ''}.</p>
        </details>
    );
}

// "My PR got feedback, now what?" Paste review comments or a CI log, or give the PR link.
// The coach explains what's wanted and suggests fixes; the user makes them and replies.
export default function PRCoach({ repo, initialPrUrl = '', autoRun = false }) {
    const [prUrl, setPrUrl] = useState(initialPrUrl);
    const [text, setText] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState(null);
    const request = useRef(null);

    const prLinkOk = !prUrl.trim() || PR_LINK.test(prUrl.trim());
    const canRun = !loading && prLinkOk && (prUrl.trim() || text.trim());

    const coach = async (url = prUrl, pasted = text) => {
        request.current?.abort();
        const controller = new AbortController();
        request.current = controller;
        setLoading(true);
        setError('');
        try {
            const response = await fetch(`${API_URL}/api/coach`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pr_url: url.trim(), text: pasted, repo, ai: true }),
                signal: controller.signal,
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'The coach could not read that.');
            setResult(data);
        } catch (err) {
            if (!controller.signal.aborted) setError(err.message);
        } finally {
            if (request.current === controller) setLoading(false);
        }
    };

    useEffect(() => {
        if (autoRun && initialPrUrl) coach(initialPrUrl, '');
        return () => request.current?.abort();
        // Run once for the link the page was opened with.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const submit = e => {
        e.preventDefault();
        if (canRun) coach();
    };

    const pr = result?.facts?.pr;
    const ai = result?.ai;
    const links = { repo: result?.code_repo, branch: result?.ref };

    return (
        <div className="coach">
            <form className="coach-form" onSubmit={submit}>
                <label className="label" htmlFor="coach-pr">Pull request link (optional)</label>
                <input
                    id="coach-pr"
                    type="text"
                    className="coach-input"
                    placeholder="https://github.com/owner/repo/pull/123"
                    value={prUrl}
                    onChange={e => setPrUrl(e.target.value)}
                    aria-invalid={!prLinkOk}
                />
                {!prLinkOk && <p className="link-hint">That doesn't look like a pull request link (…/pull/123).</p>}
                <label className="label" htmlFor="coach-text">Review comments or CI log (optional)</label>
                <textarea
                    id="coach-text"
                    className="coach-input coach-textarea"
                    placeholder="Paste a reviewer's comments, or the output of a failing check…"
                    value={text}
                    onChange={e => setText(e.target.value)}
                    rows={7}
                />
                <div className="coach-actions">
                    <button type="submit" className="search-button" disabled={!canRun}>
                        {loading ? 'Reading…' : 'Explain and suggest fixes'}
                    </button>
                    <span className="similar-empty">Advice only: nothing is posted or pushed for you.</span>
                </div>
            </form>

            {error && (
                <div className="error-box">
                    <span className="error-icon">⚠️</span>
                    <p>{error}</p>
                </div>
            )}
            {loading && <p className="issues-hint pulsing">Reading the reviews, checks and code…</p>}

            {result && !loading && (
                <div className="coach-result">
                    {pr && (
                        <p className="coach-pr">
                            <a href={pr.url} target="_blank" rel="noopener noreferrer">#{pr.number} {pr.title}</a>
                            <span className="tag">{pr.merged ? 'merged' : pr.state}</span>
                            {pr.mergeable_state === 'dirty' && <span className="tag">merge conflicts</span>}
                        </p>
                    )}
                    {result.facts?.logs_need_token && (
                        <p className="similar-empty">
                            GitHub only shares failed job logs with a token: set GITHUB_TOKEN to include them,
                            or paste the log above.
                        </p>
                    )}
                    {result.ai_error && <p className="similar-empty">⚠️ {result.ai_error}</p>}

                    {ai ? (
                        <>
                            <div className="ai-summary">
                                <h3>What to do</h3>
                                <p>{ai.summary}</p>
                            </div>
                            <div className="coach-items">
                                {ai.items.map((item, i) => (
                                    <article key={i} className="coach-item">
                                        <header>
                                            <span className={`coach-kind kind-${item.kind}`}>{KIND_LABEL[item.kind]}</span>
                                            <h4>{item.title}</h4>
                                        </header>
                                        <span className="label">What they want</span>
                                        <Cited text={item.what} citations={item.citations.what} {...links} />
                                        <span className="label">How to fix it</span>
                                        <Cited text={item.fix} citations={item.citations.fix} {...links} />
                                        <Commands commands={item.commands} />
                                        <CitationReport msg={{ citations: [...item.citations.what, ...item.citations.fix], ...links }} />
                                    </article>
                                ))}
                            </div>
                            {ai.reply && (
                                <div className="detail-item">
                                    <div className="claim-comment-head">
                                        <span className="label">Reply draft (post it yourself after pushing your fixes)</span>
                                        <CopyButton text={ai.reply} />
                                    </div>
                                    <pre className="claim-comment">{ai.reply}</pre>
                                </div>
                            )}
                            {result.guidance?.length > 0 && (
                                <details className="coach-threads">
                                    <summary>Rule-based checks ({result.guidance.length})</summary>
                                    <Guidance items={result.guidance} />
                                </details>
                            )}
                        </>
                    ) : result.guidance?.length > 0 ? (
                        <Guidance items={result.guidance} />
                    ) : (
                        <p className="similar-empty">
                            Nothing recognisable was found. Paste the full review comment or the log of the failing step.
                        </p>
                    )}
                    <Threads threads={result.facts?.threads} repo={result.repo} />
                </div>
            )}
        </div>
    );
}
