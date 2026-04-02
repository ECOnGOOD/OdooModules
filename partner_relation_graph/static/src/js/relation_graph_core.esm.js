/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { RecordAutocomplete } from "@web/core/record_selectors/record_autocomplete";
import { useService } from "@web/core/utils/hooks";

import {
    Component,
    onMounted,
    onWillStart,
    onWillUnmount,
    onWillUpdateProps,
    useEffect,
    useRef,
    useState,
} from "@odoo/owl";

const GraphLibrary = window.PartnerRelationSimpleGraph;

function makeEmptyGraph(partnerId = false) {
    return {
        nodes: [],
        edges: [],
        meta: {
            focal_partner_id: partnerId || false,
            expanded_partner_ids: [],
            total_node_count: 0,
            total_edge_count: 0,
            truncated: false,
        },
    };
}

function toSingleId(value) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : false;
}

function toEdgeId(value) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed !== 0 ? parsed : false;
}

function toIdList(values) {
    return (values || [])
        .map((value) => Number(value))
        .filter(
            (value, index, list) =>
                Number.isInteger(value) && value > 0 && list.indexOf(value) === index
        );
}

function toFilterIdList(values) {
    return (values || [])
        .map((value) => Number(value))
        .filter(
            (value, index, list) =>
                Number.isInteger(value) && value !== 0 && list.indexOf(value) === index
        );
}

function normalizeInitialGraphState(value) {
    if (!value) {
        return null;
    }
    let state = value;
    if (typeof state === "string") {
        try {
            state = JSON.parse(state);
        } catch {
            return null;
        }
    }
    if (!state || typeof state !== "object") {
        return null;
    }
    return {
        partnerId: toSingleId(state.partnerId),
        includeInactive: Boolean(state.includeInactive),
        showChildContacts:
            state.showChildContacts === undefined ? true : Boolean(state.showChildContacts),
        relationTypeIds: toFilterIdList(state.relationTypeIds),
        expandedPartnerIds: toIdList(state.expandedPartnerIds),
        selectedNodeId: toSingleId(state.selectedNodeId),
        selectedEdgeId: toEdgeId(state.selectedEdgeId),
        graphViewState: state.graphViewState && typeof state.graphViewState === "object"
            ? state.graphViewState
            : null,
    };
}

export class RelationGraphCanvas extends Component {
    static template = "partner_relation_graph.RelationGraphCanvas";
    static props = {
        graphData: Object,
        selectedNodeId: { type: Number, optional: true },
        selectedEdgeId: { type: Number, optional: true },
        viewState: { type: Object, optional: true },
        onNodeSelect: { type: Function, optional: true },
        onEdgeSelect: { type: Function, optional: true },
        onEdgeOpen: { type: Function, optional: true },
        onBackgroundClick: { type: Function, optional: true },
        onViewStateChange: { type: Function, optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.lastGraphData = null;
        onMounted(() => {
            if (!GraphLibrary) {
                return;
            }
            this.graph = new GraphLibrary(this.canvasRef.el);
            this.graph.on("nodeclick", ({ id }) => this.props.onNodeSelect?.(id));
            this.graph.on("edgeclick", ({ id }) => this.props.onEdgeSelect?.(id));
            this.graph.on("edgedblclick", ({ id }) => this.props.onEdgeOpen?.(id));
            this.graph.on("backgroundclick", () => this.props.onBackgroundClick?.());
            this.graph.on("viewstatechange", (viewState) => this.props.onViewStateChange?.(viewState));
            if (this.props.viewState) {
                this.graph.restoreState(this.props.viewState);
            }
            this.syncGraph();
        });
        useEffect(
            () => {
                this.syncGraph();
            },
            () => [this.props.graphData, this.props.selectedNodeId, this.props.selectedEdgeId]
        );
        onWillUnmount(() => {
            this.graph?.destroy();
        });
    }

    syncGraph() {
        if (!this.graph) {
            return;
        }
        if (this.lastGraphData !== this.props.graphData) {
            this.graph.setData(this.props.graphData);
            this.lastGraphData = this.props.graphData;
        }
        this.graph.setSelection({
            nodeId: this.props.selectedNodeId,
            edgeId: this.props.selectedEdgeId,
        });
    }
}

export class RelationGraphExplorer extends Component {
    static template = "partner_relation_graph.RelationGraphExplorer";
    static components = {
        RecordAutocomplete,
        RelationGraphCanvas,
    };
    static props = {
        seedPartnerId: { type: Number, optional: true },
        standalone: { type: Boolean, optional: true },
        initialGraphState: { type: Object, optional: true },
    };
    static defaultProps = {
        standalone: false,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.lastNodeInteraction = { id: false, at: 0 };
        this.initialGraphState = normalizeInitialGraphState(this.props.initialGraphState);
        const initialPartnerId = this.initialGraphState?.partnerId || toSingleId(this.props.seedPartnerId);
        this.state = useState({
            partnerId: initialPartnerId,
            includeInactive: Boolean(this.initialGraphState?.includeInactive),
            relationTypeIds: this.initialGraphState?.relationTypeIds || [],
            showChildContacts: this.initialGraphState?.showChildContacts ?? true,
            relationTypes: [],
            graphData: makeEmptyGraph(initialPartnerId),
            loading: false,
            error: "",
            selectedNodeId: this.initialGraphState?.selectedNodeId || false,
            selectedEdgeId: this.initialGraphState?.selectedEdgeId || false,
            expandedPartnerIds: this.initialGraphState?.expandedPartnerIds || [],
            graphViewState: this.initialGraphState?.graphViewState || null,
        });

        onWillStart(async () => {
            await this.loadRelationTypes();
            if (this.state.partnerId) {
                await this.reloadGraph();
            }
        });

        onWillUpdateProps(async (nextProps) => {
            if (this.props.standalone) {
                return;
            }
            const nextPartnerId = toSingleId(nextProps.seedPartnerId);
            if (nextPartnerId !== this.state.partnerId) {
                this.state.partnerId = nextPartnerId;
                this.state.expandedPartnerIds = [];
                this.state.graphViewState = null;
                this.clearSelection();
                if (this.state.partnerId) {
                    await this.reloadGraph();
                } else {
                    this.state.graphData = makeEmptyGraph();
                }
            }
        });
    }

