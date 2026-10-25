import { useEffect, useRef, useState } from 'react';

const COL = 250;        // horizontal distance between depth levels
const ROW = 42;         // vertical distance between leaf rows
const NODE_W = 210;
const NODE_H = 30;
const FILE_LIMIT = 25;  // files shown per folder before a "+N more" node
const MIN_SCALE = 0.2;
const MAX_SCALE = 2.5;
const START = { x: 24, y: 24 };

const truncate = (text, max = 24) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

// Lay out only the expanded part of the tree, left to right. Leaves take one row each and a
// parent sits midway between its first and last child.
function layoutChart(root, rootName, expanded) {
    const nodes = [];
    const edges = [];
    let row = 0;

    function visit(kind, name, node, path, depth) {
        const id = kind === 'folder' ? path || '/' : path;
        const x = depth * COL;
        const children = [];

        if (kind === 'folder' && expanded.has(id)) {
            for (const folder of Object.keys(node.folders).sort()) {
                children.push(['folder', folder, node.folders[folder], path ? `${path}/${folder}` : folder]);
            }
            const files = expanded.has(`${id}#all`) ? node.files : node.files.slice(0, FILE_LIMIT);
            for (const file of files) children.push(['file', file.name, null, file.path]);
            if (files.length < node.files.length) {
                children.push(['more', `+${node.files.length - files.length} more files`, null, `${id}#all`]);
            }
        }

        let y;
        if (children.length) {
            const ys = children.map(([k, n, child, p]) => visit(k, n, child, p, depth + 1));
            y = (ys[0] + ys[ys.length - 1]) / 2;
            ys.forEach((childY, i) => edges.push({ id: `${id}->${children[i][3]}`, x1: x + NODE_W, y1: y, x2: x + COL, y2: childY }));
        } else {
            y = row++ * ROW;
        }

        const count = node ? Object.keys(node.folders).length + node.files.length : 0;
        nodes.push({ id, kind, path, x, y, label: depth === 0 ? rootName : name, count, open: expanded.has(id) });
        return y;
    }

    visit('folder', rootName, root, '', 0);
    return { nodes, edges };
}

export default function FlowChart({ root, rootName, fileUrl, className = '' }) {
    const [expanded, setExpanded] = useState(() => new Set(['/']));
    const [view, setView] = useState({ scale: 1, ...START });
    const viewport = useRef(null);
    const moved = useRef(false);

    const { nodes, edges } = layoutChart(root, rootName, expanded);

    const zoomAt = (factor, cx, cy) => {
        setView(v => {
            const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor));
            // Keep the point under (cx, cy) fixed while zooming.
            const k = scale / v.scale;
            return { scale, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k };
        });
    };

    const zoomCenter = factor => {
        const el = viewport.current;
        zoomAt(factor, el.clientWidth / 2, el.clientHeight / 2);
    };

    const fit = () => {
        const el = viewport.current;
        const width = Math.max(...nodes.map(n => n.x)) + NODE_W;
        const height = Math.max(...nodes.map(n => n.y)) + NODE_H;
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE,
            Math.min((el.clientWidth - 48) / width, (el.clientHeight - 48) / height)));
        setView({ scale, x: (el.clientWidth - width * scale) / 2, y: (el.clientHeight - height * scale) / 2 });
    };

    const reset = () => setView({ scale: 1, ...START });

    // Ctrl/Cmd + wheel zooms. React's wheel listener is passive, so register our own to be
    // able to stop the browser's page zoom.
    useEffect(() => {
        const el = viewport.current;
        const onWheel = e => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            const rect = el.getBoundingClientRect();
            zoomAt(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - rect.left, e.clientY - rect.top);
        };
        el.addEventListener('wheel', onWheel, { passive: false });
        return () => el.removeEventListener('wheel', onWheel);
    }, []);

    const startDrag = e => {
        if (e.button !== 0) return;
        const start = { x: e.clientX, y: e.clientY, view };
        moved.current = false;
        const onMove = ev => {
            const dx = ev.clientX - start.x;
            const dy = ev.clientY - start.y;
            if (Math.abs(dx) + Math.abs(dy) > 3) moved.current = true;
            setView({ ...start.view, x: start.view.x + dx, y: start.view.y + dy });
        };
        const onUp = () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
    };

    const handleNodeClick = node => {
        if (moved.current) return; // the pointer was dragging the chart, not clicking
        if (node.kind === 'file') {
            window.open(fileUrl(node.path), '_blank', 'noopener,noreferrer');
            return;
        }
        const next = new Set(expanded);
        if (next.has(node.id) && node.kind === 'folder') next.delete(node.id);
        else next.add(node.id);

        // Re-centring parents between their new children moves boxes up or down; shift the view
        // so the clicked folder stays under the pointer. ("+N more" disappears, so anchor its folder.)
        const anchor = node.kind === 'more' ? node.id.replace(/#all$/, '') : node.id;
        const before = nodes.find(n => n.id === anchor);
        const after = layoutChart(root, rootName, next).nodes.find(n => n.id === anchor);
        setExpanded(next);
        setView(v => ({ ...v, y: v.y - (after.y - before.y) * v.scale }));
    };

    const label = node => {
        if (node.kind === 'folder') return `${node.open ? '▾' : '▸'} 📁 ${truncate(node.label, 18)} (${node.count})`;
        if (node.kind === 'file') return `📄 ${truncate(node.label)}`;
        return node.label;
    };

    return (
        <div className={`flowchart ${className}`}>
            <div className="flowchart-toolbar">
                <button type="button" onClick={() => zoomCenter(1 / 1.2)} aria-label="Zoom out">−</button>
                <button type="button" onClick={reset} className="zoom-level" title="Reset zoom">{Math.round(view.scale * 100)}%</button>
                <button type="button" onClick={() => zoomCenter(1.2)} aria-label="Zoom in">+</button>
                <button type="button" onClick={fit}>Fit</button>
                <span className="flowchart-help">Drag to move · Ctrl + scroll to zoom · Click a folder to open it</span>
            </div>
            <div className="flowchart-viewport" ref={viewport} onPointerDown={startDrag}>
                <svg width="100%" height="100%">
                    <g transform={`translate(${view.x},${view.y}) scale(${view.scale})`}>
                        {edges.map(e => {
                            const mid = (e.x1 + e.x2) / 2;
                            return (
                                <path
                                    key={e.id}
                                    className="flow-edge"
                                    d={`M${e.x1},${e.y1 + NODE_H / 2} C${mid},${e.y1 + NODE_H / 2} ${mid},${e.y2 + NODE_H / 2} ${e.x2},${e.y2 + NODE_H / 2}`}
                                />
                            );
                        })}
                        {nodes.map(node => (
                            <g
                                key={node.id}
                                className={`flow-node ${node.kind} ${node.id === '/' ? 'root' : ''}`}
                                transform={`translate(${node.x},${node.y})`}
                                onClick={() => handleNodeClick(node)}
                            >
                                <title>{node.path || node.label}</title>
                                <rect width={NODE_W} height={NODE_H} rx="8" />
                                <text x="10" y={NODE_H / 2} dominantBaseline="central">{label(node)}</text>
                            </g>
                        ))}
                    </g>
                </svg>
            </div>
        </div>
    );
}
