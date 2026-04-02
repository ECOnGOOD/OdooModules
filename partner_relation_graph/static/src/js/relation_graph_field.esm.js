/** @odoo-module **/

import { registry } from "@web/core/registry";
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

    get partnerId() {
        return this.props.record.resId;
    }
}

export const partnerRelationGraphField = {
    component: RelationGraphField,
    displayName: "Partner Relation Graph",
    supportedTypes: ["integer"],
};

registry.category("fields").add("partner_relation_graph", partnerRelationGraphField);
