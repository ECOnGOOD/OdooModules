import logging
import re

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Type codes follow the internal-reference scheme the membership products are
# created with, MEM_<SCOPE>_<TYPE>_<VARIANT> (e.g. MEM_DE_COMP_101-200), and the
# partner type each code implies.
PARTNER_TYPE_BY_TYPE_CODE = {
    "IND": "person",
    "COMP": "company",
    "ORG": "company",
    "MUN": "company",
}

# Tokens the form's "I am a" select (field 1589) may carry, in either language.
TYPE_CODE_TOKENS = {
    "IND": ("individual", "private", "privatperson", "person", "privat"),
    "COMP": ("company", "unternehmen", "firma", "business", "betrieb"),
    "ORG": ("association", "verein", "organisation", "organization", "npo", "ngo"),
    "MUN": ("municipality", "gemeinde", "kommune", "public", "behörde", "behoerde"),
}

# Attribute names that carry the employee-count tier.
TIER_ATTRIBUTE_NAMES = {"Variant", "Mitarbeiterzahl", "MA", "FTE", "EPU"}

OPEN_ENDED_HIGH = 10**9

TRUTHY = {"1", "true", "yes", "y", "ja", "x", "on", "checked"}


class WebformError(Exception):
    """A payload that is well-formed but cannot be mapped onto Odoo records.

    ``code`` is the machine-readable reason returned to WordPress, so a failed
    submission can be diagnosed from the Formidable entry without server access.
    """

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def parse_variant_range(label):
    """Parse a tier attribute label into an inclusive ``(low, high)`` range.

    Accepts the labels the membership products carry: ``'0-1'``, ``'11-22'``,
    ``'2'``, ``'2501+'``. Returns ``None`` for anything unparseable.
    """
    if not label:
        return None
    text = str(label).strip().replace(" ", "")
    if not text:
        return None
    if text.endswith("+"):
        try:
            return (int(text[:-1]), OPEN_ENDED_HIGH)
        except ValueError:
            return None
    if "-" in text:
        low_str, _sep, high_str = text.partition("-")
        try:
            return (int(low_str), int(high_str))
        except ValueError:
            return None
    try:
        number = int(text)
    except ValueError:
        return None
    return (number, number)


def truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in TRUTHY


def to_float(value, default=0.0):
    if value in (None, "", False):
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def to_int(value):
    """Integer from a form value, or ``None``. ``'45 MA'`` gives 45."""
    if value in (None, "", False):
        return None
    if isinstance(value, bool):
        return None
    match = re.search(r"-?\d+", str(value).replace(".", "").replace(",", ""))
    return int(match.group()) if match else None


