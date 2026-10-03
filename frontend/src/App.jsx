import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './App.css';

export default function App() {
    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);
    const [issues, setIssues] = useState(null);
    const [error, setError] = useState('');

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!query.trim()) return;

        setLoading(true);
        setError('');
        setData(null);
        setIssues(null);

        try {
            const isIssue = query.includes('/issues/');

            if (isIssue) {
                const response = await fetch(`https://contrib-bud.onrender.com//api/plan`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ issue_url: query, ai: true })
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Failed to generate plan');
                setData({ type: 'plan', content: result });
            } else {
                const [analyzeRes, issuesRes] = await Promise.all([
                    fetch(`https://contrib-bud.onrender.com//api/analyze`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ repo: query, ai: true })
                    }),
                    fetch(`https://contrib-bud.onrender.com//api/issues`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ repo: query, ai: true })
                    })
                ]);

                const analyzeResult = await analyzeRes.json();
                const issuesResult = await issuesRes.json();

                if (!analyzeRes.ok) throw new Error(analyzeResult.error || 'Failed to analyze repository');
                if (!issuesRes.ok) throw new Error(issuesResult.error || 'Failed to fetch issues');

                setData({ type: 'analyze', content: analyzeResult });
                setIssues(issuesResult);
            }
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleIssueClick = async (issueUrl) => {
        setQuery(issueUrl);
        setLoading(true);
        setError('');
        setData(null);
        setIssues(null);

        try {
            const response = await fetch(`https://contrib-bud.onrender.com//api/plan`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ issue_url: issueUrl, ai: true })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Failed to generate plan');
            setData({ type: 'plan', content: result });
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="container">
            <header className="header animate-in" style={{ animationDelay: '0.1s' }}>
                <div className="logo-container">
                    <h1>Contrib Bud</h1>
                </div>
                <p>Your AI Mentor for Open Source Contributions</p>
            </header>

            <main>
                <div className="glass search-box animate-in" style={{ animationDelay: '0.2s' }}>
                    <form onSubmit={handleSubmit} className="search-form">
                        <input
                            type="text"
                            placeholder="Paste a GitHub repo or issue link (e.g. facebook/react)"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            className="search-input"
                        />
                        <button type="submit" disabled={loading} className="search-button">
                            {loading ? 'Working...' : 'Analyze'}
                        </button>
                    </form>
                </div>

                {error && (
                    <div className="glass error-box animate-in" style={{ animationDelay: '0.1s' }}>
                        <span className="error-icon">⚠️</span>
                        <p>{error}</p>
                    </div>
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

                                {data.content.ai_summary && (
                                    <div className="ai-summary">
                                        <h3>AI Contribution Summary</h3>
                                        <div className="markdown-body">
                                            <ReactMarkdown>{data.content.ai_summary}</ReactMarkdown>
                                        </div>
                                    </div>
                                )}

                                {issues && issues.issues && issues.issues.length > 0 && (
                                    <div className="issues-section">
                                        <h3>🎯 Beginner Friendly Issues</h3>
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
                                {issues && (!issues.issues || issues.issues.length === 0) && (
                                    <div className="issues-section">
                                        <h3>🎯 Beginner Friendly Issues</h3>
                                        <p className="issues-hint">No beginner-friendly issues were found in this repository right now. Try checking their issues tab directly!</p>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div>
                                <h2>Plan for Issue #{data.content.issue?.number}</h2>
                                {data.content.issue?.title && (
                                    <p className="subtitle">{data.content.issue.title}</p>
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
                                            <span className="label">PR Description Template</span>
                                            <div className="markdown-body pr-template">
                                                <ReactMarkdown>{data.content.markdown}</ReactMarkdown>
                                            </div>
                                        </div>
                                    )}

                                    <div className="detail-item" style={{ marginTop: '1rem' }}>
                                        <span className="label">Comment Template (To claim the issue)</span>
                                        <div className="markdown-body pr-template" style={{ borderColor: 'rgba(59, 130, 246, 0.5)', background: 'rgba(59, 130, 246, 0.05)' }}>
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
        </div>
    );
}
