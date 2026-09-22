import json
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import HttpCase

from ..models.webform_intake import WebformError, parse_variant_range

TOKEN_PARAMETER = "association_membership_webform.token"


class WebformTestCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "ECOnGOOD Testland e.V."})
        cls.env.user.company_ids |= cls.company

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
            "econ_assoc_select": "ECOnGOOD Testland e.V.",
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
            "econ_assoc_select": "ECOnGOOD Testland e.V.",
        }
        payload.update(overrides)
        return payload


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
    def test_regional_association_wins_over_the_national_one(self):
        regional = self.env["res.company"].create({"name": "ECOnGOOD Testland Süd"})
        company = self.intake._resolve_company({
            "econ_assoc_select": "ECOnGOOD Testland e.V.",
            "econ_region_assoc": "ECOnGOOD Testland Süd",
        })
        self.assertEqual(company, regional)

    def test_unknown_association_is_refused_not_defaulted(self):
        # Filing a member under the wrong association is worse than refusing.
        with self.assertRaises(WebformError) as caught:
            self.intake._resolve_company({"econ_assoc_select": "Nowhere e.V."})
        self.assertEqual(caught.exception.code, "company_unresolved")

    def test_a_wildcard_does_not_match_an_arbitrary_association(self):
        # The ORM wraps an ilike value in %...% without escaping the wildcards in
        # it, so an unescaped "%" would resolve to whichever company came first
        # and file the member under it.
        for wildcard in ("%", "_", "%%%"):
            with self.assertRaises(WebformError) as caught:
                self.intake._resolve_company({"econ_assoc_select": wildcard})
            self.assertEqual(caught.exception.code, "company_unresolved")

    def test_a_partial_name_still_matches(self):
        # The escaping must not cost the fuzzy match the form relies on.
        company = self.intake._resolve_company({"econ_assoc_select": "Testland e.V."})
        self.assertEqual(company, self.company)


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


class TestIntakeOrganisation(WebformTestCommon):
    def test_contact_person_becomes_a_child_partner(self):
        result = self.intake.process(self._company_payload())
        partner = self.env["res.partner"].browse(result["partner_id"])
        self.assertTrue(partner.is_company)
        contact = partner.child_ids
        self.assertEqual(len(contact), 1)
        self.assertEqual(contact.name, "Anna Schmidt")
        self.assertEqual(contact.email, "anna@techsolutions.example")

    def test_invoice_partner_is_resolved_on_create(self):
        # membership.create applies apply_invoice_partner_default, so this holds
        # over RPC even though no onchange runs.
        result = self.intake.process(self._company_payload())
        membership = self.env["membership.membership"].browse(result["membership_id"])
        self.assertTrue(membership.invoice_partner_id)

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

    def test_service_opt_ins_and_the_note_are_logged(self):
        result = self.intake.process(self._individual_payload(
            service_newsletter="1",
            service_website_listing="1",
            note_to_econgood="Please call me.",
        ))
        membership = self.env["membership.membership"].browse(result["membership_id"])
        bodies = " ".join(membership.message_ids.mapped("body"))
        self.assertIn("Please call me.", bodies)
        self.assertIn("Newsletter", bodies)


class TestIdempotency(WebformTestCommon):
    def test_the_same_entry_twice_gives_one_partner_and_one_membership(self):
        payload = self._individual_payload()
        first = self.intake.process(payload)
        second = self.intake.process(payload)
        self.assertEqual(first["partner_id"], second["partner_id"])
        self.assertEqual(first["membership_id"], second["membership_id"])

    def test_a_second_entry_id_for_the_same_person_reuses_the_partner(self):
        self.intake.process(self._individual_payload())
        result = self.intake.process(self._individual_payload(entry_id="9999"))
        partners = self.env["res.partner"].search([
            ("email", "=ilike", "manfred@example.org"),
        ])
        self.assertEqual(len(partners), 1)
        self.assertEqual(result["partner_id"], partners.id)

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
class TestWebformEndpoint(HttpCase):
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