def escape_like(value):
    """Escape a submitted value used inside an ``ilike`` pattern.

    The ORM wraps an ``ilike`` value in ``%...%`` without escaping the wildcards
    inside it, so an unescaped ``%`` from the form would match *any* record. That
    matters most for the association lookup: the association decides whose books
    the member lands on.
    """
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def normalize_name(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


class MembershipWebformIntake(models.AbstractModel):
    """Maps a Formidable submission onto partner and membership records.

    Kept free of any HTTP concern so it can be tested directly; the controller
    only authenticates, parses the body and delegates here.
    """

    _name = "membership.webform.intake"
    _description = "Membership Webform Intake"

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    @api.model
    def process(self, payload):
        """Create or update the records for one submission.

        Returns ``{"partner_id", "membership_id", "warnings"}``. Raises
        :class:`WebformError` when the payload cannot be mapped; the caller is
        responsible for rolling the transaction back so nothing partial remains.
        """
        warnings = []
        company = self._resolve_company(payload)
        type_code = self._resolve_type_code(payload)
        self = self.with_company(company)

        partner = self._upsert_partner(payload, company, type_code)
        contact = self._upsert_contact_person(payload, partner, company)
        self._link_communication_contact(partner, contact, warnings)

        product = self._resolve_product(payload, company, partner, type_code, warnings)
        membership = self._upsert_membership(payload, partner, company, product, contact)

        self._apply_payment(payload, partner, company, membership, warnings)
        self._log_unmapped(payload, membership, warnings)

        return {
            "partner_id": partner.id,
            "membership_id": membership.id,
            "membership_state": membership.state,
            "company_id": company.id,
            "product_id": product.id,
            "amount": membership.amount,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # company and type
    # ------------------------------------------------------------------

    @api.model
    def _resolve_company(self, payload):
        """The association that owns the membership.

        The regional association wins over the national one when both are given.
        Unlike ``OdooConnector.get_company_id``, an unresolved name is an error
        rather than a fallback to company 1: filing a member under the wrong
        association is worse than refusing the submission.
        """
        Company = self.env["res.company"]
        for key in ("econ_region_assoc", "econ_assoc_select", "econ_main_assoc"):
            name = (payload.get(key) or "").strip()
            if not name:
                continue
            pattern = escape_like(name)
            for domain in (
                [("name", "=", name)],
                [("partner_id.name", "=", name)],
                [("name", "ilike", pattern)],
                [("partner_id.name", "ilike", pattern)],
            ):
                company = Company.search(domain, limit=1)
                if company:
                    return company
        raise WebformError(
            "company_unresolved",
            _("No association matches %s.")
            % (payload.get("econ_region_assoc") or payload.get("econ_assoc_select") or "(empty)"),
        )

    @api.model
    def _resolve_type_code(self, payload):
        """IND / COMP / ORG / MUN, from the form's "I am a" select plus flags."""
        raw = str(payload.get("type") or "").strip().lower()
        code = None
        for candidate, tokens in TYPE_CODE_TOKENS.items():
            if any(token in raw for token in tokens):
                code = candidate
                break
        if code is None:
            # Fall back to whichever block carries a name.
            if payload.get("assoc_name"):
                code = "ORG"
            elif payload.get("company_name"):
                code = "COMP"
            else:
                code = "IND"
        # A charitable company is priced as a nonprofit organisation.
        if code == "COMP" and truthy(payload.get("assoc_is_charitable")):
            code = "ORG"
        if truthy(payload.get("is_municipality")):
            code = "MUN"
        return code

    # ------------------------------------------------------------------
    # partner
    # ------------------------------------------------------------------

    @api.model
    def _partner_values(self, payload, company, type_code):
        """Field values for the member, per form block."""
        today = fields.Date.context_today(self)
        vals = {"company_id": False}

        if type_code == "IND":
            name = " ".join(
                part
                for part in (payload.get("first_name"), payload.get("last_name"))
                if part
            ).strip()
            vals.update(
                {
                    "name": name or payload.get("email") or _("Unnamed Contact"),
                    "company_type": "person",
                    "street": payload.get("street"),
                    "street2": payload.get("address_extra"),
                    "zip": payload.get("zip"),
                    "city": payload.get("city"),
                    "country_id": self._country_id(payload.get("country")),
                    "email": payload.get("email"),
                    "phone": payload.get("phone"),
                    "vat": payload.get("ind_tax"),
                    "function": payload.get("occupation"),
                    "title": self._title_id(payload.get("title") or payload.get("salutation")),
                }
            )
        else:
            prefix = "assoc" if type_code in ("ORG", "MUN") else "company"
            vals.update(
                {
                    "name": payload.get(f"{prefix}_name") or _("Unnamed Organization"),
                    "company_type": "company",
                    "street": payload.get(f"{prefix}_street"),
                    "street2": payload.get(f"{prefix}_street_extra"),
                    "zip": payload.get(f"{prefix}_zip"),
                    "city": payload.get(f"{prefix}_city"),
                    "country_id": self._country_id(payload.get(f"{prefix}_country")),
                    "email": payload.get(f"{prefix}_email"),
                    "phone": payload.get(f"{prefix}_phone"),
                    "website": payload.get(f"{prefix}_website"),
                    # The VAT ID is the stronger identifier; the tax number is the
                    # fallback for organisations that have no VAT ID.
                    "vat": payload.get("company_vat_id")
                    or payload.get(f"{prefix}_tax_number"),
                    "company_registry": payload.get("assoc_reg_number"),
                }
            )

        # Fields contributed by econgood_extra_fields.
        extra = {
            "employee_count": to_int(
                payload.get("company_employees") or payload.get("assoc_employees_fte")
            ),
            "legacy_id_formidable": self._entry_reference(payload),
        }
        if truthy(payload.get("assoc_is_charitable")):
            extra["nonprofit_status"] = "nonprofit"
        # 2458 is the data protection agreement that is actually signed; 1604 is
        # the privacy notice everyone acknowledges and has no field of its own.
        if truthy(payload.get("data_protection_agreement")):
            extra["privacy_agreement_signed_date"] = today
        if truthy(payload.get("code_of_conduct")):
            extra["code_of_conduct_signed_date"] = today
        vals.update(self._filter_fields("res.partner", extra))

        return {key: value for key, value in vals.items() if value not in (None, "")}

    @api.model
    def _upsert_partner(self, payload, company, type_code):
        Partner = self.env["res.partner"]
        vals = self._partner_values(payload, company, type_code)
        partner = self._find_existing_partner(payload, vals)
        if partner:
            partner.write(vals)
            return partner
        return Partner.create(vals)

    @api.model
    def _find_existing_partner(self, payload, vals):
        """Match order: Formidable entry id, then email **and** name.

        An email-only match is deliberately refused: shared family and office
        addresses make email alone unsafe as an identity.
        """
        Partner = self.env["res.partner"]
        reference = self._entry_reference(payload)
        if reference and "legacy_id_formidable" in Partner._fields:
            existing = Partner.search(
                [("legacy_id_formidable", "=", reference)], limit=1
            )
            if existing:
                return existing

        email = vals.get("email")
        name = vals.get("name")
        if not email or not name:
            return Partner
        candidates = Partner.search([("email", "=ilike", email)])
        wanted = normalize_name(name)
        return next(
            (c for c in candidates if normalize_name(c.name) == wanted),
            Partner,
        )

    @api.model
    def _upsert_contact_person(self, payload, partner, company):
        """The named contact person of an organisation, as a child partner."""
        Partner = self.env["res.partner"]
        if not partner.is_company:
            return Partner
        name = " ".join(
            part
            for part in (payload.get("contact_first_name"), payload.get("contact_last_name"))
            if part
        ).strip()
        if not name:
            return Partner

        vals = {
            "parent_id": partner.id,
            "type": "contact",
            "name": name,
            "email": payload.get("contact_email"),
            "phone": payload.get("contact_phone"),
            "function": payload.get("contact_role"),
            "title": self._title_id(
                payload.get("contact_title") or payload.get("contact_salutation")
            ),
            "company_id": False,
        }
        vals = {key: value for key, value in vals.items() if value not in (None, "")}

        domain = [("parent_id", "=", partner.id)]
        if vals.get("email"):
            domain.append(("email", "=ilike", vals["email"]))
        else:
            domain.append(("name", "=", name))
        existing = Partner.search(domain, limit=1)
        if existing:
            existing.write(vals)
            return existing
        return Partner.create(vals)

    @api.model
    def _link_communication_contact(self, partner, contact, warnings):
        """Point the module's recipient lookup at the named contact person.

        ``membership._get_communication_partners`` resolves recipients through
        ``address_get(["contact"])``, which returns ``partner_contact_id`` when the
        optional OCA ``partner_contact_address_default`` is installed and otherwise
        the organisation itself. Setting it here is what keeps member emails off the
        general head-office address. The field is guarded because that module must
        never become a dependency.
        """
        if not contact or not partner.is_company:
            return
        if "partner_contact_id" not in self.env["res.partner"]._fields:
            warnings.append("contact_person_not_linked")
            return
        if not partner.partner_contact_id:
            partner.partner_contact_id = contact.id

    # ------------------------------------------------------------------
    # product and tier
    # ------------------------------------------------------------------

    @api.model
    def _resolve_product(self, payload, company, partner, type_code, warnings):
        """Infer the membership product from association, type and employee count.

        The CRM's own type label is only a last resort: matching a foreign label
        string against an Odoo product name is the weakest link in the write-only
        design, so it never runs before the structural inference.
        """
        Product = self.env["product.product"]

        reference = (payload.get("membership_product_ref") or "").strip()
        if reference:
            variant = Product.search([("default_code", "=", reference)], limit=1)
            if variant:
                return variant
            warnings.append("product_ref_unknown")

        candidates = Product.search(
            Product._membership_product_domain(company, partner)
        )
        if not candidates:
            raise WebformError(
                "product_unresolved",
                _("No membership product of %(company)s accepts a %(kind)s member.")
                % {
                    "company": company.display_name,
                    "kind": PARTNER_TYPE_BY_TYPE_CODE.get(type_code, type_code),
                },
            )

        typed = candidates.filtered(
            lambda p: f"_{type_code}_" in (p.default_code or "")
        )
        pool = typed or candidates

        label = (payload.get("membership_type") or "").strip()
        if label:
            by_label = pool.filtered(
                lambda p: label.casefold() in (p.product_tmpl_id.name or "").casefold()
            )
            if by_label:
                pool = by_label

        templates = pool.product_tmpl_id
        if len(templates) > 1:
            # Several types remain plausible; the one with the most tiers is the
            # size-scaled membership, which is the one an employee count applies to.
            best = max(templates, key=lambda t: len(t.product_variant_ids))
            pool = pool.filtered(lambda p: p.product_tmpl_id == best)

        employee_count = to_int(
            payload.get("company_employees") or payload.get("assoc_employees_fte")
        )
        return self._pick_tier(pool, employee_count, warnings)

    @api.model
    def _tier_range(self, variant):
        """The employee range a variant covers, or ``None`` if it carries no tier."""
        for ptav in variant.product_template_attribute_value_ids:
            if ptav.attribute_id.name and ptav.attribute_id.name not in TIER_ATTRIBUTE_NAMES:
                continue
            parsed = parse_variant_range(ptav.name)
            if parsed:
                return parsed
        return None

    @api.model
    def _pick_tier(self, variants, employee_count, warnings):
        """Pick the variant covering ``employee_count``, clamping when none does.

        Tier ranges are data, and data can have gaps: a count that no range
        covers clamps to the nearest tier and reports ``tier_clamped`` rather
        than losing the signup.
        """
        ranged = []
        for variant in variants:
            parsed = self._tier_range(variant)
            if parsed:
                ranged.append((parsed, variant))

        if not ranged:
            # A membership without size tiers, e.g. the individual products.
            return variants[0]

        if employee_count is None:
            warnings.append("employee_count_missing")
            return min(ranged, key=lambda item: item[0][0])[1]

        covering = [
            (high - low, variant)
            for (low, high), variant in ranged
            if low <= employee_count <= high
        ]
        if covering:
            # The most specific range wins where several overlap.
            return min(covering, key=lambda item: item[0])[1]

        warnings.append("tier_clamped")
        below = [(low, variant) for (low, _high), variant in ranged if low <= employee_count]
        if below:
            return max(below, key=lambda item: item[0])[1]
        return min(ranged, key=lambda item: item[0][0])[1]

    # ------------------------------------------------------------------
    # membership
    # ------------------------------------------------------------------

    @api.model
    def _upsert_membership(self, payload, partner, company, product, contact):
        """Create the membership in ``waiting``, or update the open one.

        ``state`` is never written directly — ``membership.membership.write``
        refuses that outside its own transitions — so the record is created in its
        ``draft`` default and moved on with ``action_submit()``. No period is
        created: periods belong to activation, which stays a staff action.
        """
        Membership = self.env["membership.membership"]
        vals = {
            "partner_id": partner.id,
            "company_id": company.id,
            "product_id": product.id,
        }

        amount = self._requested_amount(payload, product, company)
        if amount is not None:
            vals["amount"] = amount

        existing = Membership.search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", company.id),
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                ("state", "in", ("draft", "waiting", "active", "cancelled")),
            ],
            limit=1,
        )
        if existing:
            existing.write(vals)
            membership = existing
        else:
            membership = Membership.create(vals)

        if membership.state == "draft":
            membership.action_submit()
        return membership

    @api.model
    def _requested_amount(self, payload, product, company):
        """The optional higher fee (form field 1547), when it beats the tier price."""
        offered = to_float(payload.get("fee_optional_extra"))
        if offered <= 0:
            return None
        standard = product._get_membership_price(company)
        return offered if offered > standard else None

    # ------------------------------------------------------------------
    # payment
    # ------------------------------------------------------------------

    @api.model
    def _apply_payment(self, payload, partner, company, membership, warnings):
        bank = self._upsert_partner_bank(payload, partner, company)
        mode = self._resolve_payment_mode(payload, company)
        if mode and "customer_payment_mode_id" in self.env["res.partner"]._fields:
            partner.customer_payment_mode_id = mode.id
        elif payload.get("payment_method"):
            # The member stated a payment method but it could not be recorded —
            # a company only has the payment modes that were set up for it.
            warnings.append("payment_mode_unavailable")
        if self._is_direct_debit(payload):
            self._ensure_mandate(partner, company, bank, warnings)

        frequency = str(payload.get("payment_frequency") or "").strip().lower()
        if frequency and not any(
            token in frequency for token in ("year", "jähr", "jaehr", "annual", "annu")
        ):
            # Billing is one period per calendar year; anything else needs a human.
            warnings.append("payment_frequency_not_yearly")

    @api.model
    def _upsert_partner_bank(self, payload, partner, company):
        """Attach the IBAN, resolving or creating the bank from the BIC."""
        iban = str(payload.get("iban") or "").replace(" ", "").upper()
        if not iban:
            return self.env["res.partner.bank"]
        Bank = self.env["res.partner.bank"]
        # base_iban rewrites acc_number into the spaced "pretty" form on write, so
        # the compacted value only matches sanitized_acc_number. Searching the raw
        # column instead would miss on every replay and duplicate the account.
        number_field = (
            "sanitized_acc_number"
            if "sanitized_acc_number" in Bank._fields
            else "acc_number"
        )
        existing = Bank.search(
            [(number_field, "=", iban), ("partner_id", "=", partner.id)], limit=1
        )
        if existing:
            return existing

        vals = {"acc_number": iban, "partner_id": partner.id, "company_id": company.id}
        bic = str(payload.get("bic") or "").strip()
        if bic:
            bank = self.env["res.bank"].search([("bic", "=", bic)], limit=1)
            if not bank:
                bank = self.env["res.bank"].create({"name": bic, "bic": bic})
            vals["bank_id"] = bank.id
        return Bank.create(vals)

    @api.model
    def _is_direct_debit(self, payload):
        method = str(payload.get("payment_method") or "").casefold()
        return any(
            token in method for token in ("sepa", "lastschrift", "debit", "einzug")
        )

    @api.model
    def _resolve_payment_mode(self, payload, company):
        """``Überweisung`` / ``Lastschrift``, by name, on this company.

        Optional: ``account_payment_mode`` is not a dependency of this module.
        """
        if "account.payment.mode" not in self.env:
            return None
        wanted = "Lastschrift" if self._is_direct_debit(payload) else "Überweisung"
        return self.env["account.payment.mode"].search(
            [("name", "=", wanted), ("company_id", "=", company.id)], limit=1
        ) or None

    @api.model
    def _ensure_mandate(self, partner, company, bank, warnings):
        """A SEPA mandate for direct debit, when ``account_banking_mandate`` is in."""
        if "account.banking.mandate" not in self.env:
            warnings.append("mandate_module_missing")
            return
        if not bank:
            warnings.append("mandate_without_iban")
            return
        Mandate = self.env["account.banking.mandate"]
        if Mandate.search([("partner_bank_id", "=", bank.id)], limit=1):
            return
        Mandate.create(
            {
                "partner_id": partner.id,
                "partner_bank_id": bank.id,
                "company_id": company.id,
                "signature_date": fields.Date.context_today(self),
            }
        )

    # ------------------------------------------------------------------
    # chatter
    # ------------------------------------------------------------------

    @api.model
    def _log_unmapped(self, payload, membership, warnings):
        """Everything the data model has no home for, on the membership chatter.

        Nothing submitted is silently dropped, even where no field exists yet.
        """
        lines = []
        donation = to_float(payload.get("donation_amount"))
        if donation > 0:
            lines.append(
                _("Donation offered on signup: %(amount)s %(currency)s")
                % {
                    "amount": donation,
                    "currency": payload.get("currency") or membership.currency_id.name,
                }
            )
        for key, label in (
            ("payment_method", _("Payment method")),
            ("payment_frequency", _("Payment frequency")),
            ("service_actively_work", _("Wants to actively work in ECOnGOOD")),
            ("service_newsletter", _("Newsletter subscription")),
            ("service_website_listing", _("Listing on the website")),
            ("econ_local_chapter", _("Local chapter")),
            ("birth_city", _("City of birth")),
            ("birthday", _("Birthday")),
            ("company_sector", _("Sector")),
            ("assoc_sector", _("Sector")),
            ("note_to_econgood", _("Note to ECOnGOOD")),
        ):
            value = payload.get(key)
            if value not in (None, "", False):
                lines.append(f"{label}: {value}")
        if truthy(payload.get("data_protection_notice")):
            lines.append(_("Privacy notice acknowledged on the signup form."))
        if warnings:
            lines.append(_("Intake warnings: %s") % ", ".join(warnings))

        reference = self._entry_reference(payload)
        header = _("Website signup")
        if reference:
            header = _("Website signup (Formidable entry %s)") % reference
        if not lines:
            lines = [_("No extra data.")]

        # message_post escapes a plain string, so the body has to be Markup or the
        # chatter shows literal tags. Markup's %-formatting escapes its arguments,
        # which is what keeps submitted values from injecting HTML.
        items = Markup("").join(Markup("<li>%s</li>") % line for line in lines)
        membership.message_post(body=Markup("<p>%s</p><ul>%s</ul>") % (header, items))

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    @api.model
    def _entry_reference(self, payload):
        value = payload.get("entry_id") or payload.get("formidable_number")
        return str(value).strip() if value not in (None, "", False) else None

    @api.model
    def _filter_fields(self, model, vals):
        """Drop values whose field is not installed on this database.

        The same guard the repo-side tooling applies, so optional OCA modules stay
        optional.
        """
        fields_ = self.env[model]._fields
        return {key: value for key, value in vals.items() if key in fields_}

    @api.model
    def _country_id(self, name):
        if not name:
            return False
        country = self.env["res.country"].search(
            [("name", "ilike", escape_like(name))], limit=1
        )
        if not country and len(str(name).strip()) == 2:
            country = self.env["res.country"].search(
                [("code", "=", str(name).strip().upper())], limit=1
            )
        return country.id or False

    @api.model
    def _title_id(self, value):
        if not value:
            return False
        mapping = {"herr": "Mister", "frau": "Madam", "dr.": "Doctor", "dr": "Doctor"}
        name = mapping.get(str(value).strip().lower(), str(value).strip())
        Title = self.env["res.partner.title"]
        title = Title.search([("name", "=", name)], limit=1)
        if not title:
            title = Title.search([("shortcut", "=", str(value).strip())], limit=1)
        return title.id or False
