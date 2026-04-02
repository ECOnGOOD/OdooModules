/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

import { Component } from "@odoo/owl";

import { RelationGraphExplorer } from "@partner_relation_graph/js/relation_graph_core.esm";

export class RelationGraphClientAction extends Component {
    static template = "partner_relation_graph.RelationGraphClientAction";
    static components = {
        RelationGraphExplorer,
    };
    static props = {
        ...standardActionServiceProps,
    };

    get initialGraphState() {
        return this.props.action?.context?.default_graph_state || null;
    }

    get seedPartnerId() {
        return Number(
            this.initialGraphState?.partnerId
                || this.props.action?.context?.default_partner_id
                || this.props.action?.context?.active_id
                || 0
        ) || false;
    }
}

registry.category("actions").add("partner_relation_graph.client_action", RelationGraphClientAction);
