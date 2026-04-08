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
    _RELATION_GRAPH_CHILD_CONTACT_LABEL = "Has contact"
    _RELATION_GRAPH_CHILD_CONTACT_INVERSE_LABEL = "Contact of"

    def _compute_relationship_graph_seed_id(self):
        for partner in self:
            partner.relationship_graph_seed_id = partner.id

    def action_open_relationship_graph(self, default_graph_state=None):
        self.ensure_one()
        context = {
            **self.env.context,
            "active_id": self.id,
            "active_ids": self.ids,
            "default_partner_id": self.id,
        }
        if default_graph_state not in (None, False):
            context["default_graph_state"] = default_graph_state
        return {
            "type": "ir.actions.client",
            "tag": "partner_relation_graph.client_action",
            "name": _("Relationship Graph"),
            "context": context,
        }

    @api.model
    def get_relationship_graph(
        self,
        partner_id,
        include_inactive=False,
        relation_type_ids=None,
        expanded_partner_ids=None,
        include_child_contacts=True,
    ):
        (
            partner_id,
            include_inactive,
            relation_type_ids,
            expanded_partner_ids,
            include_child_contacts,
        ) = self._normalize_graph_call(
            partner_id,
            include_inactive,
            relation_type_ids,
            expanded_partner_ids,
            include_child_contacts,
        )
        relation_type_ids = self._normalize_graph_filter_ids(relation_type_ids)
        expanded_partner_ids = self._normalize_graph_ids(expanded_partner_ids)
        focal_partner = self.search([("id", "=", partner_id)], limit=1)
        if not focal_partner:
            raise MissingError(_("The selected partner is no longer available."))

        expanded_partners = self.search([("id", "in", expanded_partner_ids)])
        seed_partners = focal_partner | expanded_partners
        seed_partner_ids = [partner.id for partner in seed_partners]
        selected_relation_type_ids = [
            relation_type_id for relation_type_id in relation_type_ids if relation_type_id > 0
        ]
        has_explicit_type_filter = bool(relation_type_ids)
        include_child_contacts = bool(include_child_contacts)

        relation_rows = []
        truncated = False
        relation_type_map = {}
        if not has_explicit_type_filter or selected_relation_type_ids:
            relation_domain = [("this_partner_id", "in", seed_partner_ids)]
            if selected_relation_type_ids:
                relation_domain.append(("type_id", "in", selected_relation_type_ids))
            if not include_inactive:
                relation_domain.append(("active", "=", True))

            relation_rows = self.env["res.partner.relation.all"].with_context(active_test=False).search_read(
                relation_domain,
                fields=[
                    "res_id",
                    "this_partner_id",
                    "other_partner_id",
                    "type_id",
                    "date_start",
                    "date_end",
                    "active",
                    "is_inverse",
                ],
                order="date_end desc, date_start desc, id desc",
                limit=self._RELATION_GRAPH_QUERY_LIMIT + 1,
            )
            truncated = len(relation_rows) > self._RELATION_GRAPH_QUERY_LIMIT
            relation_rows = relation_rows[: self._RELATION_GRAPH_QUERY_LIMIT]
            relation_type_map = self._get_graph_relation_type_map(relation_rows)

        relation_edges = self._build_relation_graph_edges(relation_rows, relation_type_map)
        child_contact_edges = (
            self._build_child_contact_graph_edges(seed_partners) if include_child_contacts else []
        )
        graph_source_edges = relation_edges + child_contact_edges
        partner_map = self._get_accessible_graph_partner_map(focal_partner.id, graph_source_edges)

        nodes = OrderedDict()
        graph_nodes = []
        graph_edges = []
        expanded_set = set(expanded_partners.ids)

        for edge in graph_source_edges:
            required_node_ids = [edge["source_partner_id"], edge["target_partner_id"]]
            if any(required_partner_id not in partner_map for required_partner_id in required_node_ids):
                continue
            new_node_count = len(
                [
                    required_partner_id
                    for required_partner_id in required_node_ids
                    if required_partner_id not in nodes
                ]
            )
            if graph_edges and (
                len(graph_edges) >= self._RELATION_GRAPH_EDGE_LIMIT
                or len(nodes) + new_node_count > self._RELATION_GRAPH_NODE_LIMIT
            ):
                truncated = True
                continue
            for node_partner_id in required_node_ids:
                if node_partner_id in nodes:
                    continue
                partner_data = partner_map[node_partner_id]
                node = {
                    "id": node_partner_id,
                    "display_name": partner_data["display_name"],
                    "is_company": bool(partner_data["is_company"]),
                    "is_focal": node_partner_id == focal_partner.id,
                    "is_expanded": node_partner_id in expanded_set,
                    "is_seed": node_partner_id in seed_partner_ids,
                    "is_child_contact": partner_data["is_child_contact"],
                }
                nodes[node_partner_id] = node
                graph_nodes.append(node)
            graph_edges.append(
                {
                    "id": edge["id"],
                    "source": edge["source_partner_id"],
                    "target": edge["target_partner_id"],
                    "label": edge["label"],
                    "inverse_label": edge["inverse_label"],
                    "type_id": edge["type_id"],
                    "date_start": edge["date_start"],
                    "date_end": edge["date_end"],
                    "active": edge["active"],
                    "is_inverse": edge["is_inverse"],
                    "kind": edge["kind"],
                    "record_model": edge["record_model"],
                    "record_id": edge["record_id"],
                    "openable": edge["openable"],
                }
            )

        if focal_partner.id not in nodes:
            focal_data = partner_map.get(focal_partner.id, self._build_graph_partner_data(focal_partner))
            focal_node = {
                "id": focal_partner.id,
                "display_name": focal_data["display_name"],
                "is_company": bool(focal_data["is_company"]),
                "is_focal": True,
                "is_expanded": focal_partner.id in expanded_set,
                "is_seed": True,
                "is_child_contact": focal_data["is_child_contact"],
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
                    if relation_id != focal_partner.id and relation_id in partner_map
                ],
                "total_node_count": len(graph_nodes),
                "total_edge_count": len(graph_edges),
                "truncated": truncated,
            },
        }

    @api.model
    def _normalize_graph_call(
        self,
        partner_id,
        include_inactive=False,
        relation_type_ids=None,
        expanded_partner_ids=None,
        include_child_contacts=True,
    ):
        if isinstance(partner_id, (list, tuple)):
            packed = list(partner_id)
            partner_id = packed[0] if packed else False
            if len(packed) > 1 and include_inactive in (False, None):
                include_inactive = packed[1]
            if len(packed) > 2 and relation_type_ids in (None, False):
                relation_type_ids = packed[2]
            if len(packed) > 3 and expanded_partner_ids in (None, False):
                expanded_partner_ids = packed[3]
            if len(packed) > 4 and include_child_contacts in (True, None):
                include_child_contacts = packed[4]

        try:
            partner_id = int(partner_id)
        except (TypeError, ValueError):
            partner_id = False

        include_inactive = bool(include_inactive)
        include_child_contacts = bool(include_child_contacts)
        return (
            partner_id,
            include_inactive,
            relation_type_ids,
            expanded_partner_ids,
            include_child_contacts,
        )

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
    def _normalize_graph_filter_ids(self, values):
        if not values:
            return []
        normalized = []
        for value in values:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    @api.model
    def _get_graph_relation_type_map(self, relation_rows):
        type_ids = self._normalize_graph_ids(
            [
                relation_row.get("type_id", [False])[0]
                for relation_row in relation_rows
                if relation_row.get("type_id")
            ]
        )
        if not type_ids:
            return {}
        relation_types = self.env["res.partner.relation.type"].search_read(
            [("id", "in", type_ids)],
            fields=["name", "name_inverse"],
            limit=len(type_ids),
        )
        return {relation_type["id"]: relation_type for relation_type in relation_types}

    @api.model
    def _build_graph_partner_data(self, partner):
        return {
            "id": partner.id,
            "display_name": partner.display_name,
            "is_company": bool(partner.is_company),
            "is_child_contact": bool(partner.parent_id),
        }

    @api.model
    def _get_accessible_graph_partner_map(self, focal_partner_id, relation_edges):
        partner_ids = {focal_partner_id}
        for edge in relation_edges:
            partner_ids.add(edge["source_partner_id"])
            partner_ids.add(edge["target_partner_id"])

        partners = self.search([("id", "in", list(partner_ids))])
        return {partner.id: self._build_graph_partner_data(partner) for partner in partners}

    @api.model
    def _build_relation_graph_edges(self, relation_rows, relation_type_map):
        edges_by_relation = OrderedDict()
        for relation in relation_rows:
            this_partner = relation.get("this_partner_id") or []
            other_partner = relation.get("other_partner_id") or []
            if len(this_partner) < 2 or len(other_partner) < 2:
                continue

            this_partner_id = int(this_partner[0])
            other_partner_id = int(other_partner[0])
            is_inverse = bool(relation.get("is_inverse"))
            source_partner_id = other_partner_id if is_inverse else this_partner_id
            target_partner_id = this_partner_id if is_inverse else other_partner_id
            type_id = relation.get("type_id", [False])[0] if relation.get("type_id") else False
            type_data = relation_type_map.get(type_id, {})
            candidate = {
                "id": relation["res_id"],
                "source_partner_id": source_partner_id,
                "target_partner_id": target_partner_id,
                "label": type_data.get("name") or (relation.get("type_id") or [False, ""])[1],
                "inverse_label": type_data.get("name_inverse") or "",
                "type_id": type_id,
                "date_start": relation.get("date_start"),
                "date_end": relation.get("date_end"),
                "active": relation.get("active"),
                "is_inverse": is_inverse,
                "kind": "relation",
                "record_model": "res.partner.relation",
                "record_id": relation["res_id"],
                "openable": True,
            }
            existing = edges_by_relation.get(relation["res_id"])
            if existing and not existing["is_inverse"]:
                continue
            edges_by_relation[relation["res_id"]] = candidate
        return list(edges_by_relation.values())

    @api.model
    def _build_child_contact_graph_edges(self, seed_partners):
        edges_by_pair = OrderedDict()
        if not seed_partners:
            return []

        seed_partner_ids = set(seed_partners.ids)
        child_contacts = self.search(
            [("parent_id", "in", list(seed_partner_ids))],
            order="name asc, id asc",
        )
        for child_contact in child_contacts:
            parent_partner = child_contact.parent_id
            if not parent_partner or parent_partner.id not in seed_partner_ids:
                continue
            edge = self._prepare_child_contact_graph_edge(parent_partner, child_contact)
            edges_by_pair[edge["id"]] = edge

        for seed_partner in seed_partners.filtered("parent_id"):
            parent_partner = self.search([("id", "=", seed_partner.parent_id.id)], limit=1)
            if not parent_partner:
                continue
            edge = self._prepare_child_contact_graph_edge(parent_partner, seed_partner)
            edges_by_pair[edge["id"]] = edge

        return list(edges_by_pair.values())

    @api.model
    def _prepare_child_contact_graph_edge(self, parent_partner, child_partner):
        return {
            "id": self._make_child_contact_edge_id(parent_partner.id, child_partner.id),
            "source_partner_id": parent_partner.id,
            "target_partner_id": child_partner.id,
            "label": self._RELATION_GRAPH_CHILD_CONTACT_LABEL,
            "inverse_label": self._RELATION_GRAPH_CHILD_CONTACT_INVERSE_LABEL,
            "type_id": False,
            "date_start": False,
            "date_end": False,
            "active": True,
            "is_inverse": False,
            "kind": "child_contact",
            "record_model": False,
            "record_id": False,
            "openable": False,
        }

    @api.model
    def _make_child_contact_edge_id(self, parent_partner_id, child_partner_id):
        return -((int(parent_partner_id) << 24) + int(child_partner_id))