    get partnerSelectorIds() {
        return this.state.partnerId ? [this.state.partnerId] : [];
    }

    get selectedNode() {
        return this.state.graphData.nodes.find((node) => node.id === this.state.selectedNodeId);
    }

    get selectedEdge() {
        return this.state.graphData.edges.find((edge) => edge.id === this.state.selectedEdgeId);
    }

    get hasGraphData() {
        return this.state.graphData.nodes.length > 1 || this.state.graphData.edges.length > 0;
    }

    get focalPartnerId() {
        return toSingleId(this.state.graphData.meta?.focal_partner_id);
    }

    get includeChildContacts() {
        return Boolean(this.state.showChildContacts);
    }

    get relationTypeDomainIds() {
        return this.state.relationTypeIds.filter((relationTypeId) => relationTypeId > 0);
    }

    getPartnerSelectorIds() {
        return this.partnerSelectorIds;
    }

    async loadRelationTypes() {
        const relationTypes = await this.orm.call(
            "res.partner.relation.type",
            "search_read",
            [[], ["name", "name_inverse"]],
            { order: "name asc", limit: 200 }
        );
        this.state.relationTypes = relationTypes;
    }

    async reloadGraph() {
        if (!this.state.partnerId) {
            this.state.graphData = makeEmptyGraph();
            return;
        }
        this.state.loading = true;
        this.state.error = "";
        try {
            const payload = await this.orm.call("res.partner", "get_relationship_graph", [
                this.state.partnerId,
                this.state.includeInactive,
                this.state.relationTypeDomainIds,
                this.state.expandedPartnerIds,
                this.includeChildContacts,
            ]);
            this.state.graphData = payload;
            if (!payload.nodes.find((node) => node.id === this.state.selectedNodeId)) {
                this.state.selectedNodeId = false;
            }
            if (!payload.edges.find((edge) => edge.id === this.state.selectedEdgeId)) {
                this.state.selectedEdgeId = false;
            }
        } catch (error) {
            this.state.error = error?.message || _t("Could not load the relationship graph.");
            this.state.graphData = makeEmptyGraph(this.state.partnerId);
            this.notification.add(this.state.error, { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    clearSelection() {
        this.lastNodeInteraction = { id: false, at: 0 };
        this.state.selectedNodeId = false;
        this.state.selectedEdgeId = false;
    }

    async onPartnerUpdate(ids) {
        this.state.partnerId = ids[0] || false;
        this.state.expandedPartnerIds = [];
        this.state.graphViewState = null;
        this.clearSelection();
        await this.reloadGraph();
    }

    async onToggleChildContacts(event) {
        this.state.showChildContacts = Boolean(event.target.checked);
        this.state.graphViewState = null;
        await this.reloadGraph();
    }

    async onToggleInactive(event) {
        this.state.includeInactive = Boolean(event.target.checked);
        this.state.graphViewState = null;
        await this.reloadGraph();
    }

    async onRelationTypeChange(event) {
        this.state.relationTypeIds = toFilterIdList(
            Array.from(event.target.selectedOptions).map((option) => option.value)
        );
        this.state.expandedPartnerIds = [];
        this.state.graphViewState = null;
        this.clearSelection();
        await this.reloadGraph();
    }

    async onNodeSelect(nodeId) {
        const selectedNodeId = toSingleId(nodeId);
        const now = Date.now();
        const shouldToggleExpansion =
            Boolean(selectedNodeId) &&
            selectedNodeId === this.state.selectedNodeId &&
            this.lastNodeInteraction.id === selectedNodeId &&
            now - this.lastNodeInteraction.at < 360;

        this.state.selectedNodeId = selectedNodeId;
        this.state.selectedEdgeId = false;

        if (shouldToggleExpansion) {
            this.lastNodeInteraction = { id: false, at: 0 };
            await this.toggleExpandedNode(selectedNodeId);
            return;
        }

        this.lastNodeInteraction = { id: selectedNodeId, at: now };
    }

    onEdgeSelect(edgeId) {
        this.lastNodeInteraction = { id: false, at: 0 };
        this.state.selectedEdgeId = toEdgeId(edgeId);
        this.state.selectedNodeId = false;
    }

    onBackgroundClick() {
        this.clearSelection();
    }

    async onGraphEdgeOpen(edgeId) {
        await this.openRelation(edgeId);
    }

    async onOpenSelectedPartner() {
        await this.openPartner(this.selectedNode?.id);
    }

    async onOpenSelectedRelation() {
        await this.openRelation(this.selectedEdge?.id);
    }

    async openStandalone() {
        if (this.props.standalone || !this.state.partnerId) {
            return;
        }
        const action = await this.orm.call("res.partner", "action_open_relationship_graph", [
            [this.state.partnerId],
            this.serializeGraphState(),
        ]);
        await this.action.doAction(action);
    }

    serializeGraphState() {
        return {
            partnerId: this.state.partnerId || false,
            includeInactive: Boolean(this.state.includeInactive),
            showChildContacts: Boolean(this.state.showChildContacts),
            relationTypeIds: [...this.state.relationTypeIds],
            expandedPartnerIds: [...this.state.expandedPartnerIds],
            selectedNodeId: this.state.selectedNodeId || false,
            selectedEdgeId:
                this.state.selectedEdgeId === false ? false : this.state.selectedEdgeId,
            graphViewState: this.state.graphViewState || null,
        };
    }

    async toggleExpandedNode(nodeId = false) {
        const selectedNodeId = toSingleId(nodeId);
        if (!selectedNodeId || selectedNodeId === this.focalPartnerId) {
            return;
        }
        const nextExpandedIds = this.state.expandedPartnerIds.includes(selectedNodeId)
            ? this.state.expandedPartnerIds.filter((id) => id !== selectedNodeId)
            : [...this.state.expandedPartnerIds, selectedNodeId];
        this.state.expandedPartnerIds = toIdList(nextExpandedIds);
        await this.reloadGraph();
        this.state.selectedNodeId = selectedNodeId;
    }

    async openPartner(partnerId = false) {
        const selectedPartnerId = toSingleId(partnerId || this.selectedNode?.id);
        if (!selectedPartnerId) {
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: selectedPartnerId,
            views: [[false, "form"]],
            target: "main",
        });
    }

    async openRelation(edgeId = false) {
        const selectedEdgeId = toEdgeId(edgeId || this.selectedEdge?.id);
        const edge = this.state.graphData.edges.find((candidate) => candidate.id === selectedEdgeId);
        const selectedRelationId = toSingleId(edge?.record_id);
        if (!edge?.openable || edge.record_model !== "res.partner.relation" || !selectedRelationId) {
            return;
        }
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner.relation",
            res_id: selectedRelationId,
            views: [[false, "form"]],
            target: "main",
        });
    }

    partnerRecordUrl(partnerId = false) {
        const selectedPartnerId = toSingleId(partnerId || this.selectedNode?.id);
        return selectedPartnerId ? `/odoo/res.partner/${selectedPartnerId}` : "#";
    }

    getEdgeDisplay(edge) {
        if (!edge) {
            return {
                label: "",
                oppositeLabel: "",
                source: false,
                target: false,
            };
        }
        const selectedNodeId = toSingleId(this.state.selectedNodeId);
        const focalPartnerId = this.focalPartnerId;
        const edgeTouchesSelectedNode =
            selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);
        const edgeTouchesFocalNode =
            focalPartnerId && (edge.source === focalPartnerId || edge.target === focalPartnerId);

        let preferredSourceId = false;
        if (edgeTouchesSelectedNode) {
            preferredSourceId = selectedNodeId;
        } else if (edgeTouchesFocalNode) {
            preferredSourceId = focalPartnerId;
        }

        if (preferredSourceId && edge.target === preferredSourceId) {
            return {
                label: edge.inverse_label || edge.label,
                oppositeLabel: edge.label,
                source: edge.target,
                target: edge.source,
            };
        }
        return {
            label: edge.label,
            oppositeLabel: edge.inverse_label || edge.label,
            source: edge.source,
            target: edge.target,
        };
    }

    displayEdgeLabel(edge) {
        return this.getEdgeDisplay(edge).label;
    }

    displayOppositeEdgeLabel(edge) {
        return this.getEdgeDisplay(edge).oppositeLabel;
    }

    onGraphViewStateChange(viewState) {
        this.state.graphViewState = viewState;
    }

    relationTypeLabel(relationType) {
        if (!relationType?.name_inverse) {
            return relationType?.name || _t("Relation Type");
        }
        return `${relationType.name} / ${relationType.name_inverse}`;
    }
}
