const ICONS = { good: '✔', ok: '!', bad: '✘', unknown: '–', info: 'i' };

// "Is this repo worth my time?" Welcoming / Slow / Inactive, with the signals behind it.
export default function HealthBadge({ health }) {
    if (!health) return null;
    if (!health.verdict) {
        return (
            <p className="health-unavailable">
                Repo health couldn't be checked{health.error ? `: ${health.error}` : '.'}
            </p>
        );
    }
    return (
        <details className={`health health-${health.verdict}`}>
            <summary>
                <span className="health-badge">● {health.label}</span>
                <span className="health-summary">{health.summary}</span>
                <span className="health-why">Why?</span>
            </summary>
            <ul className="health-signals">
                {health.signals.map(signal => (
                    <li key={signal.key} className={`signal-${signal.status}`}>
                        <span className="signal-icon" aria-hidden="true">{ICONS[signal.status]}</span>
                        <span className="signal-label">{signal.label}</span>
                        <span className="signal-detail">{signal.detail}</span>
                    </li>
                ))}
            </ul>
            {health.confidence === 'low' && (
                <p className="health-note">Based on very few recent pull requests, so treat this as a rough guide.</p>
            )}
        </details>
    );
}
