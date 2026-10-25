// The swatch is what the bubble shows; the colours themselves live in index.css.
export const THEMES = [
    { id: 'orange', name: 'Orange', swatch: '#f97316' },
    { id: 'red', name: 'Red', swatch: '#ef4444' },
    { id: 'pink', name: 'Pink', swatch: '#ec4899' },
    { id: 'purple', name: 'Purple', swatch: '#a855f7' },
    { id: 'indigo', name: 'Indigo', swatch: '#6366f1' },
    { id: 'blue', name: 'Blue', swatch: '#3b82f6' },
    { id: 'teal', name: 'Teal', swatch: '#14b8a6' },
    { id: 'green', name: 'Green', swatch: '#22c55e' },
    { id: 'yellow', name: 'Yellow', swatch: '#eab308' },
    { id: 'sunset', name: 'Sunset', swatch: 'linear-gradient(135deg, #f43f5e 0%, #f97316 55%, #fbbf24 100%)' },
    { id: 'neon', name: 'Neon', swatch: 'linear-gradient(135deg, #39ff14 0%, #00f0ff 50%, #ff2bd6 100%)' },
    { id: 'light', name: 'Light', swatch: '#fafaf9' },
    { id: 'dark', name: 'Dark', swatch: '#09090b' },
];

const STORAGE_KEY = 'theme';
const DEFAULT_THEME = 'orange';

export function savedTheme() {
    try {
        const id = localStorage.getItem(STORAGE_KEY);
        return THEMES.some(t => t.id === id) ? id : DEFAULT_THEME;
    } catch {
        return DEFAULT_THEME; // storage can be blocked (private windows, strict settings)
    }
}

export function applyTheme(id) {
    if (id === DEFAULT_THEME) delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = id;
    try {
        localStorage.setItem(STORAGE_KEY, id);
    } catch {
        // Not remembered, but the theme still applies for this visit.
    }
}
