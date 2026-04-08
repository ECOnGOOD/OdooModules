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
        cls.company_contact = cls.env["res.partner"].create(
            {
                "name": "Graph Contact",
                "is_company": False,
                "parent_id": cls.company.id,
            }
        )
        cls.second_hop = cls.env["res.partner"].create(
            {
                "name": "Graph Chapter",
                "is_company": True,
            }
        )
        cls.second_hop_contact = cls.env["res.partner"].create(
            {
                "name": "Chapter Contact",
                "is_company": False,
                "parent_id": cls.second_hop.id,
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
        cls.child_contact_filter_id = cls.env["res.partner"]._RELATION_GRAPH_CHILD_CONTACT_FILTER_ID

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
        self.assertEqual(node_by_id[self.member.id]["structure_key"], "person")
        self.assertEqual(node_by_id[self.member.id]["style_key"], "person")
        self.assertEqual(node_by_id[self.company.id]["structure_key"], "company")
        self.assertEqual(node_by_id[self.company.id]["style_key"], "company_generic")
        self.assertFalse(payload["meta"]["has_econgood_taxonomy"])
        self.assertEqual(payload["meta"]["legend_mode"], "collapsed")

        edge = payload["edges"][0]
        self.assertEqual(edge["id"], self.active_relation.id)
        self.assertEqual(edge["source"], self.member.id)
        self.assertEqual(edge["target"], self.company.id)
        self.assertEqual(edge["label"], "Member Of")
        self.assertTrue(edge["active"])
        self.assertEqual(edge["kind"], "relation")
        self.assertTrue(edge["openable"])

    def test_graph_payload_can_include_inactive_and_filter_relation_type(self):
        payload = self.env["res.partner"].get_relationship_graph(
            partner_id=self.member.id,
            include_inactive=True,
            relation_type_ids=[self.alumni_type.id],
            include_child_contacts=False,
        )

        self.assertEqual(payload["meta"]["total_edge_count"], 1)
        self.assertEqual(payload["edges"][0]["id"], self.past_relation.id)
        self.assertFalse(payload["edges"][0]["active"])
        self.assertEqual(
            {node["id"] for node in payload["nodes"]},
            {self.member.id, self.past_company.id},
        )

    def test_graph_payload_includes_child_contacts_for_focal_company(self):
        payload = self.env["res.partner"].get_relationship_graph(partner_id=self.company.id)

        self.assertEqual(payload["meta"]["total_edge_count"], 3)
        self.assertEqual(
            {node["id"] for node in payload["nodes"]},
            {self.member.id, self.company.id, self.company_contact.id, self.second_hop.id},
        )
        child_edges = [edge for edge in payload["edges"] if edge["kind"] == "child_contact"]
        self.assertEqual(len(child_edges), 1)
        self.assertEqual(child_edges[0]["source"], self.company.id)
        self.assertEqual(child_edges[0]["target"], self.company_contact.id)
        self.assertEqual(child_edges[0]["label"], "Has contact")
        self.assertFalse(child_edges[0]["openable"])
        self.assertLess(child_edges[0]["id"], 0)
        node_by_id = {node["id"]: node for node in payload["nodes"]}
        self.assertEqual(node_by_id[self.company_contact.id]["structure_key"], "child_contact")
        self.assertEqual(node_by_id[self.company_contact.id]["style_key"], "child_contact")

    def test_graph_payload_returns_reverse_parent_link_for_child_contact(self):
        payload = self.env["res.partner"].get_relationship_graph(partner_id=self.company_contact.id)

        self.assertEqual(payload["meta"]["total_edge_count"], 1)
        edge = payload["edges"][0]
        self.assertEqual(edge["kind"], "child_contact")
        self.assertEqual(edge["source"], self.company.id)
        self.assertEqual(edge["target"], self.company_contact.id)
        self.assertEqual(
            {node["id"] for node in payload["nodes"]},
            {self.company.id, self.company_contact.id},
        )

    def test_graph_payload_expands_one_hop_from_selected_neighbor(self):
        initial_payload = self.env["res.partner"].get_relationship_graph(partner_id=self.member.id)
        self.assertNotIn(self.second_hop.id, {node["id"] for node in initial_payload["nodes"]})
        self.assertNotIn(self.company_contact.id, {node["id"] for node in initial_payload["nodes"]})

        expanded_payload = self.env["res.partner"].get_relationship_graph(
            partner_id=self.member.id,
            expanded_partner_ids=[self.company.id],
        )
        self.assertIn(self.company.id, expanded_payload["meta"]["expanded_partner_ids"])
        self.assertEqual(expanded_payload["meta"]["total_edge_count"], 3)
        self.assertIn(self.second_hop.id, {node["id"] for node in expanded_payload["nodes"]})
        self.assertIn(self.company_contact.id, {node["id"] for node in expanded_payload["nodes"]})

    def test_graph_payload_accepts_nested_call_shape_from_web_client(self):
        expanded_payload = self.env["res.partner"].get_relationship_graph(
            [self.member.id, False, [], [self.company.id], True]
        )

        self.assertIn(self.company.id, expanded_payload["meta"]["expanded_partner_ids"])
        self.assertIn(self.second_hop.id, {node["id"] for node in expanded_payload["nodes"]})
        self.assertIn(self.company_contact.id, {node["id"] for node in expanded_payload["nodes"]})
        self.assertEqual(expanded_payload["meta"]["total_edge_count"], 3)

    def test_expanded_nodes_include_their_own_child_contacts(self):
        payload = self.env["res.partner"].get_relationship_graph(
            partner_id=self.company.id,
            expanded_partner_ids=[self.second_hop.id],
        )

        child_edge_targets = {
            edge["target"] for edge in payload["edges"] if edge["kind"] == "child_contact"
        }
        self.assertEqual(child_edge_targets, {self.company_contact.id, self.second_hop_contact.id})
        self.assertIn(self.second_hop_contact.id, {node["id"] for node in payload["nodes"]})

    def test_child_contact_filter_can_be_selected_on_its_own(self):
        payload = self.env["res.partner"].get_relationship_graph(
            partner_id=self.company.id,
            relation_type_ids=[self.child_contact_filter_id],
            include_child_contacts=True,
        )

        self.assertEqual(payload["meta"]["total_edge_count"], 1)
        self.assertEqual(payload["edges"][0]["kind"], "child_contact")
        self.assertEqual(payload["edges"][0]["target"], self.company_contact.id)

    def test_explicit_relation_type_filter_can_hide_child_contacts(self):
        payload = self.env["res.partner"].get_relationship_graph(
            partner_id=self.company.id,
            relation_type_ids=[self.branch_type.id],
            include_child_contacts=False,
        )

        self.assertEqual(payload["meta"]["total_edge_count"], 1)
        self.assertEqual(payload["edges"][0]["kind"], "relation")
        self.assertEqual(payload["edges"][0]["id"], self.second_hop_relation.id)

    def test_empty_graph_returns_only_focal_partner(self):
        payload = self.env["res.partner"].get_relationship_graph(partner_id=self.lonely_partner.id)

        self.assertEqual(payload["meta"]["total_edge_count"], 0)
        self.assertEqual(payload["meta"]["total_node_count"], 1)
        self.assertEqual(payload["nodes"][0]["id"], self.lonely_partner.id)
        self.assertTrue(payload["nodes"][0]["is_focal"])

    def test_visual_classification_returns_fallbacks_without_optional_taxonomy(self):
        partner_model = self.env["res.partner"]

        self.assertFalse(partner_model._has_graph_taxonomy_support())
        self.assertEqual(
            partner_model._classify_graph_partner_visuals(is_company=False, has_parent=False)["style_key"],
            "person",
        )
        self.assertEqual(
            partner_model._classify_graph_partner_visuals(is_company=True, has_parent=False)["style_key"],
            "company_generic",
        )
        self.assertEqual(
            partner_model._classify_graph_partner_visuals(is_company=False, has_parent=True)["style_key"],
            "child_contact",
        )

    def test_visual_classification_prefers_ou_type_and_uses_stable_fallbacks(self):
        partner_model = self.env["res.partner"]

        visual = partner_model._classify_graph_partner_visuals(
            is_company=True,
            has_parent=False,
            ou_type_code="local_chapter",
            organization_kind_code="company",
        )
        self.assertEqual(visual["style_key"], "ou_type_local_chapter")
        self.assertEqual(visual["style_label"], "Local Chapter")

        unknown_ou_visual = partner_model._classify_graph_partner_visuals(
            is_company=True,
            has_parent=False,
            ou_type_code="surprise_bucket",
        )
        self.assertEqual(unknown_ou_visual["style_key"], "ou_type_other")

        unknown_kind_visual = partner_model._classify_graph_partner_visuals(
            is_company=True,
            has_parent=False,
            organization_kind_code="surprise_kind",
        )
        self.assertEqual(
            unknown_kind_visual["style_key"], "organization_kind_other_organization"
        )

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
            partner_id=self.company.id,
            include_inactive=True,
        )

        partner_ids = [node["id"] for node in payload["nodes"]]
        relation_ids = [edge["record_id"] for edge in payload["edges"] if edge["openable"]]
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
        graph_state = {
            "partnerId": self.member.id,
            "selectedNodeId": self.company.id,
            "expandedPartnerIds": [self.company.id],
        }
        action = self.member.action_open_relationship_graph(graph_state)

        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "partner_relation_graph.client_action")
        self.assertEqual(action["context"]["default_partner_id"], self.member.id)
        self.assertEqual(action["context"]["default_graph_state"], graph_state)
