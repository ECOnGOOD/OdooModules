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
            if (!Number.isInteger(edgeId) || edgeId === 0) {
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

    function getNodeVectorOffset(node, direction, extra = 0) {
        if (node?.is_company) {
            const halfWidth = 32 + extra;
            const halfHeight = 18 + extra;
            const absX = Math.abs(direction.x) || 0.0001;
            const absY = Math.abs(direction.y) || 0.0001;
            const scale = Math.min(halfWidth / absX, halfHeight / absY);
            return {
                x: direction.x * scale,
                y: direction.y * scale,
            };
        }
        const radius = 26 + extra;
        return {
            x: direction.x * radius,
            y: direction.y * radius,
        };
    }

    function computeParallelPath(source, target, trackOffset, sourceNode = null, targetNode = null) {
        if (source.x === target.x && source.y === target.y) {
            const loopSize = 46 + Math.abs(trackOffset) * 18;
            const direction = normalizeDirection(0.72, -1);
            const endX = source.x + loopSize * 0.12;
            const endY = source.y - loopSize * 0.76;
            return {
                path: `M ${source.x} ${source.y} C ${source.x + loopSize} ${source.y - loopSize}, ${source.x - loopSize} ${source.y - loopSize}, ${target.x} ${target.y}`,
                labelX: source.x,
                labelY: source.y - loopSize - 10,
                startX: source.x,
                startY: source.y,
                endX,
                endY,
                direction,
            };
        }
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const distance = Math.hypot(dx, dy) || 1;
        const normalX = -dy / distance;
        const normalY = dx / distance;
        const offsetDistance = trackOffset * 22;
        const offsetX = normalX * offsetDistance;
        const offsetY = normalY * offsetDistance;
        const baseStartX = source.x + offsetX;
        const baseStartY = source.y + offsetY;
        const baseEndX = target.x + offsetX;
        const baseEndY = target.y + offsetY;
        const lineDirection = normalizeDirection(baseEndX - baseStartX, baseEndY - baseStartY);
        const sourceOffset = getNodeVectorOffset(sourceNode, lineDirection, 5);
        const targetOffset = getNodeVectorOffset(
            targetNode,
            { x: -lineDirection.x, y: -lineDirection.y },
            16
        );
        const startX = baseStartX + sourceOffset.x;
        const startY = baseStartY + sourceOffset.y;
        const endX = baseEndX + targetOffset.x;
        const endY = baseEndY + targetOffset.y;
        const labelX = (startX + endX) / 2;
        const labelY = (startY + endY) / 2 - 8;

        return {
            path: `M ${startX} ${startY} L ${endX} ${endY}`,
            labelX,
            labelY,
            startX,
            startY,
            endX,
            endY,
            direction: lineDirection,
        };
    }

    function buildArrowHeadPoints(curve, size = 12, width = 7) {
        const direction = curve.direction || normalizeDirection(curve.endX - curve.startX, curve.endY - curve.startY);
        const baseX = curve.endX - direction.x * size;
        const baseY = curve.endY - direction.y * size;
        const perpendicular = { x: -direction.y, y: direction.x };
        const leftX = baseX + perpendicular.x * width;
        const leftY = baseY + perpendicular.y * width;
        const rightX = baseX - perpendicular.x * width;
        const rightY = baseY - perpendicular.y * width;
        return `${curve.endX},${curve.endY} ${leftX},${leftY} ${rightX},${rightY}`;
    }

    function resolveEdgeDirection(edge, selectedNodeId, focalNodeId) {
        const edgeTouchesSelectedNode =
            selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);
        const edgeTouchesFocalNode =
            focalNodeId && (edge.source === focalNodeId || edge.target === focalNodeId);

        let preferredSourceId = null;
        if (edgeTouchesSelectedNode) {
            preferredSourceId = selectedNodeId;
        } else if (edgeTouchesFocalNode) {
            preferredSourceId = focalNodeId;
        }

        if (preferredSourceId && edge.target === preferredSourceId) {
            return {
                sourceId: edge.target,
                targetId: edge.source,
                label: edge.inverse_label || edge.label,
            };
        }
        return {
            sourceId: edge.source,
            targetId: edge.target,
            label: edge.label,
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

    function estimateNodeExtent(node) {
        const lines = splitLabelLines(node?.display_name || "");
        const longestLine = lines.reduce((width, line) => Math.max(width, line.length), 0);
        const labelWidth = longestLine * 7.2 + 32;
        const labelHeight = lines.length * 14 + 24;
        const shapeWidth = node?.is_company ? 78 : 64;
        const shapeHeight = node?.is_company ? 48 : 64;
        return Math.max(shapeWidth, shapeHeight, labelWidth, labelHeight);
    }

    function estimateLevelSpacing(nodes) {
        if (!nodes?.length) {
            return 108;
        }
        const averageExtent =
            nodes.reduce((sum, node) => sum + estimateNodeExtent(node), 0) / nodes.length;
        return Math.max(104, Math.min(176, averageExtent * 0.96));
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
            this.nodeById = new Map();
            this.manualNodePositions = new Map();
            this.currentFocalId = null;
            this.nodeDrag = null;
            this.suppressedNodeClick = { id: null, until: 0 };
            this.suppressedBackgroundClickUntil = 0;
            this.isDragging = false;
            this.dragOrigin = null;
            this.lastPan = { x: 0, y: 0 };
            this.renderToken = null;
            this.activePointerId = null;
            this.pendingViewportRestore = null;
            this.lastMeasuredSize = null;
            this.resizeObserver = null;
            this.renderBase();
            this.bindViewportEvents();
            this.bindResizeObserver();
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
            window.removeEventListener("resize", this.onWindowResizeBound);
            this.resizeObserver?.disconnect();
            if (this.renderToken) {
                window.cancelAnimationFrame(this.renderToken);
            }
            this.container.innerHTML = "";
        }

        cloneViewState() {
            const viewportCenter = this.getViewportCenterWorld();
            return {
                viewport: {
                    ...this.viewport,
                    center_world_x: viewportCenter.x,
                    center_world_y: viewportCenter.y,
                },
                currentFocalId: this.currentFocalId,
                layout: [...(this.layout || new Map()).entries()].map(([nodeId, position]) => ({
                    id: nodeId,
                    x: position.x,
                    y: position.y,
                })),
                manualNodePositions: [...this.manualNodePositions.entries()].map(([nodeId, position]) => ({
                    id: nodeId,
                    x: position.x,
                    y: position.y,
                })),
            };
        }

        getViewportCenterWorld() {
            const { width, height } = this.lastMeasuredSize || this.measureContainer();
            const scale = this.viewport.scale || 1;
            return {
                x: (width / 2 - this.viewport.x) / scale,
                y: (height / 2 - this.viewport.y) / scale,
            };
        }

        applyViewportRestore(viewportState) {
            if (!viewportState || typeof viewportState !== "object") {
                return;
            }
            const scale = Number(viewportState.scale);
            if (!Number.isFinite(scale)) {
                return;
            }
            const centerWorldX = Number(viewportState.center_world_x);
            const centerWorldY = Number(viewportState.center_world_y);
            if (Number.isFinite(centerWorldX) && Number.isFinite(centerWorldY)) {
                const { width, height } = this.measureContainer();
                this.lastMeasuredSize = { width, height };
                this.viewport = {
                    scale,
                    x: width / 2 - centerWorldX * scale,
                    y: height / 2 - centerWorldY * scale,
                };
                return;
            }
            const x = Number(viewportState.x);
            const y = Number(viewportState.y);
            if (Number.isFinite(x) && Number.isFinite(y)) {
                this.viewport = { scale, x, y };
            }
        }

        publishViewState() {
            this.emit("viewstatechange", this.cloneViewState());
        }

        restoreState(viewState) {
            if (!viewState || typeof viewState !== "object") {
                return;
            }
            const parsePositions = (items) => {
                const positions = new Map();
                for (const item of items || []) {
                    const nodeId = Number(item?.id);
                    const x = Number(item?.x);
                    const y = Number(item?.y);
                    if (!Number.isInteger(nodeId) || !Number.isFinite(x) || !Number.isFinite(y)) {
                        continue;
                    }
                    positions.set(nodeId, { x, y });
                }
                return positions;
            };
            this.layout = parsePositions(viewState.layout);
            this.manualNodePositions = parsePositions(viewState.manualNodePositions);
            this.pendingViewportRestore = viewState.viewport || null;
            const focalId = Number(viewState.currentFocalId);
            this.currentFocalId = Number.isInteger(focalId) && focalId > 0 ? focalId : null;
        }

        renderBase() {
            this.container.innerHTML = `
                <svg class="prg-graph-svg" xmlns="http://www.w3.org/2000/svg" aria-label="Relationship graph">
                    <g class="prg-viewport"></g>
                </svg>
            `;
            this.svg = this.container.querySelector("svg");
            this.viewportEl = this.container.querySelector(".prg-viewport");
            this.lastMeasuredSize = this.measureContainer();
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

        bindResizeObserver() {
            this.onWindowResizeBound = this.onResize.bind(this);
            window.addEventListener("resize", this.onWindowResizeBound);
            if (typeof window.ResizeObserver === "function") {
                this.resizeObserver = new window.ResizeObserver(() => this.onResize());
                this.resizeObserver.observe(this.container);
            }
        }

        measureContainer() {
            return {
                width: Math.max(this.container.clientWidth || 320, 320),
                height: Math.max(this.container.clientHeight || 320, 320),
            };
        }

        onResize() {
            const nextSize = this.measureContainer();
            const previousSize = this.lastMeasuredSize || nextSize;
            if (
                nextSize.width === previousSize.width &&
                nextSize.height === previousSize.height
            ) {
                return;
            }
            const scale = this.viewport.scale || 1;
            const centerWorldX = (previousSize.width / 2 - this.viewport.x) / scale;
            const centerWorldY = (previousSize.height / 2 - this.viewport.y) / scale;
            this.lastMeasuredSize = nextSize;
            this.viewport = {
                scale,
                x: nextSize.width / 2 - centerWorldX * scale,
                y: nextSize.height / 2 - centerWorldY * scale,
            };
            this.scheduleRender();
            this.publishViewState();
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
            const previousLayout = this.layout ? new Map(this.layout) : new Map();
            this.data = normalizeGraphData(data);
            this.pruneManualPositions();
            this.layout = this.computeLayout(previousLayout);
            this.edgeTracks = buildEdgeTracks(this.data.edges || []);
            this.nodeById = new Map((this.data.nodes || []).map((node) => [node.id, node]));
            const focalId = this.data.meta?.focal_partner_id || null;
            if (this.currentFocalId !== focalId) {
                this.currentFocalId = focalId;
                this.fitViewportToLayout();
            }
            if (this.pendingViewportRestore) {
                this.applyViewportRestore(this.pendingViewportRestore);
                this.pendingViewportRestore = null;
            }
            this.render();
            this.publishViewState();
        }

        setSelection(selection) {
            this.selection = {
                nodeId: selection?.nodeId ?? null,
                edgeId: selection?.edgeId ?? null,
            };
            this.render();
        }

        resetViewport() {
            const { width, height } = this.measureContainer();
            this.lastMeasuredSize = { width, height };
            this.viewport = {
                scale: 1,
                x: width / 2,
                y: height / 2,
            };
        }

        fitViewportToLayout() {
            const nodes = this.data.nodes || [];
            if (!nodes.length) {
                this.resetViewport();
                return;
            }
            const { width, height } = this.measureContainer();
            this.lastMeasuredSize = { width, height };
            const paddingX = Math.max(56, width * 0.08);
            const paddingY = Math.max(56, height * 0.1);
            let minX = Infinity;
            let minY = Infinity;
            let maxX = -Infinity;
            let maxY = -Infinity;
            for (const node of nodes) {
                const position = this.layout.get(node.id) || { x: 0, y: 0 };
                const extent = estimateNodeExtent(node) / 2;
                minX = Math.min(minX, position.x - extent);
                maxX = Math.max(maxX, position.x + extent);
                minY = Math.min(minY, position.y - extent);
                maxY = Math.max(maxY, position.y + extent);
            }
            const boundsWidth = Math.max(1, maxX - minX);
            const boundsHeight = Math.max(1, maxY - minY);
            const scale = Math.max(
                0.6,
                Math.min(
                    1.85,
                    (width - paddingX * 2) / boundsWidth,
                    (height - paddingY * 2) / boundsHeight
                )
            );
            const centerX = (minX + maxX) / 2;
            const centerY = (minY + maxY) / 2;
            this.viewport = {
                scale,
                x: width / 2 - centerX * scale,
                y: height / 2 - centerY * scale,
            };
        }

        computeLayout(previousLayout = new Map()) {
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
            const width = Math.max(this.container.clientWidth || 320, 320);
            const height = Math.max(this.container.clientHeight || 320, 320);
            const baseRadius = Math.min(260, Math.max(118, Math.min(width * 0.28, height * 0.24)));
            const radiusStep = Math.max(136, Math.min(228, Math.round(Math.min(width, height) * 0.24)));
            const positions = new Map();
            const knownPositions = new Map(previousLayout || []);
            if (focalId && knownPositions.has(focalId)) {
                positions.set(focalId, { ...knownPositions.get(focalId) });
            } else if (focalId) {
                positions.set(focalId, { x: 0, y: 0 });
            }
            for (const [nodeId, position] of this.manualNodePositions.entries()) {
                knownPositions.set(nodeId, { ...position });
                positions.set(nodeId, { ...position });
            }
            for (const [depth, level] of [...levelNodes.entries()].sort((left, right) => left[0] - right[0])) {
                if (!level.length) {
                    continue;
                }
                const levelSpacing = estimateLevelSpacing(level);
                const requiredRadius =
                    level.length <= 1 ? 0 : (level.length * levelSpacing) / (Math.PI * 2);
                const radius =
                    depth === 0
                        ? 0
                        : Math.max(baseRadius + Math.max(0, depth - 1) * radiusStep, requiredRadius);
                const missingNodes = level
                    .slice()
                    .sort((left, right) => left.display_name.localeCompare(right.display_name))
                    .filter((node) => !positions.has(node.id) && !knownPositions.has(node.id));
                const slice = (Math.PI * 2) / Math.max(missingNodes.length, 1);
                missingNodes.forEach((node, index) => {
                    const positionedNeighbors = [...(adjacency.get(node.id) || [])]
                        .map((neighborId) => positions.get(neighborId) || knownPositions.get(neighborId))
                        .filter(Boolean);
                    if (positionedNeighbors.length) {
                        const anchor = positionedNeighbors.reduce(
                            (accumulator, position) => ({
                                x: accumulator.x + position.x,
                                y: accumulator.y + position.y,
                            }),
                            { x: 0, y: 0 }
                        );
                        const centerX = anchor.x / positionedNeighbors.length;
                        const centerY = anchor.y / positionedNeighbors.length;
                        const outwardAngle =
                            Math.abs(centerX) < 1 && Math.abs(centerY) < 1
                                ? -Math.PI / 2
                                : Math.atan2(centerY, centerX);
                        const arcSpan =
                            missingNodes.length <= 1
                                ? 0
                                : Math.min(Math.PI * 1.5, 0.95 + (missingNodes.length - 1) * 0.42);
                        const localSpacing = estimateLevelSpacing(missingNodes);
                        const localRadius = Math.max(
                            104,
                            Math.min(
                                radiusStep * 1.28,
                                (missingNodes.length * localSpacing) /
                                    Math.max(Math.PI * 0.92, arcSpan || Math.PI * 0.92)
                            )
                        );
                        const angle =
                            missingNodes.length <= 1
                                ? outwardAngle
                                : outwardAngle - arcSpan / 2 + (arcSpan / (missingNodes.length - 1)) * index;
                        positions.set(node.id, polarPoint(localRadius, angle, centerX, centerY));
                        return;
                    }
                    const angle = -Math.PI / 2 + slice * index;
                    positions.set(node.id, polarPoint(radius, angle, 0, 0));
                });
                for (const node of level) {
                    if (!positions.has(node.id) && knownPositions.has(node.id)) {
                        positions.set(node.id, { ...knownPositions.get(node.id) });
                    }
                }
            }
            for (const node of nodes) {
                if (!positions.has(node.id) && knownPositions.has(node.id)) {
                    positions.set(node.id, { ...knownPositions.get(node.id) });
                }
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
            const didPan = this.isDragging;
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
                    this.suppressedBackgroundClickUntil = Date.now() + 250;
                    this.emit("nodeclick", { id: nodeId });
                }
                this.nodeDrag = null;
                this.svg.classList.remove("is-node-dragging");
                this.publishViewState();
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
            if (didPan) {
                this.publishViewState();
            }
        }

        onWheel(event) {
            event.preventDefault();
            const factor = event.deltaY < 0 ? 1.1 : 0.9;
            this.viewport.scale = Math.max(0.45, Math.min(2.5, this.viewport.scale * factor));
            this.applyViewport();
            this.publishViewState();
        }

        onSvgClick(event) {
            if (Date.now() < this.suppressedBackgroundClickUntil) {
                return;
            }
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
            const directedEdge = resolveEdgeDirection(
                edge,
                this.selection.nodeId,
                this.data.meta?.focal_partner_id || null
            );
            const source = this.layout.get(directedEdge.sourceId) || { x: 0, y: 0 };
            const target = this.layout.get(directedEdge.targetId) || { x: 0, y: 0 };
            const sourceNode = this.nodeById.get(directedEdge.sourceId) || null;
            const targetNode = this.nodeById.get(directedEdge.targetId) || null;
            const track = this.edgeTracks.get(edge.id) || { offset: 0 };
            const curve = computeParallelPath(source, target, track.offset, sourceNode, targetNode);
            const arrowPoints = buildArrowHeadPoints(curve);
            const classes = ["prg-edge", edge.active ? "is-active" : "is-inactive", `is-${edge.kind || "relation"}`];
            if (this.selection.edgeId === edge.id) {
                classes.push("is-selected");
            }
            return `
                <g class="${classes.join(" ")}" data-edge-id="${edge.id}">
                    <path class="prg-edge-hitbox" d="${curve.path}"></path>
                    <path class="prg-edge-line" d="${curve.path}"></path>
                    <polygon class="prg-edge-arrow-head" points="${arrowPoints}"></polygon>
                    <rect class="prg-edge-label-bg" rx="12"></rect>
                    <text class="prg-edge-label" x="${curve.labelX}" y="${curve.labelY + 4}" text-anchor="middle">${escapeHtml(directedEdge.label)}</text>
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
                    this.emit("nodeclick", { id: nodeId });
                });
                nodeEl.addEventListener("dblclick", (event) => {
                    event.stopPropagation();
                    const nodeId = Number(nodeEl.dataset.nodeId);
                    this.suppressedNodeClick = { id: null, until: 0 };
                    this.suppressedBackgroundClickUntil = Date.now() + 250;
                    this.emit("nodedblclick", { id: nodeId });
                });
            }
            for (const edgeEl of this.viewportEl.querySelectorAll("[data-edge-id]")) {
                edgeEl.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const edgeId = Number(edgeEl.dataset.edgeId);
                    this.emit("edgeclick", { id: edgeId });
                });
                edgeEl.addEventListener("dblclick", (event) => {
                    event.stopPropagation();
                    const edgeId = Number(edgeEl.dataset.edgeId);
                    this.emit("edgedblclick", { id: edgeId });
                });
            }
        }
    }

    window.PartnerRelationSimpleGraph = PartnerRelationSimpleGraph;
})();
