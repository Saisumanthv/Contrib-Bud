import { useEffect, useState } from 'react';
import { THEMES, applyTheme, savedTheme } from './theme';

export default function ThemePicker() {
    const [theme, setTheme] = useState(savedTheme);
    const [open, setOpen] = useState(false);

    // Another open tab (e.g. the flow chart) changed the theme: follow it.
    useEffect(() => {
        const onStorage = e => {
            if (e.key !== 'theme') return;
            const id = savedTheme();
            setTheme(id);
            applyTheme(id);
        };
        window.addEventListener('storage', onStorage);
        return () => window.removeEventListener('storage', onStorage);
    }, []);

    const choose = id => {
        setTheme(id);
        applyTheme(id);
    };

    return (
        <div className={`theme-picker ${open ? 'open' : ''}`}>
            {/* While closed the bubbles are hidden behind the button and can't be focused. */}
            <div className="theme-bubbles-track">
                <div id="theme-bubbles" className="theme-bubbles" role="radiogroup" aria-label="Theme" inert={!open}>
                    {THEMES.map(t => (
                        <button
                            key={t.id}
                            type="button"
                            role="radio"
                            aria-checked={theme === t.id}
                            aria-label={`${t.name} theme`}
                            title={`${t.name} theme`}
                            className={`theme-bubble ${theme === t.id ? 'active' : ''}`}
                            style={{ background: t.swatch }}
                            onClick={() => choose(t.id)}
                        />
                    ))}
                </div>
            </div>
            <button
                type="button"
                className="theme-toggle"
                aria-expanded={open}
                aria-controls="theme-bubbles"
                onClick={() => setOpen(!open)}
            >
                🎨 Themes
            </button>
        </div>
    );
}
