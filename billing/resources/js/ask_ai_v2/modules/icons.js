export const ICON_PATHS = {
    check: ['M20 6 9 17l-5-5'],
    x: ['M18 6 6 18', 'M6 6l12 12'],
    'loader-circle': ['M21 12a9 9 0 1 1-6.219-8.56'],
    'chevron-right': ['m9 18 6-6-6-6'],
    'arrow-right': ['M5 12h14', 'm12 5 7 7-7 7'],
    'circle-check': ['M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0', 'm9 12 2 2 4-4'],
    'circle-x': ['M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0', 'm15 9-6 6', 'm9 9 6 6'],
    'circle-help': ['M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0', 'M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3', 'M12 17h.01'],
    'circle-minus': ['M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0', 'M8 12h8'],
    search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16', 'M21 21l-4.35-4.35'],
};

export function mcpIcon(name, className) {
    const paths = ICON_PATHS[name];
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.75');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    if (className) svg.setAttribute('class', className);
    for (const d of paths || []) {
        const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        p.setAttribute('d', d);
        svg.appendChild(p);
    }
    return svg;
}
