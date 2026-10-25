import { useState } from 'react';
import { API_URL } from './api';
import HealthBadge from './HealthBadge';
import { loadSkills, saveSkills } from './skills';

// "Help me find where to contribute": languages + interests in, welcoming repos out,
// ranked by the repo health check (Welcoming first).
export default function Discover({ onAnalyze }) {
    const [languages, setLanguages] = useState(loadSkills);
    const [interests, setInterests] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState(null);

    const submit = async e => {
        e.preventDefault();
        if (!languages.trim() && !interests.trim()) return;
        saveSkills(languages);
        setLoading(true);
        setError('');
        try {
            const response = await fetch(`${API_URL}/api/discover`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ languages, interests }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Search failed');
            setResult(data);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="coach">
            <form className="coach-form" onSubmit={submit}>
                <label className="label" htmlFor="discover-langs">Languages and tools you know</label>
                <input
                    id="discover-langs"
                    className="coach-input"
                    placeholder="e.g. python, javascript, react, css"
                    value={languages}
                    onChange={e => setLanguages(e.target.value)}
                />
                <label className="label" htmlFor="discover-interests">What you're interested in</label>
                <input
                    id="discover-interests"
                    className="coach-input"
                    placeholder="e.g. cli, data visualization, games, education"
                    value={interests}
                    onChange={e => setInterests(e.target.value)}
                />
                <div className="coach-actions">
                    <button type="submit" className="search-button" disabled={loading || (!languages.trim() && !interests.trim())}>
                        {loading ? 'Searching…' : 'Find repos for me'}
                    </button>
                    <span className="similar-empty">
                        Active repos with open beginner issues, checked for how quickly maintainers respond.
                    </span>
                </div>
            </form>

            {error && (
                <div className="error-box">
                    <span className="error-icon">⚠️</span>
                    <p>{error}</p>
                </div>
            )}
            {loading && <p className="issues-hint pulsing">Searching GitHub and checking how welcoming each repo is… this takes a little while.</p>}

            {result && !loading && (
                result.repos.length === 0 ? (
                    <p className="similar-empty">
                        No active repos with open beginner issues matched. Try broader interests or another language.
                    </p>
                ) : (
                    <div className="similar-list">
                        {result.repos.map(r => (
                            <article key={r.repo} className={`similar-card discover-card ${r.health.verdict === 'inactive' ? 'dimmed' : ''}`}>
                                <div className="discover-head">
                                    <a className="similar-issue" href={r.url} target="_blank" rel="noopener noreferrer">{r.repo}</a>
                                    <span className="similar-size">★ {r.stars} · {r.language || '?'}</span>
                                </div>
                                {r.description && <p className="similar-review">{r.description}</p>}
                                {(r.matched_languages.length > 0 || r.matched_interests.length > 0) && (
                                    <div className="issue-tags">
                                        {[...r.matched_languages, ...r.matched_interests].map(m => (
                                            <span key={m} className="tag match-tag">✓ {m}</span>
                                        ))}
                                    </div>
                                )}
                                <HealthBadge health={r.health.verdict ? r.health : { verdict: null, error: r.health.error }} />
                                <div className="coach-actions">
                                    <button type="button" className="copy-button" onClick={() => onAnalyze(r.repo)}>
                                        See how to contribute →
                                    </button>
                                    <a className="similar-why" href={r.good_first_issues_url} target="_blank" rel="noopener noreferrer">
                                        Good first issues on GitHub ↗
                                    </a>
                                </div>
                            </article>
                        ))}
                    </div>
                )
            )}
        </div>
    );
}
