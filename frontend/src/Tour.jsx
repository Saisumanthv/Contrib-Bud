import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { API_URL } from './api';
import CitationReport from './CitationReport';
import { citationComponents, markCitations } from './citationLinks';
import { markdownProps } from './markdown';

const tours = new Map(); // repo -> tour, for this visit (writing one takes a model call)

// "How is this repo organized, and where does X happen?" A guided tour built from the code
// index, with buttons that ask the chat about a folder or a feature.
export default function Tour({ repo, onAsk, onShowTree, flowchartUrl }) {
    const [tour, setTour] = useState(() => tours.get(repo) || null);
    const [error, setError] = useState('');
    const [where, setWhere] = useState('');

    useEffect(() => {
        if (tours.has(repo)) return undefined;
        const controller = new AbortController();
        fetch(`${API_URL}/api/tour`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo, ai: true }),
            signal: controller.signal,
        })
            .then(async response => {
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Could not build the tour');
                tours.set(repo, data);
                setTour(data);
            })
            .catch(err => {
                if (!controller.signal.aborted) setError(err.message);
            });
        return () => controller.abort();
    }, [repo]);

    const askWhere = e => {
        e.preventDefault();
        if (!where.trim()) return;
        onAsk(`Where in the code does this happen: ${where.trim()}? Point me to the files and lines.`);
        setWhere('');
    };

    if (error) return <p className="issues-hint">⚠️ {error}</p>;
    if (!tour) return <p className="issues-hint pulsing">Reading the code and writing your tour… (the first time takes a little while)</p>;

    const msg = { citations: tour.citations, repo: tour.repo, branch: tour.branch };
    return (
        <div className="tour">
            <div className="tour-actions">
                <button type="button" className="copy-button" onClick={onShowTree}>🌳 Browse the file tree</button>
                <a className="copy-button" href={flowchartUrl} target="_blank" rel="noopener noreferrer">🔀 Open the flow chart ↗</a>
            </div>
            {tour.ai_error && <p className="similar-empty">⚠️ {tour.ai_error}</p>}
            <div className="markdown-body">
                <ReactMarkdown {...markdownProps(citationComponents(msg))}>
                    {markCitations(tour.markdown, tour.citations)}
                </ReactMarkdown>
            </div>
            <CitationReport msg={msg} />

            <form className="tour-where" onSubmit={askWhere}>
                <label className="label" htmlFor="tour-where">Where does … happen?</label>
                <div className="tour-where-row">
                    <input
                        id="tour-where"
                        className="coach-input"
                        placeholder="e.g. command-line arguments are parsed"
                        value={where}
                        onChange={e => setWhere(e.target.value)}
                    />
                    <button type="submit" className="copy-button" disabled={!where.trim()}>Ask in chat →</button>
                </div>
            </form>

            <span className="label">Folders</span>
            <div className="tour-areas">
                {tour.facts.areas.map(area => (
                    <article key={area.folder} className="similar-card">
                        <div className="discover-head">
                            <strong>{area.folder === '(root)' ? 'Top-level files' : `${area.folder}/`}</strong>
                            <span className="similar-size">{area.files} files{area.languages.length ? ` · ${area.languages.join(', ')}` : ''}</span>
                        </div>
                        {area.description && <p className="similar-review">{area.description}</p>}
                        <ul className="similar-files">
                            {area.sample.slice(0, 4).map(f => (
                                <li key={f.path} title={f.about || undefined}>
                                    <a href={`https://github.com/${tour.repo}/blob/${tour.branch}/${f.path}`} target="_blank" rel="noopener noreferrer">
                                        <code>{f.path}</code>
                                    </a>
                                </li>
                            ))}
                        </ul>
                        {area.folder !== '(root)' && (
                            <button
                                type="button"
                                className="link-button tour-ask"
                                onClick={() => onAsk(`What does the ${area.folder}/ folder do, and which files in it matter most for a newcomer?`)}
                            >
                                Ask about {area.folder}/ in chat →
                            </button>
                        )}
                    </article>
                ))}
            </div>
        </div>
    );
}
