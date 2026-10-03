import { useState } from 'react';
import './App.css';

export default function App() {
    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!query.trim()) return;

        setLoading(true);
        setError('');
        setData(null);

        try {
            // Check if the query is an issue link
            const isIssue = query.includes('/issues/');
            const endpoint = isIssue ? '/api/plan' : '/api/analyze';
            const payload = isIssue ? { issue_url: query, ai: true } : { repo: query, ai: true };

            const response = await fetch(`http://localhost:8765${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to analyze repository');
            }

            setData({ type: isIssue ? 'plan' : 'analyze', content: result });
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
                    <div className="logo-icon">✨</div>
                    <h1>Contrib Buddy</h1>
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
                            {loading ? 'Analyzing...' : 'Analyze'}
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
                        <p>Our AI is analyzing the repository facts and guidelines...</p>
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
                                        <div className="summary-content">{data.content.ai_summary}</div>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div>
                                <h2>Plan for Issue #{data.content.issue?.number}</h2>
                                {data.content.issue?.title && (
                                    <p className="subtitle">{data.content.issue.title}</p>
                                )}
                                
                                <div className="plan-details">
                                    <div className="detail-item">
                                        <span className="label">Suggested Branch Name</span>
                                        <code className="code-block">{data.content.branch}</code>
                                    </div>
                                    <div className="detail-item">
                                        <span className="label">Suggested Commit Message</span>
                                        <code className="code-block">{data.content.commit_message}</code>
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
