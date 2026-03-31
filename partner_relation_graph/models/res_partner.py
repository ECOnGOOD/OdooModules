from collections import OrderedDict

from odoo import _, api, fields, models
from odoo.exceptions import MissingError


class ResPartner(models.Model):
    _inherit = "res.partner"

    relationship_graph_seed_id = fields.Integer(
        compute="_compute_relationship_graph_seed_id",
        string="Relationship Graph Seed",
    )

    _RELATION_GRAPH_EDGE_LIMIT = 120
    _RELATION_GRAPH_NODE_LIMIT = 180
    _RELATION_GRAPH_QUERY_LIMIT = 320

    def _compute_relationship_graph_seed_id(self):
        for partner in self:
            partner.relationship_graph_seed_id = partner.id

    def action_open_relationship_graph(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "partner_relation_graph.client_action",
            "name": _("Relationship Graph"),
            "context": {
                **self.env.context,
                "active_id": self.id,
                "active_ids": self.ids,
                "default_partner_id": self.id,
            },
        }

    @api.model
    def get_relationship_graph(
        self,
        partner_id,
        include_inactive=False,
        relation_type_ids=None,
        expanded_partner_ids=None,
    ):
        relation_type_ids = self._normalize_graph_ids(relation_type_ids)
        expanded_partner_ids = self._normalize_graph_ids(expanded_partner_ids)
        focal_partner = self.search([("id", "=", partner_id)], limit=1)
        if not focal_partner:
            raise MissingError(_("The selected partner is no longer available."))

        expanded_partners = self.search([("id", "in", expanded_partner_ids)])
        seed_partner_ids = [focal_partner.id] + [
            partner.id for partner in expanded_partners if partner.id != focal_partner.id
        ]

        relation_domain = [("this_partner_id", "in", seed_partner_ids)]
        if relation_type_ids:
            relation_domain.append(("type_id", "in", relation_type_ids))
        if not include_inactive:
            relation_domain.append(("active", "=", True))

        relation_rows = (
            self.env["res.partner.relation.all"]
            .with_context(active_test=False)
            .search(
                relation_domain,
                order="date_end desc, date_start desc, id desc",
                limit=self._RELATION_GRAPH_QUERY_LIMIT + 1,
            )
        )

        truncated = len(relation_rows) > self._RELATION_GRAPH_QUERY_LIMIT
        relation_rows = relation_rows[: self._RELATION_GRAPH_QUERY_LIMIT]
        relation_edges = self._build_relation_graph_edges(relation_rows)

        nodes = OrderedDict()
        graph_nodes = []
        graph_edges = []
        expanded_set = set(expanded_partners.ids)

        for edge in relation_edges:
            required_nodes = [edge["source_partner"], edge["target_partner"]]
            new_node_count = len(
                [partner for partner in required_nodes if partner.id not in nodes]
            )
            if graph_edges and (
                len(graph_edges) >= self._RELATION_GRAPH_EDGE_LIMIT
                or len(nodes) + new_node_count > self._RELATION_GRAPH_NODE_LIMIT
            ):
                truncated = True
                continue
            for partner in required_nodes:
                if partner.id in nodes:
                    continue
                node = {
                    "id": partner.id,
                    "display_name": partner.display_name,
                    "is_company": bool(partner.is_company),
                    "is_focal": partner.id == focal_partner.id,
                    "is_expanded": partner.id in expanded_set,
                    "is_seed": partner.id in seed_partner_ids,
                }
                nodes[partner.id] = node
                graph_nodes.append(node)
            graph_edges.append(
                {
                    "id": edge["id"],
                    "source": edge["source_partner"].id,
                    "target": edge["target_partner"].id,
                    "label": edge["label"],
                    "inverse_label": edge["inverse_label"],
                    "type_id": edge["type_id"],
                    "date_start": edge["date_start"],
                    "date_end": edge["date_end"],
                    "active": edge["active"],
                    "is_inverse": edge["is_inverse"],
                }
            )

        if focal_partner.id not in nodes:
            focal_node = {
                "id": focal_partner.id,
                "display_name": focal_partner.display_name,
                "is_company": bool(focal_partner.is_company),
                "is_focal": True,
                "is_expanded": focal_partner.id in expanded_set,
                "is_seed": True,
            }
            nodes[focal_partner.id] = focal_node
            graph_nodes.insert(0, focal_node)

        graph_nodes.sort(
            key=lambda item: (
                not item["is_focal"],
                not item["is_seed"],
                item["display_name"].lower(),
                item["id"],
            )
        )
        graph_edges.sort(key=lambda item: (item["label"].lower(), item["id"]))

        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "meta": {
                "focal_partner_id": focal_partner.id,
                "expanded_partner_ids": [
                    relation_id
                    for relation_id in seed_partner_ids
                    if relation_id != focal_partner.id
                ],
                "total_node_count": len(graph_nodes),
                "total_edge_count": len(graph_edges),
                "truncated": truncated,
            },
        }

    @api.model
    def _normalize_graph_ids(self, values):
        if not values:
            return []
        normalized = []
        for value in values:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in normalized:
                normalized.append(value)
        return normalized

    @api.model
    def _build_relation_graph_edges(self, relation_rows):
        edges_by_relation = OrderedDict()
        for relation in relation_rows:
            source_partner = (
                relation.other_partner_id if relation.is_inverse else relation.this_partner_id
            )
            target_partner = (
                relation.this_partner_id if relation.is_inverse else relation.other_partner_id
            )
            candidate = {
                "id": relation.res_id,
                "source_partner": source_partner,
                "target_partner": target_partner,
                "label": relation.type_id.name,
                "inverse_label": relation.type_id.name_inverse,
                "type_id": relation.type_id.id,
                "date_start": relation.date_start,
                "date_end": relation.date_end,
                "active": relation.active,
                "is_inverse": relation.is_inverse,
            }
            existing = edges_by_relation.get(relation.res_id)
            if existing and not existing["is_inverse"]:
                continue
            edges_by_relation[relation.res_id] = candidate
        return list(edges_by_relation.values())
