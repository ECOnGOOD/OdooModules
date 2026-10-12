from datetime import timedelta

from odoo import fields
from odoo.tests import new_test_user
from odoo.tests.common import TransactionCase


class TestChapterMemberReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.today = fields.Date.context_today(Partner)
        cls.company_a = cls.env["res.company"].create({"name": "Regional A e.V."})
        cls.company_b = cls.env["res.company"].create({"name": "Regional B e.V."})

        def ou(name, xmlid, companies):
            return Partner.create({
                "name": name,
                "is_company": True,
                "is_econgood_ou": True,
                "ou_type_id": cls.env.ref(f"econgood_extra_fields.{xmlid}").id,
                "company_ids": [(6, 0, companies.ids)],
            })

        cls.region = ou("Regional A", "res_partner_ou_type_regional_association", cls.company_a)
        cls.chapter = ou("LC Alpha", "res_partner_ou_type_local_chapter", cls.company_a)
        cls.other_chapter = ou("LC Beta", "res_partner_ou_type_local_chapter", cls.company_b)

        RelationType = cls.env["res.partner.relation.type"]
        cls.child_ou = RelationType.create({"name": "child OU", "name_inverse": "parent OU"})
        cls.chapter_type = RelationType.create({"name": "Local Chapter", "name_inverse": "Member"})

        cls.person = Partner.create({"name": "Anna Member"})
        cls.organisation = Partner.create({"name": "Member GmbH", "is_company": True})
        cls.former = Partner.create({"name": "Former Member"})

        Relation = cls.env["res.partner.relation"]
        Relation.create({
            "left_partner_id": cls.chapter.id,
            "right_partner_id": cls.region.id,
            "type_id": cls.child_ou.id,
        })
        cls.person_link = Relation.create({
            "left_partner_id": cls.person.id,
            "right_partner_id": cls.chapter.id,
            "type_id": cls.chapter_type.id,
            "date_start": cls.today - timedelta(days=30),
        })
        # Recorded the other way round, as staff may do through the inverse type.
        cls.organisation_link = Relation.create({
            "left_partner_id": cls.chapter.id,
            "right_partner_id": cls.organisation.id,
            "type_id": cls.chapter_type.id,
        })
        cls.former_link = Relation.create({
            "left_partner_id": cls.former.id,
            "right_partner_id": cls.chapter.id,
            "type_id": cls.chapter_type.id,
            "date_start": cls.today - timedelta(days=400),
            "date_end": cls.today - timedelta(days=1),
        })
        cls.beta_link = Relation.create({
            "left_partner_id": cls.person.id,
            "right_partner_id": cls.other_chapter.id,
            "type_id": cls.chapter_type.id,
        })

    def _rows(self, env=None, domain=None):
        env = env or self.env
        env.flush_all()
        return env["econgood.chapter.member.report"].search(domain or [])

    def test_one_row_per_chapter_relation_in_either_orientation(self):
        rows = self._rows(domain=[("chapter_id", "=", self.chapter.id)])
        self.assertEqual(
            set(rows.mapped("relation_id").ids),
            {self.person_link.id, self.organisation_link.id, self.former_link.id},
        )
        self.assertEqual(
            set(rows.mapped("member_id").ids),
            {self.person.id, self.organisation.id, self.former.id},
        )

    def test_the_relation_to_the_parent_ou_is_not_a_member(self):
        rows = self._rows()
        self.assertNotIn(self.region, rows.mapped("member_id"))

    def test_an_association_company_partner_is_an_ou_even_unmarked(self):
        federation = self.env["res.company"].create({"name": "Federation e.V."}).partner_id
        self.assertFalse(federation.is_econgood_ou)
        self.env["res.partner.relation"].create({
            "left_partner_id": self.other_chapter.id,
            "right_partner_id": federation.id,
            "type_id": self.child_ou.id,
        })
        rows = self._rows(domain=[("chapter_id", "=", self.other_chapter.id)])
        self.assertNotIn(federation, rows.mapped("member_id"))
        self.assertEqual(rows.mapped("parent_ou_id"), federation)

    def test_the_parent_ou_comes_from_the_child_ou_relation(self):
        rows = self._rows(domain=[("chapter_id", "=", self.chapter.id)])
        self.assertEqual(rows.mapped("parent_ou_id"), self.region)
        beta = self._rows(domain=[("chapter_id", "=", self.other_chapter.id)])
        self.assertFalse(beta.parent_ou_id)

    def test_only_open_relations_are_current(self):
        rows = self._rows(domain=[("chapter_id", "=", self.chapter.id), ("is_current", "=", True)])
        self.assertEqual(set(rows.mapped("member_id").ids), {self.person.id, self.organisation.id})

    def test_organisations_are_marked(self):
        row = self._rows(domain=[("relation_id", "=", self.organisation_link.id)])
        self.assertTrue(row.member_is_company)

    def test_a_company_sees_only_the_chapters_visible_to_it(self):
        user = new_test_user(
            self.env,
            login="chapter_report_user",
            groups="base.group_user",
            company_id=self.company_a.id,
            company_ids=[(6, 0, self.company_a.ids)],
        )
        rows = self._rows(env=self.env(user=user, context={"allowed_company_ids": self.company_a.ids}))
        self.assertIn(self.chapter, rows.mapped("chapter_id"))
        self.assertNotIn(self.other_chapter, rows.mapped("chapter_id"))
