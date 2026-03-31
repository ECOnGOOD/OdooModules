/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component } from "@odoo/owl";

import { RelationGraphExplorer } from "@partner_relation_graph/js/relation_graph_core.esm";

export class RelationGraphField extends Component {
    static template = "partner_relation_graph.RelationGraphField";
    static components = {
        RelationGraphExplorer,
    };
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    get partnerId() {
        return this.props.record.resId;
    }

    async openStandalone() {
        if (!this.partnerId) {
            return;
        }
        const action = await this.orm.call("res.partner", "action_open_relationship_graph", [[this.partnerId]]);
        await this.action.doAction(action);
    }
}

export const partnerRelationGraphField = {
    component: RelationGraphField,
    displayName: "Partner Relation Graph",
    supportedTypes: ["integer"],
};

registry.category("fields").add("partner_relation_graph", partnerRelationGraphField);
