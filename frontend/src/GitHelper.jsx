import { useEffect, useState } from 'react';
import { API_URL } from './api';
import CopyButton from './CopyButton';

const IS_URL = /^https?:\/\//;

// Exact git commands for forking, syncing, rebasing, fixing conflicts and undoing mistakes,
// filled in for this repo and branch. `buddy git` diagnoses a local clone.
export default function GitHelper({ repo, defaultBranch, branch }) {
    const [recipes, setRecipes] = useState(null);
    const [error, setError] = useState('');
    const [open, setOpen] = useState('setup');

    useEffect(() => {
        const controller = new AbortController();
        fetch(`${API_URL}/api/git`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo, default_branch: defaultBranch, branch }),
            signal: controller.signal,
        })
            .then(async response => {
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Could not load git commands');
                setRecipes(data.recipes);
            })
            .catch(err => {
                if (!controller.signal.aborted) setError(err.message);
            });
        return () => controller.abort();
    }, [repo, defaultBranch, branch]);

    if (error) return <p className="similar-empty">⚠️ {error}</p>;
    if (!recipes) return <p className="issues-hint pulsing">Loading git commands…</p>;
    return (
        <div className="git-helper">
            <p className="similar-empty">
                Pick what you're trying to do. In your clone, <code>buddy git</code> checks your remotes and
                branch and tells you which of these you need.
            </p>
            {recipes.map(recipe => (
                <details
                    key={recipe.id}
                    className="git-recipe"
                    open={open === recipe.id}
                    onToggle={e => {
                        if (e.currentTarget.open) setOpen(recipe.id);
                        else if (open === recipe.id) setOpen(null); // let the open one close
                    }}
                >
                    <summary>
                        <span className="git-recipe-title">{recipe.title}</span>
                        <span className="similar-why">{recipe.when}</span>
                    </summary>
                    <ol className="git-steps">
                        {recipe.steps.map(step => (
                            <li key={step.cmd}>
                                <div className="git-step-cmd">
                                    {IS_URL.test(step.cmd)
                                        ? <a href={step.cmd} target="_blank" rel="noopener noreferrer">{step.cmd}</a>
                                        : <code>{step.cmd}</code>}
                                    {!IS_URL.test(step.cmd) && !step.cmd.startsWith('(') && <CopyButton text={step.cmd} />}
                                </div>
                                <p>{step.why}</p>
                                {step.danger && <p className="git-danger">⚠ {step.danger}</p>}
                            </li>
                        ))}
                    </ol>
                </details>
            ))}
        </div>
    );
}
