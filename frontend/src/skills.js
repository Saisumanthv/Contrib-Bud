// The user's skills ("python, react"), remembered in this browser, and how issues fit them.
// Matching mirrors match_skills.py; the alias table comes from the backend (skill_aliases).
const STORAGE_KEY = 'skills';

export function loadSkills() {
    try {
        return localStorage.getItem(STORAGE_KEY) || '';
    } catch {
        return '';
    }
}

export function saveSkills(text) {
    try {
        localStorage.setItem(STORAGE_KEY, text);
    } catch {
        // Storage blocked: the skills still apply for this visit.
    }
}

export function splitSkills(text) {
    return text.split(/[,;\n]+/).map(s => s.trim()).filter(Boolean);
}

function canonical(name, aliases) {
    const lower = name.toLowerCase();
    return aliases[lower] || aliases[lower.replace(/[\s.]+(js)?$/, '')] || name;
}

// { fit: 'good' | 'partial' | 'stretch' | 'any', known, missing } for an issue's techs.
export function skillFit(techs, skillsText, aliases = {}) {
    if (!techs?.length) return { fit: 'any', known: [], missing: [] };
    const known = new Set(splitSkills(skillsText).map(s => canonical(s, aliases).toLowerCase()));
    if (techs.length === 1 && techs[0] === 'Markdown/Writing') {
        return { fit: 'good', known: techs, missing: [] }; // docs, typos, translations
    }
    const have = techs.filter(t => known.has(t.toLowerCase()));
    const missing = techs.filter(t => !known.has(t.toLowerCase()));
    const fit = missing.length === 0 ? 'good' : have.length ? 'partial' : 'stretch';
    return { fit, known: have, missing };
}

export const CONTRIBUTION_ICONS = { docs: '📄', typo: '✏️', tests: '🧪', translation: '🌐', code: '💻' };
export const FIT_LABELS = { good: 'Good fit', partial: 'Partial fit', stretch: 'Stretch', any: '' };
