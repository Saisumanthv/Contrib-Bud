import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import './App.css';
import { API_URL, fetchTree, githubFileUrl } from './api';
import HealthBadge from './HealthBadge';
import SimilarPRs from './SimilarPRs';
import ThemePicker from './ThemePicker';

const REPO_LINK = /^(https?:\/\/)?(www\.)?github\.com\/[\w.-]+\/[\w.-]+\.git\/?$/i;
const ISSUE_LINK = /^(https?:\/\/)?(www\.)?github\.com\/[\w.-]+\/[\w.-]+\/issues\/\d+\/?([?#].*)?$/i;

// 'repo' for a repo link ending in .git, 'issue' for an issue link, otherwise null.
function linkKind(text) {
    const link = text.trim();
    if (REPO_LINK.test(link)) return 'repo';
    if (ISSUE_LINK.test(link)) return 'issue';
    return null;
}

function TreeNodes({ node, fileUrl }) {
    return (
        <ul className="tree-list">
            {Object.keys(node.folders).sort().map(name => (
                <TreeFolder key={name} name={name} node={node.folders[name]} fileUrl={fileUrl} />
            ))}
            {node.files.map(file => (
                <li key={file.path} className="tree-file">
                    <a href={fileUrl(file.path)} target="_blank" rel="noopener noreferrer">📄 {file.name}</a>
                </li>
            ))}
        </ul>
    );
}

function TreeFolder({ name, node, fileUrl }) {
    const [open, setOpen] = useState(false);
    return (
        <li className="tree-folder">
            <button type="button" className="tree-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
                <span className="tree-arrow">{open ? '▾' : '▸'}</span> 📁 {name}
            </button>
            {/* Children render only when open, so large repos stay fast. */}
            {open && <TreeNodes node={node} fileUrl={fileUrl} />}
        </li>
    );
}

export default function App() {
    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);
    const [issues, setIssues] = useState(null);
    const [error, setError] = useState('');
    const [repoView, setRepoView] = useState(null);
    // Once any analysis has started, keep the two-column layout so content never jumps sideways.
    const [started, setStarted] = useState(false);
    const [summaryRetrying, setSummaryRetrying] = useState(false);
    const request = useRef(null); // AbortController of the analysis in progress
    const [activeTab, setActiveTab] = useState('issues');
    const [tree, setTree] = useState(null);
    const [treeLoading, setTreeLoading] = useState(false);
    const [treeError, setTreeError] = useState('');

    // One conversation per repo and per issue, kept for the browser session.
    const [chatInput, setChatInput] = useState('');
    const [chats, setChats] = useState(() => {
        try {
            return JSON.parse(sessionStorage.getItem('chats')) || {};
        } catch {
            return {};
        }
    });
    const [chatPending, setChatPending] = useState(null); // key of the chat awaiting a reply
    // Search index status per repo: { state: 'loading' | 'ready' | 'error', stats?, error? }
    const [indexes, setIndexes] = useState({});

    useEffect(() => {
        try {
            sessionStorage.setItem('chats', JSON.stringify(chats));
        } catch {
            // Storage full or blocked: the chat still works, it just won't survive a reload.
        }
    }, [chats]);

    // What the chat panel is about: the issue on a plan page, otherwise the analyzed repo.
    const chat = data?.type === 'plan'
        ? { mode: 'issue', key: `issue:${data.content.issue.url}`, repo: data.content.repo,
            issueUrl: data.content.issue.url, number: data.content.issue.number }
        : data?.type === 'analyze'
            ? { mode: 'repo', key: `repo:${data.content.facts.repo}`, repo: data.content.facts.repo }
            : null;
    const messages = (chat && chats[chat.key]) || [];
    const chatLoading = chat !== null && chatPending === chat.key;
    const index = chat ? indexes[chat.repo] : null;

    // Build a repo's search index in the background when its page first loads, so the first
    // chat question doesn't wait for the download. Once per repo per visit.
    const indexRequested = useRef(new Set());
    const ensureIndex = repo => {
        if (indexRequested.current.has(repo)) return;
        indexRequested.current.add(repo);
        setIndexes(prev => ({ ...prev, [repo]: { state: 'loading' } }));
        fetch(`${API_URL}/api/index`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo })
        })
            .then(async response => {
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Indexing failed');
                setIndexes(prev => ({ ...prev, [repo]: { state: 'ready', stats: result } }));
            })
            .catch(err => {
                indexRequested.current.delete(repo); // let a later visit try again
                setIndexes(prev => ({ ...prev, [repo]: { state: 'error', error: err.message } }));
            });
    };

    const handleChatSubmit = async (e) => {
        e.preventDefault();
        const question = chatInput.trim();
        if (!question || chatPending || !chat || loading) return;

        const { key } = chat;
        const append = message => setChats(prev => ({ ...prev, [key]: [...(prev[key] || []), message] }));
        const history = [...messages, { role: 'User', content: question }];
        append({ role: 'User', content: question });
        setChatInput('');
        setChatPending(key);

        try {
            const response = await fetch(`${API_URL}/api/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    mode: chat.mode, repo: chat.repo, issue_url: chat.issueUrl, question, history
                })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Chat failed');
            append({
                role: 'Assistant', content: result.reply, sources: result.sources,
                repo: result.repo, branch: result.branch
            });
        } catch (err) {
            append({ role: 'System', content: `Error: ${err.message}` });
        } finally {
            setChatPending(null);
        }
    };

    const clearChat = () => setChats(prev => {
        const next = { ...prev };
        delete next[chat.key];
        return next;
    });

    // Loaders fetch and return a function that shows the result; run() only calls it if the
    // request wasn't cancelled in the meantime.
    const loadRepo = async (repo, signal) => {
        const [analyzeRes, issuesRes] = await Promise.all([
            fetch(`${API_URL}/api/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo, ai: true }),
                signal
            }),
            fetch(`${API_URL}/api/issues`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo, ai: true }),
                signal
            })
        ]);

        const analyzeResult = await analyzeRes.json();
        const issuesResult = await issuesRes.json();

        if (!analyzeRes.ok) throw new Error(analyzeResult.error || 'Failed to analyze repository');
        if (!issuesRes.ok) throw new Error(issuesResult.error || 'Failed to fetch issues');

        return () => {
            setData({ type: 'analyze', content: analyzeResult });
            ensureIndex(analyzeResult.facts.repo);
            setIssues(issuesResult);
        };
    };

    const loadPlan = async (issueUrl, signal) => {
        const response = await fetch(`${API_URL}/api/plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ issue_url: issueUrl, ai: true }),
            signal
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to generate plan');
        return () => {
            setData({ type: 'plan', content: result });
            ensureIndex(result.repo);
        };
    };

    const run = async (load) => {
        request.current?.abort();
        const controller = new AbortController();
        request.current = controller;

        setStarted(true);
        setLoading(true);
        setError('');
        setData(null);
        setIssues(null);
        setActiveTab('issues');
        setTree(null);
        setTreeError('');

        try {
            const show = await load(controller.signal);
            if (!controller.signal.aborted) show();
        } catch (err) {
            if (!controller.signal.aborted) setError(err.message);
        } finally {
            if (request.current === controller) {
                request.current = null;
                setLoading(false);
            }
        }
    };

    // Back while loading: stop the request and return to the page the user came from.
    const cancelLoading = () => {
        request.current?.abort();
        request.current = null;
        setLoading(false);
        setError('');
        if (repoView) {
            setQuery(repoView.query);
            setData(repoView.data);
            setIssues(repoView.issues);
            setActiveTab(repoView.activeTab);
            setTree(repoView.tree);
            setRepoView(null);
        } else {
            // Nothing to go back to but the start page.
            setQuery('');
            setData(null);
            setIssues(null);
            setStarted(false);
        }
    };

    const kind = linkKind(query);

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!kind) return;

        const link = query.trim();
        setRepoView(null);
        run(signal => (kind === 'issue' ? loadPlan(link, signal) : loadRepo(link, signal)));
    };

    const handleIssueClick = (issueUrl) => {
        // Remember the repo page so the back button can restore it without refetching.
        setRepoView({ query, data, issues, activeTab, tree });
        setQuery(issueUrl);
        run(signal => loadPlan(issueUrl, signal));
    };

    // Ask for the AI summary again (GitHub data is cached, so this only re-runs the model).
    const retrySummary = async () => {
        const repo = data.content.facts.repo;
        const update = fields => setData(d => (d?.type === 'analyze' && d.content.facts.repo === repo
            ? { ...d, content: { ...d.content, ...fields } }
            : d));

        setSummaryRetrying(true);
        try {
            const response = await fetch(`${API_URL}/api/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo, ai: true })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Failed to analyze repository');
            update({ ai_summary: result.ai_summary, ai_error: result.ai_error });
        } catch (err) {
            update({ ai_error: err.message });
        } finally {
            setSummaryRetrying(false);
        }
    };

    const handleTreeTab = async () => {
        setActiveTab('tree');
        if (tree || treeLoading) return;

        setTreeLoading(true);
        setTreeError('');
        try {
            const facts = data.content.facts;
            setTree(await fetchTree(facts.repo, facts.default_branch));
        } catch (err) {
            setTreeError(err.message);
        } finally {
            setTreeLoading(false);
        }
    };

    const handleBack = () => {
        if (repoView) {
            setQuery(repoView.query);
            setData(repoView.data);
            setIssues(repoView.issues);
            setActiveTab(repoView.activeTab);
            setTree(repoView.tree);
            setError('');
            setRepoView(null);
            return;
        }
        // Opened from a pasted issue link: there is no saved repo page, so load it.
        const repo = data?.content?.repo;
        if (!repo) return;
        setQuery(`https://github.com/${repo}.git`);
        run(signal => loadRepo(repo, signal));
    };

    const chatReady = Boolean(data) && !loading;

    return (
        <div className={`container ${started ? 'with-chat' : ''}`}>
            <nav className="navbar">
                <ThemePicker />
            </nav>
            <header className="header animate-in" style={{ animationDelay: '0.1s' }}>
                <div className="logo-container">
                    <h1>My First PR</h1>
                </div>
                <p>Your AI Mentor for Open Source Contributions</p>
            </header>

            <main>
                <div className="glass search-box animate-in" style={{ animationDelay: '0.2s' }}>
                    <form onSubmit={handleSubmit} className="search-form">
                        <input
                            type="text"
                            placeholder="Paste a GitHub repo link (e.g. https://github.com/facebook/react.git)"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            className="search-input"
                            aria-invalid={query.trim() !== '' && !kind}
                            aria-describedby="link-hint"
                        />
                        <button type="submit" disabled={loading || !kind} className="search-button">
                            {loading ? 'Working...' : 'Analyze'}
                        </button>
                    </form>
                </div>
                {query.trim() !== '' && !kind && (
                    <p id="link-hint" className="link-hint">
                        Enter a GitHub repo link ending in <code>.git</code>, like <code>https://github.com/facebook/react.git</code>, or a link to one of its issues.
                    </p>
                )}

                {error && (
                    <div className="glass error-box animate-in" style={{ animationDelay: '0.1s' }}>
                        <span className="error-icon">⚠️</span>
                        <p>{error}</p>
                    </div>
                )}
                {error && repoView && (
                    <button type="button" className="back-button" onClick={handleBack}>
                        ← Back to {repoView.data?.content?.facts?.repo}
                    </button>
                )}

                {loading && (
                    <button type="button" className="back-button" onClick={cancelLoading}>
                        ← Back{repoView ? ` to ${repoView.data?.content?.facts?.repo}` : ''}
                    </button>
                )}
                {loading && (
                    <div className="loading-state pulsing animate-in" style={{ animationDelay: '0.1s' }}>
                        <div className="spinner"></div>
                        <p>Our AI is reading docs, fetching issues, and drafting plans...</p>
                    </div>
                )}

                {data && !loading && (
                    <div className="glass result-box animate-in" style={{ animationDelay: '0.1s' }}>
                        {data.type === 'analyze' ? (
                            <div>
                                <h2>{data.content.facts.repo}</h2>
                                {data.content.facts.description && (
                                    <p className="subtitle">{data.content.facts.description}</p>
                                )}

                                <div className="stats-row">
                                    <div className="stat">
                                        <span className="stat-label">Stars</span>
                                        <span className="stat-value">★ {data.content.facts.stars || '?'}</span>
                                    </div>
                                    <div className="stat">
                                        <span className="stat-label">License</span>
                                        <span className="stat-value">{data.content.facts.license || 'None'}</span>
                                    </div>
                                    <div className="stat">
                                        <span className="stat-label">Default Branch</span>
                                        <span className="stat-value">{data.content.facts.default_branch || 'main'}</span>
                                    </div>
                                </div>

                                <HealthBadge health={data.content.facts.health} />

                                <div className="ai-summary">
                                    <h3>AI Contribution Summary</h3>
                                    {data.content.ai_summary ? (
                                        <div className="markdown-body">
                                            <ReactMarkdown>{data.content.ai_summary}</ReactMarkdown>
                                        </div>
                                    ) : (
                                        <div className="summary-missing">
                                            <p>
                                                ⚠️ The AI summary couldn't be generated this time
                                                {data.content.ai_error ? `: ${data.content.ai_error}` : '.'}
                                            </p>
                                            <button
                                                type="button"
                                                className="search-button"
                                                onClick={retrySummary}
                                                disabled={summaryRetrying}
                                            >
                                                {summaryRetrying ? 'Retrying...' : 'Retry'}
                                            </button>
                                        </div>
                                    )}
                                </div>

                                <div className="tabs" role="tablist">
                                    <button
                                        type="button"
                                        role="tab"
                                        aria-selected={activeTab === 'issues'}
                                        className={`tab ${activeTab === 'issues' ? 'active' : ''}`}
                                        onClick={() => setActiveTab('issues')}
                                    >
                                        🎯 Issues <span className="tab-count">{issues?.issues?.length ?? 0}</span>
                                    </button>
                                    <button
                                        type="button"
                                        role="tab"
                                        aria-selected={activeTab === 'tree'}
                                        className={`tab ${activeTab === 'tree' ? 'active' : ''}`}
                                        onClick={handleTreeTab}
                                    >
                                        🌳 Tree <span className="tab-count">{data.content.facts.file_count ?? 0}</span>
                                    </button>
                                </div>

                                {activeTab === 'tree' && (
                                    <div className="tree-section">
                                        {treeLoading && <p className="issues-hint">Loading files...</p>}
                                        {treeError && <p className="issues-hint">⚠️ {treeError}</p>}
                                        {tree && (
                                            <>
                                                <div className="tree-header">
                                                    <p className="issues-hint">
                                                        {tree.paths.length} files on <code>{tree.branch}</code>. Click a folder to expand it, or a file to view it on GitHub.
                                                        {tree.truncated && ' This repository is very large, so GitHub returned only part of the tree.'}
                                                    </p>
                                                    <a
                                                        className="search-button flowchart-link"
                                                        href={`?${new URLSearchParams({ flowchart: tree.repo, branch: tree.branch })}`}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                    >
                                                        🔀 Open flow chart ↗
                                                    </a>
                                                </div>
                                                <div className="file-tree">
                                                    <TreeNodes node={tree.root} fileUrl={path => githubFileUrl(tree, path)} />
                                                </div>
                                            </>
                                        )}
                                    </div>
                                )}

                                {activeTab === 'issues' && issues && issues.issues && issues.issues.length > 0 && (
                                    <div className="issues-section">
                                        <p className="issues-hint">Click on any issue below to automatically generate a step-by-step contribution plan!</p>
                                        <div className="issues-list">
                                            {issues.issues.map(issue => (
                                                <div
                                                    key={issue.number}
                                                    className={`issue-card ${issue.number === issues.recommendation?.number ? 'recommended' : ''}`}
                                                    onClick={() => handleIssueClick(issue.url)}
                                                >
                                                    {issue.number === issues.recommendation?.number && (
                                                        <div className="recommended-badge">✨ Recommended for you</div>
                                                    )}
                                                    <h4>#{issue.number} {issue.title}</h4>
                                                    <div className="issue-tags">
                                                        {(issue.labels || []).map(label => (
                                                            <span key={label} className="tag">{label}</span>
                                                        ))}
                                                    </div>
                                                    <p className="issue-desc">{issue.ai?.explanation || issue.heuristic?.reasons?.[0]}</p>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                                {activeTab === 'issues' && issues && (!issues.issues || issues.issues.length === 0) && (
                                    <div className="issues-section">
                                        <p className="issues-hint">No beginner-friendly issues were found in this repository right now. Try checking their issues tab directly!</p>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div>
                                {(repoView || data.content.repo) && (
                                    <button type="button" className="back-button" onClick={handleBack}>
                                        ← Back to {repoView?.data?.content?.facts?.repo || data.content.repo}
                                    </button>
                                )}
                                <h2>Plan for Issue #{data.content.issue?.number}</h2>
                                {data.content.issue?.title && (
                                    <p className="subtitle">{data.content.issue.title}</p>
                                )}

                                <div className="issue-meta">
                                    <div className="issue-tags">
                                        {(data.content.issue?.labels || []).map(label => (
                                            <span key={label} className="tag">{label}</span>
                                        ))}
                                    </div>
                                    {data.content.issue?.url && (
                                        <a
                                            className="search-button github-link"
                                            href={data.content.issue.url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                        >
                                            View issue on GitHub ↗
                                        </a>
                                    )}
                                </div>

                                {data.content.issue?.body && (
                                    <div className="detail-item">
                                        <span className="label">Issue Description</span>
                                        <div className="markdown-body pr-template">
                                            <ReactMarkdown>{data.content.issue.body}</ReactMarkdown>
                                        </div>
                                    </div>
                                )}

                                <div className="guide-section">
                                    <h3>🚀 How to get started and open your PR</h3>
                                    <div className="guide-steps">
                                        <div className="guide-step">
                                            <div className="step-number">1</div>
                                            <div className="step-content">
                                                <h4>Ask for assignment</h4>
                                                <p>Go to the issue page and leave a friendly comment: <em>"Hi! I'd love to work on this issue. Could you please assign it to me?"</em> Wait for their approval before you start writing code.</p>
                                            </div>
                                        </div>
                                        <div className="guide-step">
                                            <div className="step-number">2</div>
                                            <div className="step-content">
                                                <h4>Fork & Clone</h4>
                                                <p>Click the <strong>Fork</strong> button on GitHub, then clone your fork to your computer using your terminal: <code>git clone {data.content.repo_url || 'https://github.com/repository'}.git</code></p>
                                            </div>
                                        </div>
                                        <div className="guide-step">
                                            <div className="step-number">3</div>
                                            <div className="step-content">
                                                <h4>Create a branch & Code</h4>
                                                <p>Create the branch suggested below. Write your code and test it!</p>
                                            </div>
                                        </div>
                                        <div className="guide-step">
                                            <div className="step-number">4</div>
                                            <div className="step-content">
                                                <h4>Commit & Push</h4>
                                                <p>Commit using the suggested message below, then push to your fork.</p>
                                            </div>
                                        </div>
                                        <div className="guide-step">
                                            <div className="step-number">5</div>
                                            <div className="step-content">
                                                <h4>Open the Pull Request</h4>
                                                <p>Go to the original repository on GitHub, click <strong>Compare & pull request</strong>, and paste the AI-drafted PR template provided below!</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <div className="plan-details">
                                    <SimilarPRs similar={data.content.similar_prs} />

                                    <div className="detail-item">
                                        <span className="label">Suggested Branch Name</span>
                                        <code className="code-block">{data.content.branch}</code>
                                    </div>
                                    <div className="detail-item">
                                        <span className="label">Suggested Commit Message</span>
                                        <code className="code-block">{data.content.commit_message}</code>
                                    </div>

                                    {data.content.markdown && (
                                        <div className="detail-item">
                                            <span className="label">Full Analysis & Contribution Plan</span>
                                            <div className="markdown-body pr-template">
                                                <ReactMarkdown>{data.content.markdown}</ReactMarkdown>
                                            </div>
                                        </div>
                                    )}

                                    <div className="detail-item" style={{ marginTop: '1rem' }}>
                                        <span className="label">Comment Template (To claim the issue)</span>
                                        <div className="markdown-body pr-template" style={{ borderColor: 'rgba(var(--secondary-rgb), 0.5)', background: 'rgba(var(--secondary-rgb), 0.05)' }}>
                                            <ReactMarkdown>
                                                {`Hi! 👋 I would love to contribute by working on this issue. 

Could you please assign it to me? Let me know if there is anything specific I should know before getting started!`}
                                            </ReactMarkdown>
                                        </div>
                                    </div>

                                    {data.content.ai_error && (
                                        <div className="error-box">
                                            <span className="error-icon">⚠️</span>
                                            <p>{data.content.ai_error}</p>
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                )}

            </main>

                {started && (
                    <aside className="glass chat-section animate-in" style={{ animationDelay: '0.2s' }}>
                        <div className="chat-header">
                            <div>
                                <h3>💬 {chat?.mode === 'issue' ? 'Chat with Issue Data' : 'Chat with Repo Data'}</h3>
                                {chatReady && chat && (
                                    <p className={`chat-index ${index?.state || 'loading'}`}>
                                        {chat.mode === 'issue' ? `Issue #${chat.number} · ` : ''}
                                        {index?.state === 'ready'
                                            ? `Searching ${index.stats.files} files of ${chat.repo}`
                                            : index?.state === 'error'
                                                ? `Code search unavailable (${index.error}). Answers use the docs only.`
                                                : `Indexing ${chat.repo}'s files…`}
                                    </p>
                                )}
                            </div>
                            {messages.length > 0 && (
                                <button
                                    onClick={clearChat}
                                    className="search-button"
                                    style={{ padding: '0.5rem 1rem', fontSize: '0.9rem', background: 'rgba(239, 68, 68, 0.2)', color: 'var(--error-text)', boxShadow: 'none' }}
                                    type="button"
                                    disabled={chatLoading}
                                >
                                    Clear
                                </button>
                            )}
                        </div>
                        <div className="chat-history">
                            {messages.length === 0 && !chatLoading && (
                                <p className="chat-empty">
                                    {!chatReady
                                        ? loading
                                            ? 'Chat will be ready when the analysis finishes.'
                                            : 'Analyze a repo to start chatting about it.'
                                        : chat?.mode === 'issue'
                                            ? `Ask anything about issue #${chat.number}: what it's asking for, which files to change, how to test your fix, or what the maintainers said.`
                                            : "Ask anything about this repo: how it's built, where something lives in the code, how to set it up, or how to run the tests."}
                                </p>
                            )}
                            {messages.map((msg, idx) => (
                                <div key={idx} className={`chat-message ${msg.role.toLowerCase()}`}>
                                    <strong>{msg.role}: </strong>
                                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                                    {msg.sources?.length > 0 && (
                                        <details className="chat-sources">
                                            <summary>Searched {msg.sources.length} file sections</summary>
                                            <ul>
                                                {msg.sources.map(s => (
                                                    <li key={`${s.path}:${s.start}`}>
                                                        <a
                                                            href={`https://github.com/${msg.repo}/blob/${msg.branch}/${s.path}#L${s.start}-L${s.end}`}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                        >
                                                            {s.path}:{s.start}-{s.end}
                                                        </a>
                                                    </li>
                                                ))}
                                            </ul>
                                        </details>
                                    )}
                                </div>
                            ))}
                            {chatLoading && <div className="chat-message assistant pulsing">Searching the code and thinking...</div>}
                        </div>
                        <form onSubmit={handleChatSubmit} className="chat-form">
                            <input
                                type="text"
                                className="search-input"
                                placeholder={chat?.mode === 'issue' ? 'Ask about this issue...' : 'Ask a question about this repo...'}
                                value={chatInput}
                                onChange={e => setChatInput(e.target.value)}
                                disabled={Boolean(chatPending) || !chatReady}
                            />
                            <button type="submit" className="search-button" disabled={Boolean(chatPending) || !chatReady || !chatInput.trim()}>
                                Send
                            </button>
                        </form>
                    </aside>
                )}
        </div>
    );
}
