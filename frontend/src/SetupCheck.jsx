import CopyButton from './CopyButton';

// What to have installed before setting up the repo (from its own files), and the command
// that checks this machine against it.
export default function SetupCheck({ requirements }) {
    if (!requirements?.length) return null;
    return (
        <div className="detail-item">
            <span className="label">🩺 What you need installed</span>
            <ul className="setup-reqs">
                {requirements.map(r => (
                    <li key={`${r.tool}-${r.source}`}>
                        <code>{r.tool}{r.required ? ` ${r.required}` : ''}</code>
                        <span className="similar-why">
                            {r.kind === 'pin' ? 'the maintainers use this version' : r.kind === 'tool' ? 'used by the setup commands' : 'required'}, from {r.source}
                        </span>
                    </li>
                ))}
            </ul>
            <div className="coach-commands">
                <pre><code>buddy doctor</code></pre>
                <CopyButton text="buddy doctor" />
            </div>
            <p className="similar-empty">
                Run it inside your clone to check your installed versions against these before setup fails
                with confusing errors.
            </p>
        </div>
    );
}
