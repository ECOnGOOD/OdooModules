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

    function normalizeGraphData(data) {
        const inputNodes = data?.nodes || [];
        const inputEdges = data?.edges || [];
        const nodesById = new Map();
        const edgesById = new Map();

        for (const node of inputNodes) {
            const nodeId = Number(node?.id);
            if (!Number.isInteger(nodeId) || nodeId <= 0) {
                continue;
            }
            const existing = nodesById.get(nodeId);
            if (!existing) {
                nodesById.set(nodeId, {
                    ...node,
                    id: nodeId,
                    is_company: Boolean(node.is_company),
                    is_focal: Boolean(node.is_focal),
                    is_expanded: Boolean(node.is_expanded),
                    is_seed: Boolean(node.is_seed),
                });
                continue;
            }
            existing.display_name = existing.display_name || node.display_name;
            existing.is_company = Boolean(existing.is_company || node.is_company);
            existing.is_focal = Boolean(existing.is_focal || node.is_focal);
            existing.is_expanded = Boolean(existing.is_expanded || node.is_expanded);
            existing.is_seed = Boolean(existing.is_seed || node.is_seed);
        }

        for (const edge of inputEdges) {
            const edgeId = Number(edge?.id);
            const sourceId = Number(edge?.source);
            const targetId = Number(edge?.target);
            if (!Number.isInteger(edgeId) || edgeId <= 0) {
                continue;
            }
            if (!nodesById.has(sourceId) || !nodesById.has(targetId)) {
                continue;
            }
            if (!edgesById.has(edgeId)) {
                edgesById.set(edgeId, {
                    ...edge,
                    id: edgeId,
                    source: sourceId,
                    target: targetId,
                    active: edge.active !== false,
                });
            }
        }

        return {
            nodes: [...nodesById.values()],
            edges: [...edgesById.values()],
            meta: data?.meta || {},
        };
    }

    function buildEdgeTracks(edges) {
        const grouped = new Map();
        for (const edge of edges) {
            const pairKey =
                edge.source < edge.target
                    ? `${edge.source}:${edge.target}`
                    : `${edge.target}:${edge.source}`;
            if (!grouped.has(pairKey)) {
                grouped.set(pairKey, []);
            }
            grouped.get(pairKey).push(edge.id);
        }
        const tracks = new Map();
        for (const edgeIds of grouped.values()) {
            edgeIds
                .slice()
                .sort((left, right) => left - right)
                .forEach((edgeId, index, sortedIds) => {
                    const center = (sortedIds.length - 1) / 2;
                    tracks.set(edgeId, {
                        index,
                        total: sortedIds.length,
                        offset: index - center,
                    });
                });
        }
        return tracks;
    }

    function computeParallelPath(source, target, trackOffset) {
        if (source.x === target.x && source.y === target.y) {
            const loopSize = 46 + Math.abs(trackOffset) * 18;
            return {
                path: `M ${source.x} ${source.y} C ${source.x + loopSize} ${source.y - loopSize}, ${source.x - loopSize} ${source.y - loopSize}, ${target.x} ${target.y}`,
                labelX: source.x,
                labelY: source.y - loopSize - 10,
            };
        }
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const distance = Math.hypot(dx, dy) || 1;
        const normalX = -dy / distance;
        const normalY = dx / distance;
        const curveDistance = trackOffset * 28;
        const controlX = (source.x + target.x) / 2 + normalX * curveDistance;
        const controlY = (source.y + target.y) / 2 + normalY * curveDistance;
        const labelX = 0.25 * source.x + 0.5 * controlX + 0.25 * target.x;
        const labelY = 0.25 * source.y + 0.5 * controlY + 0.25 * target.y;

        return {
            path: `M ${source.x} ${source.y} Q ${controlX} ${controlY} ${target.x} ${target.y}`,
            labelX,
            labelY,
        };
    }

    function splitLabelLines(label, maxLineLength = 18, maxLines = 3) {
        const words = String(label || "").trim().split(/\s+/).filter(Boolean);
        if (!words.length) {
            return [""];
        }
        const lines = [];
        let currentLine = "";
        for (const word of words) {
            const nextLine = currentLine ? `${currentLine} ${word}` : word;
            if (nextLine.length <= maxLineLength || !currentLine) {
                currentLine = nextLine;
                continue;
            }
            lines.push(currentLine);
            currentLine = word;
            if (lines.length === maxLines - 1) {
                break;
            }
        }
        if (lines.length < maxLines && currentLine) {
            lines.push(currentLine);
        }
        const consumedWords = lines.join(" ").split(/\s+/).filter(Boolean).length;
        if (consumedWords < words.length) {
            const tail = words.slice(consumedWords).join(" ");
            const lastLine = lines[lines.length - 1] || "";
            const shortened = `${lastLine} ${tail}`.trim().slice(0, maxLineLength - 1).trimEnd();
            lines[lines.length - 1] = `${shortened}…`;
        }
        return lines.slice(0, maxLines);
    }

    function boxIntersects(left, right, padding = 0) {
        return !(
            left.x + left.width + padding < right.x ||
            right.x + right.width + padding < left.x ||
            left.y + left.height + padding < right.y ||
            right.y + right.height + padding < left.y
        );
    }

    function normalizeDirection(x, y) {
        const magnitude = Math.hypot(x, y) || 1;
        return {
            x: x / magnitude,
            y: y / magnitude,
        };
    }

    function pushUniqueCandidate(candidates, candidate) {
        const roundedX = Math.round(candidate.x);
        const roundedY = Math.round(candidate.y);
        if (
            candidates.findIndex(
                (item) => Math.round(item.x) === roundedX && Math.round(item.y) === roundedY
            ) === -1
        ) {
            candidates.push(candidate);
        }
    }

    function buildLabelCandidates(position, isFocal) {
        const radii = isFocal ? [62, 86, 114, 146] : [58, 82, 106, 132];
        const horizontalSign = position.x === 0 ? 1 : Math.sign(position.x);
        const verticalSign = position.y === 0 ? 1 : Math.sign(position.y);
        const mostlyHorizontal = Math.abs(position.x) >= Math.abs(position.y);
        const priorityVectors = isFocal
            ? [
                  { x: 0, y: 1 },
                  { x: 0, y: -1 },
                  { x: 1, y: 0 },
                  { x: -1, y: 0 },
                  { x: 0.86, y: 0.5 },
                  { x: -0.86, y: 0.5 },
                  { x: 0.86, y: -0.5 },
                  { x: -0.86, y: -0.5 },
              ]
            : mostlyHorizontal
              ? [
                    { x: horizontalSign, y: 0 },
                    { x: horizontalSign, y: verticalSign * 0.66 },
                    { x: horizontalSign, y: -verticalSign * 0.66 },
                    { x: 0, y: verticalSign },
                    { x: 0, y: -verticalSign },
                    { x: -horizontalSign, y: 0 },
                    { x: -horizontalSign, y: verticalSign * 0.66 },
                    { x: -horizontalSign, y: -verticalSign * 0.66 },
                ]
              : [
                    { x: 0, y: verticalSign },
                    { x: horizontalSign * 0.66, y: verticalSign },
                    { x: -horizontalSign * 0.66, y: verticalSign },
                    { x: horizontalSign, y: 0 },
                    { x: -horizontalSign, y: 0 },
                    { x: 0, y: -verticalSign },
                    { x: horizontalSign * 0.66, y: -verticalSign },
                    { x: -horizontalSign * 0.66, y: -verticalSign },
                ];

        const candidates = [];
        for (const vector of priorityVectors) {
            const direction = normalizeDirection(vector.x, vector.y);
            for (const radius of radii) {
                pushUniqueCandidate(candidates, {
                    x: direction.x * radius,
                    y: direction.y * radius,
                });
            }
        }
        return candidates;
    }

    function buildNodeFootprint(node, position) {
        const padding = 10;
        if (node.is_company) {
            return {
                x: position.x - 32 - padding,
                y: position.y - 18 - padding,
                width: 64 + padding * 2,
                height: 36 + padding * 2,
            };
        }
        return {
            x: position.x - 26 - padding,
            y: position.y - 26 - padding,
            width: 52 + padding * 2,
            height: 52 + padding * 2,
        };
    }

    function toNumberAttribute(element, attributeName) {
        return Number(element.getAttribute(attributeName) || 0);
    }

    class PartnerRelationSimpleGraph {
        constructor(container) {
            this.container = container;
            this.uid = `partner_relation_graph_${++graphCounter}`;
            this.listeners = {};
            this.data = { nodes: [], edges: [], meta: {} };
            this.selection = { nodeId: null, edgeId: null };
            this.viewport = { scale: 1, x: 0, y: 0 };
            this.edgeTracks = new Map();
            this.manualNodePositions = new Map();
            this.currentFocalId = null;
            this.nodeDrag = null;
            this.suppressedNodeClick = { id: null, until: 0 };
            this.isDragging = false;
            this.dragOrigin = null;
            this.lastPan = { x: 0, y: 0 };
            this.renderToken = null;
            this.activePointerId = null;
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
            if (this.renderToken) {
                window.cancelAnimationFrame(this.renderToken);
            }
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

        pruneManualPositions() {
            const activeIds = new Set((this.data.nodes || []).map((node) => node.id));
            for (const nodeId of this.manualNodePositions.keys()) {
                if (!activeIds.has(nodeId)) {
                    this.manualNodePositions.delete(nodeId);
                }
            }
        }

        setData(data) {
            this.data = normalizeGraphData(data);
            this.pruneManualPositions();
            this.layout = this.computeLayout();
            this.edgeTracks = buildEdgeTracks(this.data.edges || []);
            const focalId = this.data.meta?.focal_partner_id || null;
            if (this.currentFocalId !== focalId) {
                this.resetViewport();
                this.currentFocalId = focalId;
            }
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
            for (const [nodeId, position] of this.manualNodePositions.entries()) {
                positions.set(nodeId, { ...position });
            }
            return positions;
        }

        scheduleRender() {
            if (this.renderToken) {
                return;
            }
            this.renderToken = window.requestAnimationFrame(() => {
                this.renderToken = null;
                this.render();
            });
        }

        onPointerDown(event) {
            if (typeof event.button === "number" && event.button > 0) {
                return;
            }
            const nodeEl = event.target.closest("[data-node-id]");
            if (nodeEl) {
                const nodeId = Number(nodeEl.dataset.nodeId);
                const startPosition = this.layout.get(nodeId) || { x: 0, y: 0 };
                this.nodeDrag = {
                    nodeId,
                    originClientX: event.clientX,
                    originClientY: event.clientY,
                    originX: startPosition.x,
                    originY: startPosition.y,
                    moved: false,
                };
                this.activePointerId = event.pointerId;
                this.svg.classList.add("is-node-dragging");
                try {
                    this.svg.setPointerCapture?.(event.pointerId);
                } catch {
                    // Synthetic test events may not have an active browser pointer to capture.
                }
                return;
            }
            if (event.target.closest("[data-edge-id]")) {
                return;
            }
            event.preventDefault();
            this.isDragging = true;
            this.dragOrigin = { x: event.clientX, y: event.clientY };
            this.lastPan = { x: this.viewport.x, y: this.viewport.y };
            this.activePointerId = event.pointerId;
            this.svg.classList.add("is-dragging");
            try {
                this.svg.setPointerCapture?.(event.pointerId);
            } catch {
                // Synthetic test events may not have an active browser pointer to capture.
            }
        }

        onPointerMove(event) {
            if (this.activePointerId !== null && event.pointerId !== this.activePointerId) {
                return;
            }
            if (this.nodeDrag) {
                const dx = (event.clientX - this.nodeDrag.originClientX) / this.viewport.scale;
                const dy = (event.clientY - this.nodeDrag.originClientY) / this.viewport.scale;
                if (Math.hypot(dx, dy) > 6) {
                    this.nodeDrag.moved = true;
                }
                const nextPosition = {
                    x: this.nodeDrag.originX + dx,
                    y: this.nodeDrag.originY + dy,
                };
                this.manualNodePositions.set(this.nodeDrag.nodeId, nextPosition);
                this.layout.set(this.nodeDrag.nodeId, nextPosition);
                this.scheduleRender();
                return;
            }
            if (!this.isDragging || !this.dragOrigin) {
                return;
            }
            const dx = event.clientX - this.dragOrigin.x;
            const dy = event.clientY - this.dragOrigin.y;
            this.viewport.x = this.lastPan.x + dx;
            this.viewport.y = this.lastPan.y + dy;
            this.applyViewport();
        }

        onPointerUp(event) {
            if (this.activePointerId !== null && event.pointerId !== this.activePointerId) {
                return;
            }
            if (this.nodeDrag) {
                const nodeId = this.nodeDrag.nodeId;
                if (this.nodeDrag.moved) {
                    this.suppressedNodeClick = {
                        id: nodeId,
                        until: Date.now() + 250,
                    };
                } else {
                    this.suppressedNodeClick = {
                        id: nodeId,
                        until: Date.now() + 250,
                    };
                    this.emit("nodeclick", { id: nodeId });
                }
                this.nodeDrag = null;
                this.svg.classList.remove("is-node-dragging");
            }
            this.isDragging = false;
            this.dragOrigin = null;
            this.activePointerId = null;
            this.svg.classList.remove("is-dragging");
            try {
                this.svg.releasePointerCapture?.(event.pointerId);
            } catch {
                // Synthetic test events may not have an active browser pointer to release.
            }
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
            const edgeMarkup = edges.map((edge) => this.renderEdge(edge)).join("");
            const nodeMarkup = nodes
                .map((node) => this.renderNode(node, this.layout.get(node.id) || { x: 0, y: 0 }))
                .join("");
            this.viewportEl.innerHTML = `${edgeMarkup}${nodeMarkup}`;
            this.sizeEdgeLabels();
            this.positionNodeLabels();
            this.applyViewport();
            this.bindGraphEvents();
        }

        sizeEdgeLabels() {
            for (const edgeEl of this.viewportEl.querySelectorAll("[data-edge-id]")) {
                const labelEl = edgeEl.querySelector(".prg-edge-label");
                const backgroundEl = edgeEl.querySelector(".prg-edge-label-bg");
                if (!labelEl || !backgroundEl) {
                    continue;
                }
                const bbox = labelEl.getBBox();
                backgroundEl.setAttribute("x", String(bbox.x - 8));
                backgroundEl.setAttribute("y", String(bbox.y - 4));
                backgroundEl.setAttribute("width", String(bbox.width + 16));
                backgroundEl.setAttribute("height", String(bbox.height + 8));
            }
        }

        positionNodeLabels() {
            for (const nodeEl of this.viewportEl.querySelectorAll("[data-node-id]")) {
                const labelGroup = nodeEl.querySelector(".prg-node-label");
                const textEl = nodeEl.querySelector(".prg-node-caption");
                const backgroundEl = nodeEl.querySelector(".prg-node-label-bg");
                if (!labelGroup || !textEl || !backgroundEl) {
                    continue;
                }
                const bbox = textEl.getBBox();
                const localBox = {
                    x: bbox.x - 10,
                    y: bbox.y - 7,
                    width: bbox.width + 20,
                    height: bbox.height + 14,
                };
                backgroundEl.setAttribute("x", String(localBox.x));
                backgroundEl.setAttribute("y", String(localBox.y));
                backgroundEl.setAttribute("width", String(localBox.width));
                backgroundEl.setAttribute("height", String(localBox.height));
                labelGroup.setAttribute("transform", "translate(0 0)");
            }
        }

        renderEdge(edge) {
            const source = this.layout.get(edge.source) || { x: 0, y: 0 };
            const target = this.layout.get(edge.target) || { x: 0, y: 0 };
            const track = this.edgeTracks.get(edge.id) || { offset: 0 };
            const curve = computeParallelPath(source, target, track.offset);
            const classes = ["prg-edge", edge.active ? "is-active" : "is-inactive"];
            if (this.selection.edgeId === edge.id) {
                classes.push("is-selected");
            }
            return `
                <g class="${classes.join(" ")}" data-edge-id="${edge.id}">
                    <path class="prg-edge-hitbox" d="${curve.path}"></path>
                    <path class="prg-edge-line" d="${curve.path}" marker-end="url(#${this.uid}_arrow)"></path>
                    <rect class="prg-edge-label-bg" rx="12"></rect>
                    <text class="prg-edge-label" x="${curve.labelX}" y="${curve.labelY + 4}" text-anchor="middle">${escapeHtml(edge.label)}</text>
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
            if (this.nodeDrag?.nodeId === node.id) {
                classes.push("is-dragging");
            }
            const body = node.is_company
                ? `<rect class="prg-node-shape" x="-32" y="-18" width="64" height="36" rx="12"></rect>`
                : `<circle class="prg-node-shape" cx="0" cy="0" r="26"></circle>`;
            const lines = splitLabelLines(node.display_name);
            const lineHeight = 14;
            const startY = lines.length === 1 ? 0 : -((lines.length - 1) * lineHeight) / 2;
            const textMarkup = lines
                .map(
                    (line, index) =>
                        `<tspan x="0" y="${startY + index * lineHeight}">${escapeHtml(line)}</tspan>`
                )
                .join("");
            return `
                <g class="${classes.join(" ")}" data-node-id="${node.id}" transform="translate(${position.x} ${position.y})">
                    ${body}
                    <g class="prg-node-label" data-node-label-for="${node.id}">
                        <rect class="prg-node-label-bg" rx="10" ry="10"></rect>
                        <text class="prg-node-caption" text-anchor="middle">${textMarkup}</text>
                    </g>
                </g>
            `;
        }

        bindGraphEvents() {
            for (const nodeEl of this.viewportEl.querySelectorAll("[data-node-id]")) {
                nodeEl.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const nodeId = Number(nodeEl.dataset.nodeId);
                    if (
                        this.suppressedNodeClick.id === nodeId &&
                        Date.now() < this.suppressedNodeClick.until
                    ) {
                        return;
                    }
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
