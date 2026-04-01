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

function toIdList(values) {
    return (values || [])
        .map((value) => Number(value))
        .filter(
            (value, index, list) =>
                Number.isInteger(value) && value > 0 && list.indexOf(value) === index
        );
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
        onNodeOpen: { type: Function, optional: true },
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
            this.graph.on("nodedblclick", ({ id }) => this.props.onNodeOpen?.(id));
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
    };
    static defaultProps = {
        standalone: false,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            partnerId: toSingleId(this.props.seedPartnerId),
            includeInactive: false,
            relationTypeIds: [],
            relationTypes: [],
            graphData: makeEmptyGraph(toSingleId(this.props.seedPartnerId)),
            loading: false,
            error: "",
            selectedNodeId: false,
            selectedEdgeId: false,
            expandedPartnerIds: [],
            graphViewState: null,
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

    getPartnerSelectorIds() {
        return this.partnerSelectorIds;
    }

    get selectedNode() {
        return this.state.graphData.nodes.find(
            (node) => node.id === this.state.selectedNodeId
        );
    }

    get selectedEdge() {
        return this.state.graphData.edges.find((edge) => edge.id === this.state.selectedEdgeId);
    }

    get hasGraphData() {
        return this.state.graphData.nodes.length > 1 || this.state.graphData.edges.length > 0;
    }

    get canExpandSelectedNode() {
        return Boolean(
            this.selectedNode &&
                !this.selectedNode.is_focal &&
                !this.state.expandedPartnerIds.includes(this.selectedNode.id)
        );
    }

    async loadRelationTypes() {
        this.state.relationTypes = await this.orm.call(
            "res.partner.relation.type",
            "search_read",
            [[], ["name", "name_inverse"]],
            { order: "name asc", limit: 200 }
        );
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
                this.state.relationTypeIds,
                this.state.expandedPartnerIds,
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

    async onToggleInactive(event) {
        this.state.includeInactive = Boolean(event.target.checked);
        this.state.graphViewState = null;
        await this.reloadGraph();
    }

    async onRelationTypeChange(event) {
        this.state.relationTypeIds = toIdList(
            Array.from(event.target.selectedOptions).map((option) => option.value)
        );
        this.state.expandedPartnerIds = [];
        this.state.graphViewState = null;
        this.clearSelection();
        await this.reloadGraph();
    }

    onNodeSelect(nodeId) {
        this.state.selectedNodeId = nodeId;
        this.state.selectedEdgeId = false;
    }

    onEdgeSelect(edgeId) {
        this.state.selectedEdgeId = edgeId;
        this.state.selectedNodeId = false;
    }

    onBackgroundClick() {
        this.clearSelection();
    }

    async onGraphNodeOpen(nodeId) {
        await this.openPartner(nodeId);
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

    async onExpandSelectedNode() {
        if (!this.canExpandSelectedNode) {
            return;
        }
        const selectedNodeId = this.selectedNode.id;
        this.state.expandedPartnerIds = toIdList([
            ...this.state.expandedPartnerIds,
            selectedNodeId,
        ]);
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

    async openRelation(relationId = false) {
        const selectedRelationId = toSingleId(relationId || this.selectedEdge?.id);
        if (!selectedRelationId) {
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
