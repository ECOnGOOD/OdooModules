(function () {
    let graphCounter = 0;

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function polarPoint(radius, angle, cx, cy) {
        return {
            x: cx + Math.cos(angle) * radius,
            y: cy + Math.sin(angle) * radius,
        };
    }

    function buildAdjacency(nodes, edges) {
        const adjacency = new Map();
        for (const node of nodes) {
            adjacency.set(node.id, new Set());
        }
        for (const edge of edges) {
            if (!adjacency.has(edge.source)) {
                adjacency.set(edge.source, new Set());
            }
            if (!adjacency.has(edge.target)) {
                adjacency.set(edge.target, new Set());
            }
            adjacency.get(edge.source).add(edge.target);
            adjacency.get(edge.target).add(edge.source);
        }
        return adjacency;
    }

    class PartnerRelationSimpleGraph {
        constructor(container) {
            this.container = container;
            this.uid = `partner_relation_graph_${++graphCounter}`;
            this.listeners = {};
            this.data = { nodes: [], edges: [], meta: {} };
            this.selection = { nodeId: null, edgeId: null };
            this.viewport = { scale: 1, x: 0, y: 0 };
            this.isDragging = false;
            this.dragOrigin = null;
            this.lastPan = { x: 0, y: 0 };
            this.renderBase();
            this.bindViewportEvents();
        }

        on(eventName, callback) {
            if (!this.listeners[eventName]) {
                this.listeners[eventName] = [];
            }
            this.listeners[eventName].push(callback);
        }

        emit(eventName, payload) {
            for (const callback of this.listeners[eventName] || []) {
                callback(payload);
            }
        }

        destroy() {
            this.svg?.removeEventListener("pointerdown", this.onPointerDownBound);
            this.svg?.removeEventListener("wheel", this.onWheelBound);
            this.svg?.removeEventListener("click", this.onSvgClickBound);
            window.removeEventListener("pointermove", this.onPointerMoveBound);
            window.removeEventListener("pointerup", this.onPointerUpBound);
            this.container.innerHTML = "";
        }

        renderBase() {
            this.container.innerHTML = `
                <svg class="prg-graph-svg" xmlns="http://www.w3.org/2000/svg" aria-label="Relationship graph">
                    <defs>
                        <marker id="${this.uid}_arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                            <path d="M 0 0 L 10 5 L 0 10 z" class="prg-edge-arrow"></path>
                        </marker>
                    </defs>
                    <g class="prg-viewport"></g>
                </svg>
            `;
            this.svg = this.container.querySelector("svg");
            this.viewportEl = this.container.querySelector(".prg-viewport");
        }

        bindViewportEvents() {
            this.onPointerDownBound = this.onPointerDown.bind(this);
            this.onPointerMoveBound = this.onPointerMove.bind(this);
            this.onPointerUpBound = this.onPointerUp.bind(this);
            this.onWheelBound = this.onWheel.bind(this);
            this.onSvgClickBound = this.onSvgClick.bind(this);
            this.svg.addEventListener("pointerdown", this.onPointerDownBound);
            this.svg.addEventListener("wheel", this.onWheelBound, { passive: false });
            this.svg.addEventListener("click", this.onSvgClickBound);
            window.addEventListener("pointermove", this.onPointerMoveBound);
            window.addEventListener("pointerup", this.onPointerUpBound);
        }

        setData(data) {
            this.data = data || { nodes: [], edges: [], meta: {} };
            this.layout = this.computeLayout();
            this.resetViewport();
            this.render();
        }

        setSelection(selection) {
            this.selection = {
                nodeId: selection?.nodeId ?? null,
                edgeId: selection?.edgeId ?? null,
            };
            this.render();
        }

        resetViewport() {
            const width = Math.max(this.container.clientWidth || 960, 960);
            const height = Math.max(this.container.clientHeight || 520, 520);
            this.viewport = {
                scale: 1,
                x: width / 2,
                y: height / 2,
            };
        }

        computeLayout() {
            const nodes = this.data.nodes || [];
            const edges = this.data.edges || [];
            const focalId = this.data.meta?.focal_partner_id || nodes[0]?.id;
            const adjacency = buildAdjacency(nodes, edges);
            const depths = new Map();
            const queue = [];
            if (focalId) {
                depths.set(focalId, 0);
                queue.push(focalId);
            }
            while (queue.length) {
                const current = queue.shift();
                const nextDepth = depths.get(current) + 1;
                for (const neighbor of adjacency.get(current) || []) {
                    if (!depths.has(neighbor)) {
                        depths.set(neighbor, nextDepth);
                        queue.push(neighbor);
                    }
                }
            }
            const levelNodes = new Map();
            for (const node of nodes) {
                const depth = depths.has(node.id) ? depths.get(node.id) : 3;
                if (!levelNodes.has(depth)) {
                    levelNodes.set(depth, []);
                }
                levelNodes.get(depth).push(node);
            }
            const positions = new Map();
            positions.set(focalId, { x: 0, y: 0 });
            for (const [depth, level] of [...levelNodes.entries()].sort((a, b) => a[0] - b[0])) {
                if (depth === 0) {
                    continue;
                }
                const radius = depth === 1 ? 240 : depth === 2 ? 430 : 620;
                const slice = (Math.PI * 2) / Math.max(level.length, 1);
                level
                    .slice()
                    .sort((left, right) => left.display_name.localeCompare(right.display_name))
                    .forEach((node, index) => {
                        const angle = -Math.PI / 2 + slice * index;
                        positions.set(node.id, polarPoint(radius, angle, 0, 0));
                    });
            }
            return positions;
        }

        onPointerDown(event) {
            if (event.target.closest("[data-node-id], [data-edge-id]")) {
                return;
            }
            this.isDragging = true;
            this.dragOrigin = { x: event.clientX, y: event.clientY };
            this.lastPan = { x: this.viewport.x, y: this.viewport.y };
            this.svg.classList.add("is-dragging");
        }

        onPointerMove(event) {
            if (!this.isDragging || !this.dragOrigin) {
                return;
            }
            const dx = event.clientX - this.dragOrigin.x;
            const dy = event.clientY - this.dragOrigin.y;
            this.viewport.x = this.lastPan.x + dx;
            this.viewport.y = this.lastPan.y + dy;
            this.applyViewport();
        }

        onPointerUp() {
            this.isDragging = false;
            this.dragOrigin = null;
            this.svg.classList.remove("is-dragging");
        }

        onWheel(event) {
            event.preventDefault();
            const factor = event.deltaY < 0 ? 1.1 : 0.9;
            this.viewport.scale = Math.max(0.45, Math.min(2.5, this.viewport.scale * factor));
            this.applyViewport();
        }

        onSvgClick(event) {
            if (event.target.closest("[data-node-id], [data-edge-id]")) {
                return;
            }
            this.emit("backgroundclick", {});
        }

        applyViewport() {
            this.viewportEl.setAttribute(
                "transform",
                `translate(${this.viewport.x} ${this.viewport.y}) scale(${this.viewport.scale})`
            );
        }

        render() {
            if (!this.viewportEl) {
                return;
            }
            const nodes = this.data.nodes || [];
            const edges = this.data.edges || [];
            const nodeMarkup = nodes
                .map((node) => this.renderNode(node, this.layout.get(node.id) || { x: 0, y: 0 }))
                .join("");
            const edgeMarkup = edges.map((edge) => this.renderEdge(edge)).join("");
            this.viewportEl.innerHTML = `${edgeMarkup}${nodeMarkup}`;
            this.applyViewport();
            this.bindGraphEvents();
        }

        renderEdge(edge) {
            const source = this.layout.get(edge.source) || { x: 0, y: 0 };
            const target = this.layout.get(edge.target) || { x: 0, y: 0 };
            const midX = (source.x + target.x) / 2;
            const midY = (source.y + target.y) / 2;
            const classes = ["prg-edge", edge.active ? "is-active" : "is-inactive"];
            if (this.selection.edgeId === edge.id) {
                classes.push("is-selected");
            }
            return `
                <g class="${classes.join(" ")}" data-edge-id="${edge.id}">
                    <line class="prg-edge-hitbox" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"></line>
                    <line class="prg-edge-line" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" marker-end="url(#${this.uid}_arrow)"></line>
                    <rect class="prg-edge-label-bg" x="${midX - 52}" y="${midY - 12}" width="104" height="24" rx="12"></rect>
                    <text class="prg-edge-label" x="${midX}" y="${midY + 4}" text-anchor="middle">${escapeHtml(edge.label)}</text>
                </g>
            `;
        }

        renderNode(node, position) {
            const classes = ["prg-node"];
            classes.push(node.is_company ? "is-company" : "is-person");
            if (node.is_focal) {
                classes.push("is-focal");
            }
            if (node.is_expanded) {
                classes.push("is-expanded");
            }
            if (this.selection.nodeId === node.id) {
                classes.push("is-selected");
            }
            const labelY = node.is_company ? 40 : 44;
            const body = node.is_company
                ? `<rect class="prg-node-shape" x="-32" y="-18" width="64" height="36" rx="12"></rect>`
                : `<circle class="prg-node-shape" cx="0" cy="0" r="26"></circle>`;
            return `
                <g class="${classes.join(" ")}" data-node-id="${node.id}" transform="translate(${position.x} ${position.y})">
                    ${body}
                    <text class="prg-node-caption" x="0" y="${labelY}" text-anchor="middle">${escapeHtml(node.display_name)}</text>
                </g>
            `;
        }

        bindGraphEvents() {
            for (const nodeEl of this.viewportEl.querySelectorAll("[data-node-id]")) {
                nodeEl.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const nodeId = Number(nodeEl.dataset.nodeId);
                    if (event.detail >= 2) {
                        this.emit("nodedblclick", { id: nodeId });
                        return;
                    }
                    this.emit("nodeclick", { id: nodeId });
                });
            }
            for (const edgeEl of this.viewportEl.querySelectorAll("[data-edge-id]")) {
                edgeEl.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const edgeId = Number(edgeEl.dataset.edgeId);
                    if (event.detail >= 2) {
                        this.emit("edgedblclick", { id: edgeId });
                        return;
                    }
                    this.emit("edgeclick", { id: edgeId });
                });
            }
        }
    }

    window.PartnerRelationSimpleGraph = PartnerRelationSimpleGraph;
})();
