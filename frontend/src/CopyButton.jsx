import { useState } from 'react';

export default function CopyButton({ text, label = 'Copy' }) {
    const [copied, setCopied] = useState(false);

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            setCopied(false); // clipboard blocked: the text is still there to select
        }
    };

    return (
        <button type="button" className="copy-button" onClick={copy}>
            {copied ? 'Copied ✓' : label}
        </button>
    );
}
