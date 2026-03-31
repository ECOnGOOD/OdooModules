from odoo import fields
from odoo.tests.common import TransactionCase


class TestPartnerRelationGraph(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.member = cls.env["res.partner"].create(
            {
                "name": "Graph Member",
                "is_company": False,
            }
        )
        cls.company = cls.env["res.partner"].create(
            {
                "name": "Graph Federation",
                "is_company": True,
            }
        )
        cls.second_hop = cls.env["res.partner"].create(
            {
                "name": "Graph Chapter",
                "is_company": True,
            }
        )
        cls.past_company = cls.env["res.partner"].create(
            {
                "name": "Past Chapter",
                "is_company": True,
            }
        )
        cls.lonely_partner = cls.env["res.partner"].create(
            {
                "name": "Lonely Contact",
                "is_company": False,
            }
        )
        cls.member_of_type = cls.env["res.partner.relation.type"].create(
            {
                "name": "Member Of",
                "name_inverse": "Has Member",
                "contact_type_left": "p",
                "contact_type_right": "c",
            }
        )
        cls.branch_type = cls.env["res.partner.relation.type"].create(
            {
                "name": "Branch Of",
                "name_inverse": "Has Branch",
                "contact_type_left": "c",
                "contact_type_right": "c",
            }
        )
        cls.alumni_type = cls.env["res.partner.relation.type"].create(
            {
                "name": "Alumni Of",
                "name_inverse": "Has Alumni",
                "contact_type_left": "p",
                "contact_type_right": "c",
            }
        )
        cls.active_relation = cls.env["res.partner.relation"].create(
            {
                "left_partner_id": cls.member.id,
                "right_partner_id": cls.company.id,
                "type_id": cls.member_of_type.id,
                "date_start": "2026-01-01",
            }
        )
        cls.second_hop_relation = cls.env["res.partner.relation"].create(
            {
                "left_partner_id": cls.company.id,
                "right_partner_id": cls.second_hop.id,
                "type_id": cls.branch_type.id,
                "date_start": "2026-01-01",
            }
        )
        cls.past_relation = cls.env["res.partner.relation"].create(
            {
                "left_partner_id": cls.member.id,
                "right_partner_id": cls.past_company.id,
                "type_id": cls.alumni_type.id,
                "date_start": "2025-01-01",
                "date_end": fields.Date.today().replace(year=fields.Date.today().year - 1),
            }
        )

    def test_graph_payload_defaults_to_active_direct_relations(self):
        payload = self.env["res.partner"].get_relationship_graph(partner_id=self.member.id)

        self.assertEqual(payload["meta"]["focal_partner_id"], self.member.id)
        self.assertEqual(payload["meta"]["total_edge_count"], 1)
        self.assertFalse(payload["meta"]["truncated"])

        node_by_id = {node["id"]: node for node in payload["nodes"]}
        self.assertEqual(set(node_by_id), {self.member.id, self.company.id})
        self.assertFalse(node_by_id[self.member.id]["is_company"])
        self.assertTrue(node_by_id[self.company.id]["is_company"])
        self.assertTrue(node_by_id[self.member.id]["is_focal"])

        edge = payload["edges"][0]
        self.assertEqual(edge["id"], self.active_relation.id)
        self.assertEqual(edge["source"], self.member.id)
        self.assertEqual(edge["target"], self.company.id)
        self.assertEqual(edge["label"], "Member Of")
        self.assertTrue(edge["active"])

    def test_graph_payload_can_include_inactive_and_filter_relation_type(self):
        payload = self.env["res.partner"].get_relationship_graph(
            partner_id=self.member.id,
            include_inactive=True,
            relation_type_ids=[self.alumni_type.id],
        )

        self.assertEqual(payload["meta"]["total_edge_count"], 1)
        self.assertEqual(payload["edges"][0]["id"], self.past_relation.id)
        self.assertFalse(payload["edges"][0]["active"])
        self.assertEqual(
            {node["id"] for node in payload["nodes"]},
            {self.member.id, self.past_company.id},
        )

    def test_graph_payload_expands_one_hop_from_selected_neighbor(self):
        initial_payload = self.env["res.partner"].get_relationship_graph(partner_id=self.member.id)
        self.assertNotIn(
            self.second_hop.id,
            {node["id"] for node in initial_payload["nodes"]},
        )

        expanded_payload = self.env["res.partner"].get_relationship_graph(
            partner_id=self.member.id,
            expanded_partner_ids=[self.company.id],
        )
        self.assertIn(self.company.id, expanded_payload["meta"]["expanded_partner_ids"])
        self.assertEqual(expanded_payload["meta"]["total_edge_count"], 2)
        self.assertIn(
            self.second_hop.id,
            {node["id"] for node in expanded_payload["nodes"]},
        )

    def test_empty_graph_returns_only_focal_partner(self):
        payload = self.env["res.partner"].get_relationship_graph(partner_id=self.lonely_partner.id)

        self.assertEqual(payload["meta"]["total_edge_count"], 0)
        self.assertEqual(payload["meta"]["total_node_count"], 1)
        self.assertEqual(payload["nodes"][0]["id"], self.lonely_partner.id)
        self.assertTrue(payload["nodes"][0]["is_focal"])

    def test_internal_user_can_read_graph_payload_without_sudo(self):
        internal_user = self.env["res.users"].with_context(no_reset_password=True).create(
            {
                "name": "Relationship Graph User",
                "login": "relationship.graph.user",
                "email": "relationship.graph.user@example.com",
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            }
        )

        payload = self.env["res.partner"].with_user(internal_user).get_relationship_graph(
            partner_id=self.member.id,
            include_inactive=True,
        )

        partner_ids = [node["id"] for node in payload["nodes"]]
        relation_ids = [edge["id"] for edge in payload["edges"]]
        self.assertEqual(
            set(partner_ids),
            set(self.env["res.partner"].with_user(internal_user).search([("id", "in", partner_ids)]).ids),
        )
        self.assertEqual(
            set(relation_ids),
            set(
                self.env["res.partner.relation"]
                .with_user(internal_user)
                .search([("id", "in", relation_ids)])
                .ids
            ),
        )

    def test_action_open_relationship_graph_returns_client_action(self):
        action = self.member.action_open_relationship_graph()

        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "partner_relation_graph.client_action")
        self.assertEqual(action["context"]["default_partner_id"], self.member.id)
