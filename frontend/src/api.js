export const API_URL = import.meta.env.VITE_API_URL
    || (import.meta.env.DEV ? 'http://127.0.0.1:8765' : 'https://contrib-bud.onrender.com');

export async function fetchTree(repo, branch) {
    const response = await fetch(`${API_URL}/api/tree`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo, branch })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Failed to load the file tree');
    return { ...result, root: buildTree(result.paths) };
}

// Turn ["src/a.js", "README.md"] into nested folders: { folders: {src: {...}}, files: [...] }.
export function buildTree(paths) {
    const root = { folders: {}, files: [] };
    for (const path of paths) {
        const parts = path.split('/');
        let node = root;
        for (const part of parts.slice(0, -1)) {
            node = node.folders[part] ??= { folders: {}, files: [] };
        }
        node.files.push({ name: parts[parts.length - 1], path });
    }
    return root;
}

export const githubFileUrl = (tree, path) => `https://github.com/${tree.repo}/blob/${tree.branch}/${path}`;
