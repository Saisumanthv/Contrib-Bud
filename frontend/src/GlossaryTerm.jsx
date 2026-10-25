import { useId } from 'react';
import { GLOSSARY } from './glossary';

// A jargon word with its plain-English meaning on hover, keyboard focus or tap.
export default function GlossaryTerm({ term, children }) {
    const id = useId();
    const entry = GLOSSARY[term];
    if (!entry) return children;
    const [label, definition] = entry;
    return (
        <span className="glossary-term" tabIndex={0} aria-describedby={id}>
            {children}
            <span role="tooltip" id={id} className="glossary-tip">
                <strong>{label}</strong> {definition}
            </span>
        </span>
    );
}
