import { useEffect, useState } from 'react';
import './App.css';
import { fetchTree, githubFileUrl } from './api';
import FlowChart from './FlowChart';
import ThemePicker from './ThemePicker';

// Full-window flow chart, opened in its own tab as ?flowchart=owner/repo&branch=name.
export default function FlowChartPage({ repo, branch }) {
    const [tree, setTree] = useState(null);
    const [error, setError] = useState('');

    useEffect(() => {
        document.title = `${repo} · Flow chart · My First PR`;
        fetchTree(repo, branch).then(setTree, err => setError(err.message));
    }, [repo, branch]);

    return (
        <div className="flowchart-page">
            <header className="flowchart-page-header">
                <h1>🔀 {repo}</h1>
                {tree && (
                    <span className="issues-hint">
                        {tree.paths.length} files on <code>{tree.branch}</code>
                        {tree.truncated && ' (partial: GitHub returned only part of this very large tree)'}
                    </span>
                )}
                <ThemePicker />
            </header>
            {error && (
                <div className="glass error-box">
                    <span className="error-icon">⚠️</span>
                    <p>{error}</p>
                </div>
            )}
            {!tree && !error && (
                <div className="loading-state">
                    <div className="spinner"></div>
                    <p>Loading files...</p>
                </div>
            )}
            {tree && (
                <FlowChart
                    className="fullscreen"
                    root={tree.root}
                    rootName={tree.repo.split('/')[1]}
                    fileUrl={path => githubFileUrl(tree, path)}
                />
            )}
        </div>
    );
}
