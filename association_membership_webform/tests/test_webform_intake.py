import json
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import HttpCase

from ..models.webform_intake import (
    WebformDuplicate,
    WebformError,
    parse_variant_range,
    to_amount,
)

TOKEN_PARAMETER = "association_membership_webform.token"


class WebformFixtureMixin:
    """The association tree and membership products both suites need.

    Split out so the HttpCase can post real payloads rather than only error
    paths: the duplicate refusal has to be exercised over HTTP, because the note
    it leaves behind is written *after* the savepoint rolls back.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "ECOnGOOD Testland e.V."})
        # The regional association under it, the way the live tree is shaped.
        cls.regional = cls.env["res.company"].create({
            "name": "ECOnGOOD Testland Süd e.V.",
            "parent_id": cls.company.id,
        })
        # A company the member has nothing to do with, to prove the parent check.
        cls.foreign_regional = cls.env["res.company"].create({
            "name": "ECOnGOOD Elsewhere Nord e.V.",
        })
        # env.companies raises AccessError when allowed_company_ids is not a
        # subset of the user's companies, and process() uses with_company().
        cls.env.user.company_ids |= cls.company | cls.regional | cls.foreign_regional

        # An individual membership: one variant, no size tiers.
        cls.individual_product = cls.env["product.product"].create({
            "name": "Membership Individual TL",
            "default_code": "MEM_TL_IND_REG",
            "membership_ok": True,
            "membership_partner_type": "person",
            "company_id": cls.company.id,
            "list_price": 75.0,
        })

        # A company membership priced by employee count, built the way the
        # bootstrap builds it: template priced 0, each tier's price in price_extra.
        tier_attribute = cls.env["product.attribute"].create({
            "name": "Variant",
            "value_ids": [
                (0, 0, {"name": "0-10"}),
                (0, 0, {"name": "11-50"}),
                (0, 0, {"name": "51-100"}),
            ],
        })
        cls.company_template = cls.env["product.template"].create({
            "name": "Membership Company TL",
            "membership_ok": True,
            "membership_partner_type": "company",
            "company_id": cls.company.id,
            "list_price": 0.0,
            "attribute_line_ids": [(0, 0, {
                "attribute_id": tier_attribute.id,
                "value_ids": [(6, 0, tier_attribute.value_ids.ids)],
            })],
        })
        variants = cls.company_template.product_variant_ids.sorted(
            lambda v: int(v.product_template_attribute_value_ids.name.split("-")[0])
        )
        cls.tier_small, cls.tier_mid, cls.tier_large = variants
        for variant, code, price in (
            (cls.tier_small, "MEM_TL_COMP_0-10", 120.0),
            (cls.tier_mid, "MEM_TL_COMP_11-50", 360.0),
            (cls.tier_large, "MEM_TL_COMP_51-100", 900.0),
        ):
            variant.default_code = code
            variant.product_template_attribute_value_ids.price_extra = price

        # The regional association's own products: free, and owned by it. Note
        # the _REG suffix is not what marks them regional -- the national
        # individual product carries it too; ownership is the discriminator.
        cls.regional_individual = cls.env["product.product"].create({
            "name": "Individual Regional - Süd",
            "default_code": "MEM_TL_SUED_IND_REG",
            "membership_ok": True,
            "membership_partner_type": "person",
            "company_id": cls.regional.id,
            "list_price": 0.0,
        })
        cls.regional_company_product = cls.env["product.product"].create({
            "name": "Company Regional - Süd",
            "default_code": "MEM_TL_SUED_COMP_REG",
            "membership_ok": True,
            "membership_partner_type": "company",
            "company_id": cls.regional.id,
            "list_price": 0.0,
        })

        cls.intake = cls.env["membership.webform.intake"]

    def _individual_payload(self, **overrides):
        payload = {
            "entry_id": "5001",
            "type": "INDIVIDUAL",
            "first_name": "Manfred",
            "last_name": "Mustermann",
            "email": "manfred@example.org",
            "street": "Musterstraße 42",
            "zip": "80333",
            "city": "München",
            # The select carries an option id, the text field the official name.
            # The garbage value is deliberate: it proves the id is never searched.
            "econ_assoc_select": "63219893D7E43C1499727C6DAFF07",
            "econ_main_assoc": "ECOnGOOD Testland e.V.",
        }
        payload.update(overrides)
        return payload

    def _company_payload(self, **overrides):
        payload = {
            "entry_id": "5002",
            "type": "COMPANY",
            "company_name": "Tech Solutions GmbH",
            "company_email": "info@techsolutions.example",
            "company_employees": "30",
            "contact_first_name": "Anna",
            "contact_last_name": "Schmidt",
            "contact_email": "anna@techsolutions.example",
            "econ_assoc_select": "63219893D7E43C1499727C6DAFF07",
            "econ_main_assoc": "ECOnGOOD Testland e.V.",
        }
        payload.update(overrides)
        return payload


class WebformTestCommon(WebformFixtureMixin, TransactionCase):
    pass


class TestVariantRangeParsing(WebformTestCommon):
    def test_parses_the_label_shapes_the_bootstrap_generates(self):
        self.assertEqual(parse_variant_range("0-1"), (0, 1))
        self.assertEqual(parse_variant_range("11-22"), (11, 22))
        self.assertEqual(parse_variant_range("2"), (2, 2))
        self.assertEqual(parse_variant_range("2501+")[0], 2501)

    def test_returns_none_for_unparseable_labels(self):
        self.assertIsNone(parse_variant_range(""))
        self.assertIsNone(parse_variant_range("Gold"))
        self.assertIsNone(parse_variant_range(None))


class TestCompanyResolution(WebformTestCommon):
    def _resolve(self, payload):
        warnings = []
        national, regional = self.intake._resolve_associations(payload, warnings)
        return national, regional, warnings

    def test_the_national_association_owns_the_membership(self):
        # The regional one used to win. It now gets its own membership instead,
        # so the priced national membership must stay on the national company.
        national, regional, warnings = self._resolve({
            "econ_main_assoc": "ECOnGOOD Testland e.V.",
            "econ_region_assoc": "ECOnGOOD Testland Süd e.V.",
        })
        self.assertEqual(national, self.company)
        self.assertEqual(regional, self.regional)
        self.assertEqual(warnings, [])

    def test_the_opaque_select_id_is_ignored(self):
        # 1596 carries an option id like 63219893D7E43C1499727C6DAFF07; only the
        # official-name field beside it is resolvable.
        national, _regional, _warnings = self._resolve({
            "econ_assoc_select": "63219893D7E43C1499727C6DAFF07",
            "econ_main_assoc": "ECOnGOOD Testland e.V.",
        })
        self.assertEqual(national, self.company)

    def test_an_opaque_id_alone_is_refused(self):
        with self.assertRaises(WebformError) as caught:
            self._resolve({"econ_assoc_select": "2F60448AE25335149BB0AF09B0F1D64D"})
        self.assertEqual(caught.exception.code, "company_unresolved")

    def test_a_named_select_still_works_for_stored_entries(self):
        # Entries stored before the form changed carry only econ_assoc_select,
        # and they must stay replayable.
        national, _regional, _warnings = self._resolve({
            "econ_assoc_select": "ECOnGOOD Testland e.V.",
        })
        self.assertEqual(national, self.company)

    def test_unknown_association_is_refused_not_defaulted(self):
        # Filing a member under the wrong association is worse than refusing.
        with self.assertRaises(WebformError) as caught:
            self._resolve({"econ_main_assoc": "Nowhere e.V."})
        self.assertEqual(caught.exception.code, "company_unresolved")

    def test_an_unresolved_regional_warns_but_keeps_the_signup(self):
        national, regional, warnings = self._resolve({
            "econ_main_assoc": "ECOnGOOD Testland e.V.",
            "econ_region_assoc": "Nowhere Süd e.V.",
        })
        self.assertEqual(national, self.company)
        self.assertFalse(regional)
        self.assertIn("regional_association_unresolved", warnings)

    def test_a_regional_of_another_national_is_refused(self):
        # A mis-picked select would otherwise file the member under a regional
        # association belonging to a different national tree.
        national, regional, warnings = self._resolve({
            "econ_main_assoc": "ECOnGOOD Testland e.V.",
            "econ_region_assoc": "ECOnGOOD Elsewhere Nord e.V.",
        })
        self.assertEqual(national, self.company)
        self.assertFalse(regional)
        self.assertIn("regional_association_mismatch", warnings)

    def test_the_same_name_at_both_levels_is_not_a_regional(self):
        _national, regional, warnings = self._resolve({
            "econ_main_assoc": "ECOnGOOD Testland e.V.",
            "econ_region_assoc": "ECOnGOOD Testland e.V.",
        })
        self.assertFalse(regional)
        self.assertEqual(warnings, [])

    def test_a_wildcard_does_not_match_an_arbitrary_association(self):
        # The ORM wraps an ilike value in %...% without escaping the wildcards in
        # it, so an unescaped "%" would resolve to whichever company came first
        # and file the member under it.
        for wildcard in ("%", "_", "%%%"):
            with self.assertRaises(WebformError) as caught:
                self._resolve({"econ_main_assoc": wildcard})
            self.assertEqual(caught.exception.code, "company_unresolved")

    def test_a_partial_name_still_matches(self):
        # The escaping must not cost the fuzzy match the form relies on.
        national, _regional, _warnings = self._resolve({
            "econ_main_assoc": "Testland e.V.",
        })
        self.assertEqual(national, self.company)


class TestPartnerScoping(WebformTestCommon):
    def _scoped_ids(self, partner):
        if "company_ids" in partner._fields:
            return set(partner.sudo().company_ids.ids)
        return {partner.company_id.id} if partner.company_id else set()

    def test_a_member_is_scoped_to_their_associations(self):
        result = self.intake.process(self._individual_payload(
            econ_region_assoc="ECOnGOOD Testland Süd e.V.",
        ))
        partner = self.env["res.partner"].browse(result["partner_id"])
        scoped = self._scoped_ids(partner)
        if "company_ids" in partner._fields:
            self.assertEqual(scoped, {self.company.id, self.regional.id})
        else:
            # Without base_multi_company there is only company_id, and a member
            # of two associations cannot be expressed; it stays unset.
            self.assertEqual(scoped, set())

    def test_a_national_only_member_is_scoped_to_the_national(self):
        result = self.intake.process(self._individual_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertEqual(self._scoped_ids(partner), {self.company.id})

    def test_scoping_is_a_union_not_a_replacement(self):
        # A second association must not cost the member the first one.
        result = self.intake.process(self._individual_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.intake._scope_partner(partner, [self.foreign_regional])
        scoped = self._scoped_ids(partner)
        if "company_ids" in partner._fields:
            self.assertEqual(scoped, {self.company.id, self.foreign_regional.id})
        else:
            self.assertEqual(scoped, {self.company.id})

    def test_an_existing_affiliation_is_never_dropped(self):
        result = self.intake.process(self._individual_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        if "company_ids" not in partner._fields:
            self.skipTest("base_multi_company is not installed")
        partner.sudo().company_ids = [(4, self.foreign_regional.id)]
        self.intake.process(self._individual_payload())
        self.assertIn(self.foreign_regional.id, self._scoped_ids(partner))


class TestRegionalMembership(WebformTestCommon):
    def _both(self, **overrides):
        return self.intake.process(self._individual_payload(
            econ_region_assoc="ECOnGOOD Testland Süd e.V.", **overrides
        ))

    def test_a_regional_membership_is_created_alongside_the_national_one(self):
        result = self._both()
        Membership = self.env["membership.membership"]
        national = Membership.browse(result["membership_id"])
        regional = Membership.browse(result["regional_membership_id"])
        self.assertEqual(national.company_id, self.company)
        self.assertEqual(regional.company_id, self.regional)
        self.assertEqual(regional.partner_id, national.partner_id)

    def test_the_regional_membership_is_free(self):
        result = self._both()
        regional = self.env["membership.membership"].browse(
            result["regional_membership_id"]
        )
        self.assertEqual(regional.product_id, self.regional_individual)
        self.assertEqual(regional.amount, 0.0)

    def test_the_higher_fee_does_not_leak_into_the_regional_membership(self):
        result = self._both(fee_optional_extra="500")
        Membership = self.env["membership.membership"]
        self.assertEqual(Membership.browse(result["membership_id"]).amount, 500.0)
        self.assertEqual(
            Membership.browse(result["regional_membership_id"]).amount, 0.0
        )

    def test_each_membership_gets_its_own_number(self):
        result = self._both()
        Membership = self.env["membership.membership"]
        national = Membership.browse(result["membership_id"])
        regional = Membership.browse(result["regional_membership_id"])
        self.assertTrue(national.membership_number)
        self.assertTrue(regional.membership_number)
        self.assertNotEqual(national.membership_number, regional.membership_number)

    def test_no_regional_association_means_no_second_membership(self):
        result = self.intake.process(self._individual_payload())
        self.assertIsNone(result["regional_membership_id"])

    def test_a_missing_regional_product_warns_but_keeps_the_national(self):
        # Losing a paid signup because a free bookkeeping record is missing
        # would be the worse failure.
        self.regional_individual.membership_ok = False
        result = self._both()
        self.assertTrue(result["membership_id"])
        self.assertIsNone(result["regional_membership_id"])
        self.assertIn("regional_product_missing", result["warnings"])

    def test_a_priced_regional_product_is_flagged(self):
        self.regional_individual.list_price = 42.0
        result = self._both()
        self.assertIn("regional_product_not_free", result["warnings"])
        regional = self.env["membership.membership"].browse(
            result["regional_membership_id"]
        )
        self.assertEqual(regional.amount, 42.0)

    def test_a_replay_does_not_duplicate_the_regional_membership(self):
        first = self._both()
        second = self._both()
        self.assertEqual(
            first["regional_membership_id"], second["regional_membership_id"]
        )


class TestChapterRelation(WebformTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.has_relations = "res.partner.relation" in cls.env
        if cls.has_relations:
            cls.chapter = cls.env["res.partner"].create({
                "name": "LC Hamburg", "is_company": True,
            })
            cls.relation_type = cls.env["res.partner.relation.type"].create({
                "name": "Local Chapter", "name_inverse": "Member",
            })

    def _relations(self, partner_id):
        return self.env["res.partner.relation"].search([
            ("left_partner_id", "=", partner_id),
            ("right_partner_id", "=", self.chapter.id),
        ])

    def test_the_member_is_linked_to_their_chapter(self):
        if not self.has_relations:
            self.skipTest("partner_multi_relation is not installed")
        result = self.intake.process(
            self._individual_payload(econ_local_chapter="Hamburg")
        )
        relations = self._relations(result["partner_id"])
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations.type_id, self.relation_type)

    def test_a_replay_does_not_duplicate_the_relation(self):
        if not self.has_relations:
            self.skipTest("partner_multi_relation is not installed")
        payload = self._individual_payload(econ_local_chapter="Hamburg")
        result = self.intake.process(payload)
        self.intake.process(payload)
        self.assertEqual(len(self._relations(result["partner_id"])), 1)

    def test_without_the_module_it_warns_instead_of_failing(self):
        if self.has_relations:
            self.skipTest("partner_multi_relation is installed")
        result = self.intake.process(
            self._individual_payload(econ_local_chapter="Hamburg")
        )
        self.assertTrue(result["membership_id"])


class TestLocalChapterResolution(WebformTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.chapter = cls.env["res.partner"].create({
            "name": "LC Hamburg",
            "is_company": True,
        })
        if "is_econgood_ou" in cls.env["res.partner"]._fields:
            cls.chapter.is_econgood_ou = True

    def _resolve(self, value):
        warnings = []
        chapter = self.intake._resolve_local_chapter(
            {"econ_local_chapter": value}, warnings
        )
        return chapter, warnings

    def test_the_bare_name_finds_the_prefixed_partner(self):
        # The form sends "Hamburg"; Odoo stores "LC Hamburg".
        chapter, warnings = self._resolve("Hamburg")
        self.assertEqual(chapter, self.chapter)
        self.assertEqual(warnings, [])

    def test_the_canonical_name_also_works(self):
        chapter, _warnings = self._resolve("LC Hamburg")
        self.assertEqual(chapter, self.chapter)

    def test_an_unknown_chapter_warns_and_creates_nothing(self):
        before = self.env["res.partner"].search_count([])
        chapter, warnings = self._resolve("Atlantis")
        self.assertFalse(chapter)
        self.assertIn("chapter_unresolved", warnings)
        self.assertEqual(self.env["res.partner"].search_count([]), before)

    def test_the_opaque_chapter_select_is_ignored(self):
        warnings = []
        chapter = self.intake._resolve_local_chapter(
            {"econ_chapter_select": "2F60448AE25335149BB0AF09B0F1D64D"}, warnings
        )
        self.assertFalse(chapter)
        self.assertEqual(warnings, [])

    def test_no_chapter_given_is_not_a_warning(self):
        chapter, warnings = self._resolve("")
        self.assertFalse(chapter)
        self.assertEqual(warnings, [])


class TestTypeCode(WebformTestCommon):
    def test_reads_the_form_select_in_either_language(self):
        self.assertEqual(self.intake._resolve_type_code({"type": "INDIVIDUAL"}), "IND")
        self.assertEqual(self.intake._resolve_type_code({"type": "Unternehmen"}), "COMP")
        self.assertEqual(self.intake._resolve_type_code({"type": "Verein"}), "ORG")

    def test_a_charitable_company_is_priced_as_an_organisation(self):
        code = self.intake._resolve_type_code({
            "type": "COMPANY", "assoc_is_charitable": "1",
        })
        self.assertEqual(code, "ORG")

    def test_falls_back_to_whichever_block_carries_a_name(self):
        self.assertEqual(self.intake._resolve_type_code({"assoc_name": "X e.V."}), "ORG")
        self.assertEqual(self.intake._resolve_type_code({"company_name": "X GmbH"}), "COMP")


class TestIntakeIndividual(WebformTestCommon):
    def test_creates_a_waiting_membership_without_a_period(self):
        result = self.intake.process(self._individual_payload())
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.state, "waiting")
        self.assertFalse(membership.period_ids, "periods belong to activation")
        self.assertEqual(membership.product_id, self.individual_product)
        self.assertEqual(membership.amount, 75.0)

    def test_partner_carries_the_form_data(self):
        result = self.intake.process(self._individual_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertEqual(partner.name, "Manfred Mustermann")
        self.assertFalse(partner.is_company)
        self.assertEqual(partner.city, "München")
        self.assertEqual(partner.legacy_id_formidable, "5001")

    def test_consent_dates_are_recorded_when_ticked(self):
        result = self.intake.process(self._individual_payload(
            data_protection_agreement="1", code_of_conduct="1",
        ))
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertTrue(partner.privacy_agreement_signed_date)
        self.assertTrue(partner.code_of_conduct_signed_date)

    def test_unticked_consent_leaves_the_dates_empty(self):
        result = self.intake.process(self._individual_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertFalse(partner.privacy_agreement_signed_date)
        self.assertFalse(partner.code_of_conduct_signed_date)

    def test_a_charitable_organisation_is_written_not_rejected(self):
        # nonprofit_status is a selection of unknown / confirmed / not_nonprofit.
        # Writing any other value raises a plain ValueError, which the controller
        # does not classify as a rejection, so it escaped as a 500.
        result = self.intake.process({
            "entry_id": "5003",
            "type": "ASSOCIATION",
            "assoc_name": "Gemeinnütziger Verein e.V.",
            "assoc_email": "kontakt@verein.example",
            "assoc_employees_fte": "8",
            "assoc_is_charitable": "1",
            "econ_assoc_select": "ECOnGOOD Testland e.V.",
        })
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertEqual(partner.name, "Gemeinnütziger Verein e.V.")
        self.assertEqual(partner.nonprofit_status, "confirmed")

    def test_a_charitable_company_keeps_its_company_block_data(self):
        # Priced as an organisation, but the data is still in the company block.
        # Following the type code instead of the data lost name and address.
        result = self.intake.process(self._company_payload(assoc_is_charitable="1"))
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertEqual(partner.name, "Tech Solutions GmbH")
        self.assertEqual(partner.email, "info@techsolutions.example")
        self.assertEqual(partner.nonprofit_status, "confirmed")

    def test_a_non_writable_field_is_dropped_rather_than_raising(self):
        # is_municipality is computed and not stored; _filter_fields must refuse
        # it even though the key exists on the model.
        kept = self.intake._filter_fields(
            "res.partner", {"employee_count": 5, "is_municipality": True},
        )
        self.assertEqual(kept, {"employee_count": 5})


class TestIntakeOrganisation(WebformTestCommon):
    def test_contact_person_becomes_a_child_partner(self):
        result = self.intake.process(self._company_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertTrue(partner.is_company)
        contact = partner.child_ids
        self.assertEqual(len(contact), 1)
        self.assertEqual(contact.name, "Anna Schmidt")
        self.assertEqual(contact.email, "anna@techsolutions.example")

    def test_organisation_kind_follows_the_type_code(self):
        """15.35: a new organisation gets the kind of its type code."""
        result = self.intake.process(self._company_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertEqual(
            partner.organization_kind_id,
            self.env.ref("econgood_extra_fields.res_partner_organization_kind_company"),
        )

    def test_organisation_kind_chosen_by_staff_is_kept(self):
        other = self.env.ref("econgood_extra_fields.res_partner_organization_kind_other_organization")
        first = self.intake.process(self._company_payload())
        partner = self.env["res.partner"].browse(first["partner_id"])
        partner.organization_kind_id = other
        again = self.intake._upsert_partner(self._company_payload(), self.env.company, "COMP")
        self.assertEqual(again, partner)
        self.assertEqual(partner.organization_kind_id, other)

    def test_invoice_partner_falls_back_to_the_member(self):
        # membership.create applies apply_invoice_partner_default, so this holds
        # over RPC even though no onchange runs. With no invoice contact,
        # address_get resolves to the member itself.
        result = self.intake.process(self._company_payload())
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.invoice_partner_id.id, result["partner_id"])


class TestInvoiceContact(WebformTestCommon):
    def _invoice_child(self, partner_id):
        return self.env["res.partner"].search([
            ("parent_id", "=", partner_id), ("type", "=", "invoice"),
        ])

    def test_an_invoice_email_creates_the_contact_and_wires_the_membership(self):
        result = self.intake.process(self._company_payload(
            invoice_email="rechnung@techsolutions.example",
        ))
        child = self._invoice_child(result["partner_id"])
        self.assertEqual(len(child), 1)
        self.assertEqual(child.email, "rechnung@techsolutions.example")
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.invoice_partner_id, child)

    def test_the_email_alone_names_it_after_the_member(self):
        result = self.intake.process(self._company_payload(
            invoice_email="rechnung@techsolutions.example",
        ))
        child = self._invoice_child(result["partner_id"])
        self.assertEqual(child.name, "Invoice Address - Tech Solutions GmbH")

    def test_a_submitted_name_is_used_when_both_parts_are_present(self):
        result = self.intake.process(self._company_payload(
            invoice_email="rechnung@techsolutions.example",
            invoice_first_name="Bernd",
            invoice_last_name="Buchhalter",
        ))
        self.assertEqual(self._invoice_child(result["partner_id"]).name, "Bernd Buchhalter")

    def test_the_address_is_inherited_then_overridden_field_by_field(self):
        result = self.intake.process(self._company_payload(
            company_street="Hauptstraße 1",
            company_city="Wien",
            invoice_email="rechnung@techsolutions.example",
            invoice_city="Graz",
        ))
        child = self._invoice_child(result["partner_id"])
        self.assertEqual(child.street, "Hauptstraße 1")
        self.assertEqual(child.city, "Graz")

    def test_a_replay_updates_the_contact_rather_than_adding_a_second(self):
        # Matching on the slot rather than the email is what makes a corrected
        # invoice address update in place.
        payload = self._company_payload(invoice_email="rechnung@techsolutions.example")
        first = self.intake.process(payload)
        payload["invoice_email"] = "buchhaltung@techsolutions.example"
        self.intake.process(payload)
        child = self._invoice_child(first["partner_id"])
        self.assertEqual(len(child), 1)
        self.assertEqual(child.email, "buchhaltung@techsolutions.example")

    def test_an_invoice_contact_arriving_later_is_still_wired_up(self):
        # write() does not apply the invoice-partner default, so without an
        # explicit fix a corrected submission left the membership pointing at
        # the member.
        first = self.intake.process(self._company_payload())
        membership = self.env["membership.membership"].browse(first["membership_id"])
        self.assertEqual(membership.invoice_partner_id.id, first["partner_id"])

        self.intake.process(self._company_payload(
            invoice_email="rechnung@techsolutions.example",
        ))
        child = self._invoice_child(first["partner_id"])
        self.assertEqual(membership.invoice_partner_id, child)

    def test_the_oca_invoice_address_stays_empty(self):
        """15.26: the invoice-type child is the invoice address, nothing else."""
        result = self.intake.process(self._company_payload(
            invoice_email="rechnung@techsolutions.example",
        ))
        partner = self.env["res.partner"].browse(result["partner_id"])
        if "partner_invoice_id" in partner._fields:
            self.assertFalse(partner.partner_invoice_id)
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.invoice_partner_id, self._invoice_child(partner.id))

    def test_no_invoice_email_creates_nothing(self):
        result = self.intake.process(self._company_payload())
        self.assertFalse(self._invoice_child(result["partner_id"]))

    def test_communication_partners_resolve_to_someone(self):
        result = self.intake.process(self._company_payload())
        membership = self.env["membership.membership"].browse(result["membership_id"])
        recipients = membership._get_communication_partners()
        self.assertTrue(recipients)
        if "partner_contact_id" in self.env["res.partner"]._fields:
            # With partner_contact_address_default installed the named contact
            # person must receive member emails, not the head-office address.
            self.assertEqual(recipients, membership.partner_id.child_ids)
        else:
            self.assertIn("contact_person_not_linked", result["warnings"])

    def test_employee_count_picks_the_covering_tier(self):
        result = self.intake.process(self._company_payload(company_employees="30"))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.product_id, self.tier_mid)
        self.assertEqual(membership.amount, 360.0)
        self.assertNotIn("tier_clamped", result["warnings"])

    def test_tier_is_inferred_without_any_product_label(self):
        payload = self._company_payload(company_employees="5")
        payload.pop("membership_type", None)
        result = self.intake.process(payload)
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.product_id, self.tier_small)


class TestTierClamping(WebformTestCommon):
    def test_a_count_above_every_tier_clamps_to_the_top_and_warns(self):
        # Mirrors the live gap where no variant covers 501 employees.
        result = self.intake.process(self._company_payload(company_employees="501"))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.product_id, self.tier_large)
        self.assertIn("tier_clamped", result["warnings"])

    def test_a_clamped_signup_says_so_in_the_chatter(self):
        result = self.intake.process(self._company_payload(company_employees="501"))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        bodies = " ".join(membership.message_ids.mapped("body"))
        self.assertIn("tier_clamped", bodies)

    def test_a_missing_employee_count_takes_the_lowest_tier_and_warns(self):
        payload = self._company_payload()
        payload.pop("company_employees")
        result = self.intake.process(payload)
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.product_id, self.tier_small)
        self.assertIn("employee_count_missing", result["warnings"])

    def test_no_matching_product_is_refused(self):
        self.individual_product.membership_ok = False
        self.company_template.membership_ok = False
        with self.assertRaises(WebformError) as caught:
            self.intake.process(self._individual_payload())
        self.assertEqual(caught.exception.code, "product_unresolved")

    def test_partner_type_is_enforced_by_the_module_domain(self):
        # Only a company product exists, but the payload is an individual.
        self.individual_product.membership_ok = False
        with self.assertRaises(WebformError) as caught:
            self.intake.process(self._individual_payload())
        self.assertEqual(caught.exception.code, "product_unresolved")


class TestExplicitProductReference(WebformTestCommon):
    def test_an_internal_reference_wins_over_inference(self):
        result = self.intake.process(self._company_payload(
            company_employees="5", membership_product_ref="MEM_TL_COMP_51-100",
        ))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.product_id, self.tier_large)

    def test_an_unknown_reference_warns_and_falls_back_to_inference(self):
        result = self.intake.process(self._company_payload(
            company_employees="5", membership_product_ref="MEM_NOPE",
        ))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.product_id, self.tier_small)
        self.assertIn("product_ref_unknown", result["warnings"])


class TestHigherFee(WebformTestCommon):
    def test_a_higher_optional_fee_overrides_the_tier_price(self):
        result = self.intake.process(self._individual_payload(fee_optional_extra="150"))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.amount, 150.0)

    def test_a_lower_optional_fee_is_ignored(self):
        result = self.intake.process(self._individual_payload(fee_optional_extra="10"))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.amount, 75.0)


class TestBankAndPayment(WebformTestCommon):
    def test_iban_lands_on_the_partner_as_a_bank_account(self):
        result = self.intake.process(self._individual_payload(
            iban="DE89 3704 0044 0532 0130 00", bic="COBADEFFXXX",
        ))
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertEqual(partner.bank_ids.acc_number, "DE89370400440532013000")

    def test_the_same_iban_is_not_attached_twice(self):
        payload = self._individual_payload(iban="DE89 3704 0044 0532 0130 00")
        self.intake.process(payload)
        result = self.intake.process(payload)
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertEqual(len(partner.bank_ids), 1)

    def test_a_reformatted_iban_is_still_recognised_on_replay(self):
        # base_iban rewrites acc_number into its spaced form, so a lookup on the
        # compacted value only matches via sanitized_acc_number.
        payload = self._individual_payload(iban="DE89 3704 0044 0532 0130 00")
        first = self.intake.process(payload)
        partner = self.env["res.partner"].browse(first["partner_id"])
        partner.bank_ids.acc_number = "DE89 3704 0044 0532 0130 00"
        self.intake.process(payload)
        self.assertEqual(len(partner.bank_ids), 1)

    def test_a_stated_payment_method_with_no_mode_configured_warns(self):
        result = self.intake.process(self._individual_payload(
            payment_method="SEPA Lastschrift",
        ))
        # The test company has no payment modes, so this must not pass silently.
        self.assertIn("payment_mode_unavailable", result["warnings"])

    def test_the_stated_method_is_kept_even_with_no_mode_configured(self):
        # Every association except the configured one used to lose this answer.
        result = self.intake.process(
            self._individual_payload(payment_method="Lastschrift")
        )
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertIn("Payment method: Lastschrift", partner.message_ids.mapped("body")[0])
        # The gap is still reported: it is a configuration problem, not noise.
        self.assertIn("payment_mode_unavailable", result["warnings"])

    def test_a_non_yearly_frequency_is_flagged(self):
        # Billing is one period per calendar year, so anything else needs a human.
        result = self.intake.process(self._individual_payload(payment_frequency="monatlich"))
        self.assertIn("payment_frequency_not_yearly", result["warnings"])

    def test_yearly_is_accepted_in_either_language(self):
        for value in ("yearly", "jährlich", "annual"):
            result = self.intake.process(self._individual_payload(payment_frequency=value))
            self.assertNotIn("payment_frequency_not_yearly", result["warnings"])


class TestChatter(WebformTestCommon):
    def test_the_donation_amount_is_logged_not_dropped(self):
        result = self.intake.process(self._individual_payload(
            donation_amount="50", currency="EUR",
        ))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        bodies = " ".join(membership.message_ids.mapped("body"))
        self.assertIn("50", bodies)

    def test_the_body_is_real_html_not_escaped_tags(self):
        result = self.intake.process(self._individual_payload(note_to_econgood="Hello"))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        body = membership.message_ids[0].body
        self.assertIn("<li>", body)
        self.assertNotIn("&lt;li&gt;", body)

    def test_submitted_html_cannot_inject_into_the_chatter(self):
        result = self.intake.process(self._individual_payload(
            note_to_econgood='<img src=x onerror="alert(1)">',
        ))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        body = membership.message_ids[0].body
        self.assertNotIn("<img", body)
        self.assertIn("&lt;img", body)

    def _bodies(self, record):
        return " ".join(record.message_ids.mapped("body"))

    def test_the_note_to_econgood_goes_on_the_membership(self):
        result = self.intake.process(self._individual_payload(
            service_newsletter="1",
            service_website_listing="1",
            note_to_econgood="Please call me.",
        ))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertIn("Please call me.", self._bodies(membership))

    def test_the_service_opt_ins_go_on_the_member(self):
        # They describe the member, not the membership.
        result = self.intake.process(self._individual_payload(
            service_newsletter="1",
            service_website_listing="1",
            note_to_econgood="Please call me.",
        ))
        partner = self.env["res.partner"].browse(result["partner_id"])
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertIn("Newsletter", self._bodies(partner))
        self.assertNotIn("Newsletter", self._bodies(membership))
        self.assertNotIn("Please call me.", self._bodies(partner))

    def test_the_privacy_notice_is_still_recorded(self):
        # 1604 is the notice and has no field of its own -- only 2458, the
        # agreement, maps to a date. This line is the only record of it.
        result = self.intake.process(
            self._individual_payload(data_protection_notice="1")
        )
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertIn("Privacy notice", self._bodies(partner))

    def test_a_replay_does_not_pile_up_identical_notes(self):
        payload = self._individual_payload(note_to_econgood="Please call me.")
        result = self.intake.process(payload)
        membership = self.env["membership.membership"].browse(result["membership_id"])
        before = len(membership.message_ids)
        self.intake.process(payload)
        self.intake.process(payload)
        self.assertEqual(len(membership.message_ids), before)


class TestAmountParsing(WebformTestCommon):
    def test_reads_the_shapes_the_form_produces(self):
        self.assertEqual(to_amount("600"), 600.0)
        self.assertEqual(to_amount("600,00"), 600.0)
        self.assertEqual(to_amount("600,00 €"), 600.0)
        self.assertEqual(to_amount("1.200,50"), 1200.5)
        self.assertEqual(to_amount("1,200.50"), 1200.5)
        self.assertEqual(to_amount("1.200.000"), 1200000.0)
        self.assertEqual(to_amount("0,50"), 0.5)

    def test_a_lone_separator_with_three_digits_is_thousands(self):
        # 1.200 is 1200 to a German reader and 1.2 to an English one. A
        # membership fee of 1.20 written that way is not a thing; 1200 is.
        self.assertEqual(to_amount("1.200"), 1200.0)
        self.assertEqual(to_amount("1,200"), 1200.0)

    def test_unparseable_values_fall_back(self):
        self.assertIsNone(to_amount(""))
        self.assertIsNone(to_amount("on request"))
        self.assertEqual(to_amount(None, default=0.0), 0.0)


class TestFeeInference(WebformTestCommon):
    def test_the_submitted_fee_breaks_a_tie_between_types(self):
        # Two company templates both accept the member; only the fee separates
        # them, and the structural inference has nothing left to go on.
        rival = self.env["product.product"].create({
            "name": "Membership Company TL Premium",
            "default_code": "MEM_TL_COMP_PREMIUM",
            "membership_ok": True,
            "membership_partner_type": "company",
            "company_id": self.company.id,
            "list_price": 4242.0,
        })
        result = self.intake.process(self._company_payload(fee_normal="4.242,00"))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.product_id, rival)

    def test_a_disagreeing_fee_is_flagged_and_named(self):
        result = self.intake.process(self._company_payload(fee_normal="999,00"))
        self.assertIn("fee_mismatch", result["warnings"])
        membership = self.env["membership.membership"].browse(result["membership_id"])
        body = " ".join(membership.message_ids.mapped("body"))
        self.assertIn("999", body)
        self.assertIn("360", body)

    def test_a_matching_fee_is_not_flagged(self):
        result = self.intake.process(self._company_payload(fee_normal="360,00"))
        self.assertNotIn("fee_mismatch", result["warnings"])

    def test_the_higher_optional_fee_does_not_count_as_a_mismatch(self):
        # fee_optional_extra legitimately raises the amount; comparing against
        # the amount rather than the product price would flag every donor.
        result = self.intake.process(self._company_payload(
            fee_normal="360,00", fee_optional_extra="500",
        ))
        self.assertNotIn("fee_mismatch", result["warnings"])
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertEqual(membership.amount, 500.0)


class TestIdempotency(WebformTestCommon):
    def test_the_same_entry_twice_gives_one_partner_and_one_membership(self):
        payload = self._individual_payload()
        first = self.intake.process(payload)
        second = self.intake.process(payload)
        self.assertEqual(first["partner_id"], second["partner_id"])
        self.assertEqual(first["membership_id"], second["membership_id"])

    def test_a_second_entry_id_for_the_same_person_is_refused(self):
        # A new entry for someone who already has an open membership is a second
        # signup, not a correction, and re-running the import would silently
        # overwrite whatever staff had already changed.
        first = self.intake.process(self._individual_payload())
        with self.assertRaises(WebformDuplicate) as caught:
            self.intake.process(self._individual_payload(entry_id="9999"))
        self.assertEqual(caught.exception.code, "duplicate_signup")
        self.assertEqual(caught.exception.membership_id, first["membership_id"])
        self.assertEqual(caught.exception.partner_id, first["partner_id"])

    def test_the_duplicate_check_writes_nothing(self):
        self.intake.process(self._individual_payload())
        partners_before = self.env["res.partner"].search_count([])
        memberships_before = self.env["membership.membership"].search_count([])
        with self.assertRaises(WebformDuplicate):
            self.intake.process(self._individual_payload(entry_id="9999"))
        self.assertEqual(self.env["res.partner"].search_count([]), partners_before)
        self.assertEqual(
            self.env["membership.membership"].search_count([]), memberships_before
        )

    def test_force_duplicate_lets_a_genuine_second_membership_through(self):
        self.intake.process(self._individual_payload())
        result = self.intake.process(
            self._individual_payload(entry_id="9999", force_duplicate="1")
        )
        self.assertTrue(result["membership_id"])

    def test_rejoining_after_termination_is_allowed(self):
        # A former member signing up again must not be refused as a duplicate.
        # The old membership is backdated so it does not overlap the new one --
        # _check_date_overlap counts terminated records too, so a same-day
        # rejoin is refused by the module itself, not by the duplicate check.
        first = self.intake.process(self._individual_payload())
        membership = self.env["membership.membership"].browse(first["membership_id"])
        membership.write({"date_start": "2020-01-01"})
        membership.action_activate_direct()
        membership.action_cancel_direct(
            date_end="2020-12-31", cancel_reason="Test termination",
        )
        self.assertEqual(membership.state, "terminated")

        result = self.intake.process(self._individual_payload(entry_id="9999"))
        self.assertNotEqual(result["membership_id"], first["membership_id"])

    def test_a_replay_of_the_same_entry_is_not_a_duplicate(self):
        payload = self._individual_payload()
        first = self.intake.process(payload)
        second = self.intake.process(payload)
        self.assertEqual(first["membership_id"], second["membership_id"])

    def test_a_matching_email_with_a_different_name_is_not_merged(self):
        # Shared family and office addresses make email alone unsafe as identity.
        self.intake.process(self._individual_payload())
        self.intake.process(self._individual_payload(
            entry_id="9998", first_name="Erika", last_name="Musterfrau",
        ))
        partners = self.env["res.partner"].search([
            ("email", "=ilike", "manfred@example.org"),
        ])
        self.assertEqual(len(partners), 2)

    def test_a_tier_change_updates_the_membership_instead_of_duplicating(self):
        first = self.intake.process(self._company_payload(company_employees="5"))
        second = self.intake.process(self._company_payload(company_employees="30"))
        self.assertEqual(first["membership_id"], second["membership_id"])
        membership = self.env["membership.membership"].browse(second["membership_id"])
        self.assertEqual(membership.product_id, self.tier_mid)


@tagged("post_install", "-at_install")
class TestWebformEndpoint(WebformFixtureMixin, HttpCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(TOKEN_PARAMETER, "s3cret-token")

    def _post(self, body, token="s3cret-token"):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Webform-Token"] = token
        return self.url_open(
            "/membership/webform/submit",
            data=json.dumps(body),
            headers=headers,
        )

    def test_a_missing_token_is_rejected(self):
        self.assertEqual(self._post({}, token=None).status_code, 401)

    def test_a_wrong_token_is_rejected(self):
        self.assertEqual(self._post({}, token="nope").status_code, 401)

    def test_a_non_ascii_token_is_rejected_not_a_server_error(self):
        # hmac.compare_digest refuses to compare str above U+007F, so comparing
        # the header as text turned an unauthenticated request into a 500.
        self.assertEqual(self._post({}, token="pässwörd").status_code, 401)
        self.assertEqual(self._post({}, token="s3cret-tokeñ").status_code, 401)

    def test_an_oversized_body_is_refused(self):
        response = self._post({"entry_id": "1", "note_to_econgood": "x" * 300_000})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "payload_too_large")

    def test_a_duplicate_is_refused_and_the_note_survives_the_rollback(self):
        accepted = self._post(self._individual_payload())
        self.assertEqual(accepted.status_code, 200)
        membership_id = accepted.json()["membership_id"]

        partners_before = self.env["res.partner"].search_count([])
        response = self._post(self._individual_payload(entry_id="9999"))
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["error"], "duplicate_signup")
        self.assertEqual(body["membership_id"], membership_id)

        # Nothing was written...
        self.assertEqual(self.env["res.partner"].search_count([]), partners_before)
        # ...but the note was, because it is posted after the savepoint rolls
        # back. Posting it inside would have discarded it with everything else.
        membership = self.env["membership.membership"].browse(membership_id)
        bodies = " ".join(membership.message_ids.mapped("body"))
        self.assertIn("duplicate", bodies.lower())
        self.assertIn("9999", bodies)

    def test_a_charitable_organisation_is_accepted_not_a_500(self):
        response = self._post(self._company_payload(
            entry_id="7001", assoc_is_charitable="1",
        ))
        self.assertEqual(response.status_code, 200)

    def test_an_unset_parameter_closes_the_endpoint(self):
        self.env["ir.config_parameter"].sudo().set_param(TOKEN_PARAMETER, "")
        self.assertEqual(self._post({}, token="s3cret-token").status_code, 401)

    def test_a_malformed_body_is_a_400(self):
        response = self.url_open(
            "/membership/webform/submit",
            data="not json",
            headers={"Content-Type": "application/json", "X-Webform-Token": "s3cret-token"},
        )
        self.assertEqual(response.status_code, 400)

    def test_an_unmappable_payload_is_a_422_with_a_code(self):
        response = self._post({"type": "INDIVIDUAL", "econ_assoc_select": "Nowhere"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "company_unresolved")

    def test_an_odoo_constraint_is_reported_as_422_not_500(self):
        # The real cases are setup problems rather than bugs, so they must come
        # back as something the operator can act on rather than an opaque 500.
        intake = type(self.env["membership.webform.intake"])

        def raise_constraint(self, payload):
            raise ValidationError("The membership number must be globally unique.")

        with patch.object(intake, "process", raise_constraint):
            response = self._post({"type": "INDIVIDUAL"})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["error"], "rejected_by_odoo")
        self.assertIn("globally unique", body["message"])

    def test_an_unexpected_error_is_still_a_500(self):
        intake = type(self.env["membership.webform.intake"])

        def explode(self, payload):
            raise KeyError("boom")

        with patch.object(intake, "process", explode):
            response = self._post({"type": "INDIVIDUAL"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "internal_error")

    def test_a_rejected_payload_leaves_no_partner_behind(self):
        self._post({
            "type": "INDIVIDUAL",
            "first_name": "Ghost",
            "last_name": "Record",
            "email": "ghost@example.org",
            "econ_assoc_select": "Nowhere",
        })
        self.assertFalse(
            self.env["res.partner"].search([("email", "=ilike", "ghost@example.org")])
        )
