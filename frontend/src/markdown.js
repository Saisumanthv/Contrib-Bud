import { createElement } from 'react';
import { remarkGlossary } from './glossary';
import GlossaryTerm from './GlossaryTerm';

// Shared react-markdown setup: beginner terms get hover definitions. Pass extra components
// (e.g. citation chips) to combine them.
export function markdownProps(components = {}) {
    return {
        remarkPlugins: [remarkGlossary],
        components: {
            span({ node, className, children, ...rest }) {
                const term = node?.properties?.dataTerm;
                if (term) return createElement(GlossaryTerm, { term }, children);
                return createElement('span', { className, ...rest }, children);
            },
            ...components,
        },
    };
}
