import logging
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

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

# Organisation kind (econgood_extra_fields) each organisation type code implies.
ORGANIZATION_KIND_XMLID_BY_TYPE_CODE = {
    "COMP": "econgood_extra_fields.res_partner_organization_kind_company",
    "ORG": "econgood_extra_fields.res_partner_organization_kind_organization",
    "MUN": "econgood_extra_fields.res_partner_organization_kind_municipality_public_body",
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

# The form's select fields carry an option id, not a name; see looks_like_opaque_id.
OPAQUE_ID_RE = re.compile(r"^[0-9A-Fa-f]{20,}$")

# Local chapters are partners, canonically named "LC <Name>"; the form sends the
# bare name.
CHAPTER_PREFIX = "LC "
CHAPTER_OU_TYPE_CODE = "local_chapter"

# Relation type naming varies by how the database was seeded.
CHAPTER_RELATION_NAMES = ("Local Chapter", "Regional-Gruppe", "Regionalgruppe")

# Warnings that mean "the membership was not inferred cleanly" — worth saying so
# in the chatter, not just in the response.
INFERENCE_WARNINGS = frozenset({
    "product_ref_unknown",
    "employee_count_missing",
    "tier_clamped",
    "fee_mismatch",
})


class WebformError(Exception):
    """A payload that is well-formed but cannot be mapped onto Odoo records.

    ``code`` is the machine-readable reason returned to WordPress, so a failed
    submission can be diagnosed from the Formidable entry without server access.
    """

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class WebformDuplicate(WebformError):
    """A second signup by someone who already has an open membership.

    Carries the ids as plain integers, not recordsets: the caller rolls the
    savepoint back before acting on it, which invalidates the ORM cache, so
    anything that survives the boundary has to be re-browsed.
    """

    def __init__(self, message, membership_id=None, partner_id=None):
        self.membership_id = membership_id
        self.partner_id = partner_id
        super().__init__("duplicate_signup", message)


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


def to_amount(value, default=None):
    """Parse a submitted money value, or return ``default``.

    Handles the shapes the form produces: ``600``, ``600,00``, ``"600,00 €"``,
    ``1.200,00``, ``1,200.00``.

    One case is genuinely ambiguous: a single separator followed by exactly
    three digits. ``1.200`` is 1200 to a German reader and 1.2 to an English
    one. It is read as a thousands separator, because a membership fee of 1.20
    written that way is not a thing and 1200 is. ``to_float`` cannot be reused
    here — it throws the whole value away on ``"600,00 €"`` — and neither can
    ``to_int``, which turns ``1.200,50`` into 120050.
    """
    if value in (None, "", False):
        return default
    text = re.sub(r"[^\d.,-]", "", str(value))
    if not text:
        return default

    if "." in text and "," in text:
        # Whichever separator comes last is the decimal one.
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        if text.count(sep) > 1:
            # 1.200.000 can only be thousands separators.
            text = text.replace(sep, "")
        else:
            head, _found, tail = text.partition(sep)
            text = head + tail if len(tail) == 3 else f"{head}.{tail}"

    try:
        return float(text)
    except ValueError:
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


def looks_like_opaque_id(value):
    """True for the option id the form's select fields carry instead of a name.

    Formidable sends both an opaque id and the official name: the select fields
    (1596 association, 1598 chapter) hold something like
    ``63219893D7E43C1499727C6DAFF07``, the text fields beside them hold
    ``Gemeinwohl-Ökonomie Deutschland e.V.``. Searching for a hash can only fail,
    so hash-shaped values are skipped rather than looked up.
    """
    return bool(OPAQUE_ID_RE.match(str(value or "").strip()))


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

        Returns ``{"partner_id", "membership_id", "warnings", ...}``. Raises
        :class:`WebformError` when the payload cannot be mapped; the caller is
        responsible for rolling the transaction back so nothing partial remains.

        One deliberate exception to that all-or-nothing rule: the regional
        membership has its own savepoint, so a problem creating that free record
        degrades to a warning rather than rejecting a paid national signup. See
        ``_upsert_regional_membership``.
        """
        warnings = []
        # Chatter lines that need more detail than a warning code carries.
        notes = []
        national, regional = self._resolve_associations(payload, warnings)
        chapter = self._resolve_local_chapter(payload, warnings)
        type_code = self._resolve_type_code(payload)
        self = self.with_company(national)

        self._check_duplicate_signup(payload, national, type_code)
        partner = self._upsert_partner(payload, national, type_code)
        self._scope_partner(partner, [national, regional])
        contact = self._upsert_contact_person(payload, partner, national)
        self._link_communication_contact(partner, contact, warnings)
        # The membership computes its invoice contact from this child.
        self._upsert_invoice_contact(payload, partner)

        product = self._resolve_product(
            payload, national, partner, type_code, warnings, notes,
        )
        membership = self._upsert_membership(
            payload, partner, national, product, contact,
        )

        regional_membership = self.env["membership.membership"]
        if regional:
            regional_membership = self._upsert_regional_membership(
                partner, regional, type_code, warnings, notes,
            )
        chapter_linked = self._ensure_chapter_relation(partner, chapter, warnings)

        self._apply_payment(payload, partner, national, membership, warnings)
        self._log_membership_notes(payload, membership, warnings, notes)
        self._log_partner_notes(payload, partner, chapter_linked)

        return {
            "partner_id": partner.id,
            "membership_id": membership.id,
            "membership_state": membership.state,
            "company_id": national.id,
            "product_id": product.id,
            "amount": membership.amount,
            "regional_membership_id": regional_membership.id or None,
            "regional_company_id": regional.id or None,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # company and type
    # ------------------------------------------------------------------

    @api.model
    def _named_values(self, payload, *keys):
        """The values of ``keys`` that are usable as a name, in order."""
        for key in keys:
            value = (payload.get(key) or "").strip()
            if value and not looks_like_opaque_id(value):
                yield value

    @api.model
    def _find_company_by_name(self, name):
        Company = self.env["res.company"]
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
        return Company

    @api.model
    def _resolve_associations(self, payload, warnings):
        """``(national, regional)`` — the national association owns the membership.

        Resolution runs on the official-name fields (1533 main, 1535 regional),
        never on the select fields beside them, which carry an option id.
        ``econ_assoc_select`` is still accepted as a fallback *when it holds a
        name*, because entries stored before the form changed carry nothing else
        and must stay replayable.

        Unlike ``OdooConnector.get_company_id``, an unresolved national name is an
        error rather than a fallback to company 1: filing a member under the wrong
        association is worse than refusing the submission. An unresolved *regional*
        name only warns — a typo there must not lose a paid signup.
        """
        national = self.env["res.company"]
        for name in self._named_values(payload, "econ_main_assoc", "econ_assoc_select"):
            national = self._find_company_by_name(name)
            if national:
                break
        if not national:
            raise WebformError(
                "company_unresolved",
                _("No association matches %s.")
                % (
                    payload.get("econ_main_assoc")
                    or payload.get("econ_assoc_select")
                    or "(empty)"
                ),
            )

        regional = self.env["res.company"]
        wanted = next(self._named_values(payload, "econ_region_assoc"), None)
        if not wanted:
            return national, regional

        regional = self._find_company_by_name(wanted)
        if not regional:
            warnings.append("regional_association_unresolved")
        elif regional == national:
            # The form repeats the same name at both levels for an association
            # that has no regional structure.
            regional = self.env["res.company"]
        elif regional.parent_id != national:
            # A mis-picked select would otherwise file the member under a
            # regional association of a different national tree, which no
            # constraint in the module would catch.
            warnings.append("regional_association_mismatch")
            regional = self.env["res.company"]
        return national, regional

    @api.model
    def _chapter_name_candidates(self, raw):
        """``Hamburg`` and ``LC Hamburg`` both mean the partner ``LC Hamburg``."""
        name = str(raw or "").strip()
        if not name:
            return []
        if name.upper().startswith(CHAPTER_PREFIX):
            return [name, name[len(CHAPTER_PREFIX):].strip()]
        return [f"{CHAPTER_PREFIX}{name}", name]

    @api.model
    def _resolve_local_chapter(self, payload, warnings):
        """The local chapter partner, or an empty recordset.

        Chapters are ``res.partner`` organisational units, not companies, and are
        never created from a signup: an unknown chapter is a warning plus a note,
        so staff can correct it, rather than a new half-built OU.
        """
        Partner = self.env["res.partner"]
        raw = next(self._named_values(payload, "econ_local_chapter"), None)
        if not raw:
            return Partner

        # Most constrained domain first: without the OU filter an ``ilike`` on a
        # short chapter name would match arbitrary member organisations.
        domains = []
        fields_ = Partner._fields
        if "is_econgood_ou" in fields_ and "ou_type_id" in fields_:
            domains.append([
                ("is_company", "=", True),
                ("is_econgood_ou", "=", True),
                ("ou_type_id.code", "=", CHAPTER_OU_TYPE_CODE),
            ])
        if "is_econgood_ou" in fields_:
            domains.append([("is_company", "=", True), ("is_econgood_ou", "=", True)])
        domains.append([("is_company", "=", True)])

        for base in domains:
            for candidate in self._chapter_name_candidates(raw):
                for operator, value in (
                    ("=", candidate),
                    ("ilike", escape_like(candidate)),
                ):
                    chapter = Partner.search(
                        base + [("name", operator, value)], limit=1
                    )
                    if chapter:
                        return chapter
        warnings.append("chapter_unresolved")
        return Partner

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
        """Field values for the member, per form block.

        Deliberately carries no company: scoping is applied afterwards by
        ``_scope_partner``, which has to write ``company_ids`` and must not be
        fighting a ``company_id`` in the same vals dict.
        """
        today = fields.Date.context_today(self)
        vals = {}

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
            # The pricing type code and the form block are not the same thing: a
            # charitable *company* is priced as an organisation (ORG) while its
            # data still arrives in the company block. Follow whichever block
            # actually carries a name, or the whole submission is read from the
            # empty one and lands as "Unnamed Organization" with no address.
            if payload.get("assoc_name"):
                prefix = "assoc"
            elif payload.get("company_name"):
                prefix = "company"
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
            # The selection is unknown / confirmed / not_nonprofit. Writing
            # anything else raises a plain ValueError, which the controller does
            # not treat as a rejection, so it would surface as a 500.
            extra["nonprofit_status"] = "confirmed"
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
        partner, _match_kind = self._find_existing_partner(payload, vals)
        if partner:
            partner.write(vals)
        else:
            partner = Partner.create(vals)
        # Written once the partner is a company, which the kind requires. Only
        # fills a blank: staff may have chosen a different kind (15.35).
        xmlid = ORGANIZATION_KIND_XMLID_BY_TYPE_CODE.get(type_code)
        if xmlid and partner.is_company and not partner.organization_kind_id:
            partner.organization_kind_id = self.env.ref(xmlid)
        return partner

    @api.model
    def _find_existing_partner(self, payload, vals):
        """``(partner, match_kind)``; match order: entry id, then email **and** name.

        ``match_kind`` is ``"entry"`` when the Formidable entry id matched — that
        is a replay of the same submission — and ``"email_name"`` when a different
        entry resolved to a person we already know, which is what the duplicate
        check acts on. Without the distinction a replay and a genuine second
        signup are indistinguishable.

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
                return existing, "entry"

        email = vals.get("email")
        name = vals.get("name")
        if not email or not name:
            return Partner, None
        candidates = Partner.search([("email", "=ilike", email)])
        wanted = normalize_name(name)
        match = next(
            (c for c in candidates if normalize_name(c.name) == wanted),
            Partner,
        )
        return match, ("email_name" if match else None)

    @api.model
    def _scope_partner(self, partner, companies):
        """Make the member visible to their own associations, and no others.

        A partner left without companies is visible to every association in the
        federation, which is what a signup used to produce. The write is a union
        rather than a replacement: a member who joins a second association keeps
        the first, and staff-made affiliations are never dropped.

        Ported from ``canonical_contact_importer.ensure_partner_company_affiliations``
        and its ``write_many2many_union`` helper.
        """
        if not partner or not companies:
            return
        Partner = self.env["res.partner"]
        wanted = {company.id for company in companies if company}

        if "company_ids" not in Partner._fields:
            # Without base_multi_company there is only the single-company field.
            # Setting it would *restrict* the partner rather than scope it, and
            # would override a value staff may have chosen, so only fill a blank.
            if not partner.company_id and len(wanted) == 1:
                partner.company_id = next(iter(wanted))
            return

        # company_ids is declared with depends_context=("uid",), so the sudo and
        # non-sudo caches diverge; read and write it at one su level.
        scoped = partner.sudo()
        current = set(scoped.company_ids.ids)
        if wanted <= current:
            return
        scoped.write({"company_ids": [(6, 0, sorted(current | wanted))]})

    @api.model
    def _check_duplicate_signup(self, payload, company, type_code):
        """Refuse a second signup by someone who already has an open membership.

        Runs before anything is written. Only an ``email_name`` match counts: a
        matching entry id is a replay, which must stay idempotent.

        The open states here deliberately exclude ``cancelled``, which in this
        module means "scheduled to end" rather than "gone" — a leaving member may
        rejoin through the form. ``_upsert_membership`` includes it for the
        opposite reason, so the two lists are meant to differ.
        """
        if truthy(payload.get("force_duplicate")):
            return
        vals = self._partner_values(payload, company, type_code)
        partner, match_kind = self._find_existing_partner(payload, vals)
        if match_kind != "email_name":
            return

        existing = self.env["membership.membership"].search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", company.id),
                ("state", "in", ("draft", "waiting", "active")),
            ],
            limit=1,
        )
        if not existing:
            return
        raise WebformDuplicate(
            _(
                "%(name)s already has an open membership with %(company)s"
                " (%(number)s). Nothing was changed."
            )
            % {
                "name": partner.display_name,
                "company": company.display_name,
                "number": existing.membership_number or _("no number yet"),
            },
            membership_id=existing.id,
            partner_id=partner.id,
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
            # No company here either: with partner_multi_company installed
            # company_ids is a commercial field, so a child inherits the parent's
            # scope on its own, and writing company_id would fight that.
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
    def _upsert_invoice_contact(self, payload, partner):
        """The separate invoice address, as an ``invoice``-type child partner.

        This is what ``membership.invoice_partner_id`` resolves to: it is
        computed from ``partner.address_get(["invoice"])``, which finds an
        invoice-type child and otherwise falls back to the member. No explicit
        write is needed, now or when the child arrives with a later submission.

        ``invoice_email`` alone is enough to trigger it; the remaining
        ``invoice_*`` keys are optional overrides, so Formidable fields can be
        added one at a time without changing this code.
        """
        Partner = self.env["res.partner"]
        email = (payload.get("invoice_email") or "").strip()
        if not email:
            return Partner

        first = (payload.get("invoice_first_name") or "").strip()
        last = (payload.get("invoice_last_name") or "").strip()
        if first and last:
            name = f"{first} {last}"
        else:
            name = _("Invoice Address - %s") % partner.name

        vals = {
            "parent_id": partner.id,
            "type": "invoice",
            "name": name,
            "email": email,
            # The member's address unless the form overrides it field by field.
            "street": payload.get("invoice_street") or partner.street,
            "zip": payload.get("invoice_zip") or partner.zip,
            "city": payload.get("invoice_city") or partner.city,
            "country_id": (
                self._country_id(payload.get("invoice_country"))
                or partner.country_id.id
            ),
            # Never is_company: address_get skips children flagged as companies,
            # which would make the invoice contact invisible to the resolver.
        }
        vals = {key: value for key, value in vals.items() if value not in (None, "")}

        # Matched on the slot, not the email: there is at most one invoice
        # address, and matching on email would make a corrected one create a
        # second child that address_get then picks between arbitrarily.
        existing = Partner.search(
            [("parent_id", "=", partner.id), ("type", "=", "invoice")], limit=1
        )
        if existing:
            existing.write(vals)
            invoice_contact = existing
        else:
            invoice_contact = Partner.create(vals)
        # OCA's manual "Invoice address" stays empty: the invoice-type child is
        # the invoice address (15.26).
        return invoice_contact

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
    def _resolve_product(self, payload, company, partner, type_code, warnings, notes):
        """Infer the membership product from association, type and employee count.

        The CRM's own type label is only a last resort: matching a foreign label
        string against an Odoo product name is the weakest link in the write-only
        design, so it never runs before the structural inference. The submitted
        fee is used only to break a remaining tie, and afterwards to cross-check.
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
            pool = self._narrow_by_fee(pool, templates, payload, company)

        employee_count = to_int(
            payload.get("company_employees") or payload.get("assoc_employees_fte")
        )
        variant = self._pick_tier(pool, employee_count, warnings)
        self._cross_check_fee(payload, variant, company, warnings, notes)
        return variant

    @api.model
    def _narrow_by_fee(self, pool, templates, payload, company):
        """Break a tie between membership types using the fee the member saw.

        Strictly a tie-break: the structural inference — type code, organisation
        kind, employee count — has already had its say. The price comes from the
        form, which reads it from another system, so it is the least trustworthy
        input available and must never get to overrule the structure.
        """
        fee = to_amount(payload.get("fee_normal"))
        if fee:
            matching = pool.filtered(
                lambda p: not company.currency_id.compare_amounts(
                    p._get_membership_price(company), fee
                )
            )
            if matching:
                return matching
        # No usable fee: the type with the most tiers is the size-scaled
        # membership, which is the one an employee count applies to.
        best = max(templates, key=lambda t: len(t.product_variant_ids))
        return pool.filtered(lambda p: p.product_tmpl_id == best)

    @api.model
    def _cross_check_fee(self, payload, product, company, warnings, notes):
        """Flag a disagreement between the form's price and the product's.

        Compared against the *product* price, not the membership amount: the
        optional higher fee legitimately raises the amount, so comparing against
        that would flag every generous member.
        """
        fee = to_amount(payload.get("fee_normal"))
        if not fee or not product:
            return
        price = product._get_membership_price(company)
        if not company.currency_id.compare_amounts(price, fee):
            return
        warnings.append("fee_mismatch")
        notes.append(
            _(
                "Fee mismatch: the form showed %(submitted)s, %(product)s costs"
                " %(price)s. The product price was used."
            )
            % {
                "submitted": fee,
                "product": product.display_name,
                "price": price,
            }
        )

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

        ``cancelled`` is in the update search on purpose: in this module it means
        "scheduled to end", so a correction submitted before that date should
        update the record rather than collide with it. The duplicate check
        deliberately uses a narrower set — see ``_check_duplicate_signup``.
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
    def _resolve_regional_product(self, regional, partner, type_code, warnings, notes):
        """The regional association's own membership product, normally free.

        Note the ``_REG`` suffix in an internal reference does **not** mean
        "regional" — the national individual product is ``MEM_DE_IND_REG`` too.
        What identifies a regional product is that it belongs to the regional
        company; being free is the convention, not the key.
        """
        Product = self.env["product.product"]
        candidates = Product.search(
            Product._membership_product_domain(regional, partner)
        )
        # The domain also admits company-less products, which would let a
        # federation-wide product stand in for the regional one.
        candidates = candidates.filtered(lambda p: p.company_id == regional)
        if not candidates:
            warnings.append("regional_product_missing")
            notes.append(
                _("No membership product of %s could be found, so no regional"
                  " membership was created.") % regional.display_name
            )
            return Product

        typed = candidates.filtered(
            lambda p: f"_{type_code}_" in (p.default_code or "")
        )
        pool = typed or candidates
        free = pool.filtered(lambda p: not p._get_membership_price(regional))
        if free:
            return free[0]
        # Creating a second *priced* membership is how a member gets billed
        # twice, so say so loudly rather than silently.
        warnings.append("regional_product_not_free")
        notes.append(
            _("The regional membership with %(company)s uses %(product)s, which"
              " costs %(price)s — regional memberships are normally free.")
            % {
                "company": regional.display_name,
                "product": pool[0].display_name,
                "price": pool[0]._get_membership_price(regional),
            }
        )
        return pool[0]

    @api.model
    def _upsert_regional_membership(
        self, partner, regional, type_code, warnings, notes
    ):
        """The regional association's membership, alongside the national one.

        Given its own savepoint on purpose. The regional membership is normally a
        free bookkeeping record, and losing a paid national signup because that
        record could not be created would be the worse failure — so a problem
        here degrades to a warning instead of rejecting the submission.

        Only ``ValidationError`` and ``UserError`` are caught: those are the
        module telling us the data is wrong. Anything else is a bug and must
        still surface.
        """
        Membership = self.env["membership.membership"]
        product = self._resolve_regional_product(
            regional, partner, type_code, warnings, notes
        )
        if not product:
            return Membership
        try:
            with self.env.cr.savepoint():
                return self.with_company(regional)._upsert_membership(
                    {}, partner, regional, product, None,
                )
        except (ValidationError, UserError) as error:
            warnings.append("regional_membership_failed")
            notes.append(
                _("The regional membership with %(company)s could not be created:"
                  " %(error)s")
                % {"company": regional.display_name, "error": error}
            )
            _logger.warning(
                "Regional membership for partner %s in %s failed: %s",
                partner.id,
                regional.display_name,
                error,
            )
            return Membership

    @api.model
    def _find_relation_type(self, names):
        """Resolve a relation type by name. Never creates one.

        Relation types are the federation's own vocabulary, seeded by the
        bootstrap; inventing one from a web submission would quietly add a
        second, near-duplicate type that nothing else uses.
        """
        RelationType = self.env["res.partner.relation.type"]
        for operator in ("=", "ilike"):
            for field in ("name", "name_inverse"):
                for name in names:
                    value = name if operator == "=" else escape_like(name)
                    found = RelationType.search([(field, operator, value)], limit=1)
                    if found:
                        return found
        return RelationType

    @api.model
    def _ensure_chapter_relation(self, partner, chapter, warnings):
        """Link the member to their local chapter.

        Only the chapter link is created here. The "Member Of" relation to the
        association is owned by ``association_membership`` itself, which writes it
        when a membership becomes active — a signup sitting in ``waiting`` is
        deliberately not yet a member of anything.

        Returns whether the member is linked, so the chatter can drop the raw
        chapter name once it has become a relation.
        """
        if not partner or not chapter:
            return False
        if (
            "res.partner.relation" not in self.env
            or "res.partner.relation.type" not in self.env
        ):
            warnings.append("relation_module_missing")
            return False
        relation_type = self._find_relation_type(CHAPTER_RELATION_NAMES)
        if not relation_type:
            warnings.append("chapter_relation_type_missing")
            return False

        Relation = self.env["res.partner.relation"]
        # Both orientations: the same link recorded the other way round is the
        # same fact, and creating it twice is what a replay would otherwise do.
        existing = Relation.search(
            [
                ("type_id", "=", relation_type.id),
                "|",
                "&",
                ("left_partner_id", "=", partner.id),
                ("right_partner_id", "=", chapter.id),
                "&",
                ("left_partner_id", "=", chapter.id),
                ("right_partner_id", "=", partner.id),
            ],
            limit=1,
        )
        if existing:
            return True
        Relation.create(
            {
                "left_partner_id": partner.id,
                "right_partner_id": chapter.id,
                "type_id": relation_type.id,
            }
        )
        return True

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

        # The stated method itself is kept in the partner's intake note.
        stated = (payload.get("payment_method") or "").strip()
        mode = self._resolve_payment_mode(payload, company)
        if mode and "customer_payment_mode_id" in self.env["res.partner"]._fields:
            partner.customer_payment_mode_id = mode.id
        elif stated:
            # An association without payment modes is a configuration gap the
            # operator should see, not something to quietly normalise.
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
    def _log_membership_notes(self, payload, membership, warnings, notes):
        """What belongs to *this membership*: the note to ECOnGOOD, the money,
        and anything the intake had to decide for itself.

        Everything that describes the member rather than the membership goes on
        the partner instead — see ``_log_partner_notes``.
        """
        lines = list(notes)
        value = payload.get("note_to_econgood")
        if value not in (None, "", False):
            lines.append(f"{_('Note to ECOnGOOD')}: {value}")

        donation = to_amount(payload.get("donation_amount"), default=0.0)
        if donation > 0:
            lines.append(
                _("Donation offered on signup: %(amount)s %(currency)s")
                % {
                    "amount": donation,
                    "currency": payload.get("currency") or membership.currency_id.name,
                }
            )

        inference = [w for w in warnings if w in INFERENCE_WARNINGS]
        if inference:
            lines.append(
                _("The membership could not be inferred cleanly (%s); please check"
                  " the product and the fee.") % ", ".join(inference)
            )
        if warnings:
            lines.append(_("Intake warnings: %s") % ", ".join(warnings))

        self._post_intake_note(payload, membership, lines)

    @api.model
    def _log_partner_notes(self, payload, partner, chapter_linked):
        """What describes the *member* and has no field yet, on their contact."""
        lines = []
        for key, label in (
            ("service_actively_work", _("Wants to actively work in ECOnGOOD")),
            ("service_newsletter", _("Newsletter subscription")),
            ("service_website_listing", _("Listing on the website")),
            ("birth_city", _("City of birth")),
            ("birthday", _("Birthday")),
            ("company_sector", _("Sector")),
            ("assoc_sector", _("Sector")),
            ("payment_method", _("Payment method")),
            ("payment_frequency", _("Payment frequency")),
        ):
            value = payload.get(key)
            if value not in (None, "", False):
                lines.append(f"{label}: {value}")

        # Field 1604 is the privacy *notice*, which has no field of its own --
        # only 2458, the agreement, maps to privacy_agreement_signed_date. This
        # line is the only record that the notice was acknowledged.
        if truthy(payload.get("data_protection_notice")):
            lines.append(_("Privacy notice acknowledged on the signup form."))

        # Only worth recording when it did not become a relation.
        if not chapter_linked:
            value = payload.get("econ_local_chapter")
            if value not in (None, "", False):
                lines.append(f"{_('Local chapter')}: {value}")

        if lines:
            self._post_intake_note(payload, partner, lines)

    @api.model
    def _post_intake_note(self, payload, record, lines):
        """Post one intake note, unless the same one is already there.

        Replaying an entry is normal and must stay cheap: without this check ten
        replays leave ten identical notes on the record.
        """
        if not record or not lines:
            return
        reference = self._entry_reference(payload)
        header = _("Website signup")
        if reference:
            header = _("Website signup (Formidable entry %s)") % reference

        # message_post escapes a plain string, so the body has to be Markup or the
        # chatter shows literal tags. Markup's %-formatting escapes its arguments,
        # which is what keeps submitted values from injecting HTML.
        items = Markup("").join(Markup("<li>%s</li>") % line for line in lines)
        body = Markup("<p>%s</p><ul>%s</ul>") % (header, items)
        if any(message.body == body for message in record.message_ids):
            return
        record.message_post(body=body)

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    @api.model
    def _entry_reference(self, payload):
        value = payload.get("entry_id") or payload.get("formidable_number")
        return str(value).strip() if value not in (None, "", False) else None

    @api.model
    def _filter_fields(self, model, vals):
        """Drop values that cannot be written on this database.

        Two reasons a key is dropped: the field belongs to an optional module that
        is not installed (the same guard the repo-side tooling applies, so optional
        OCA modules stay optional), or it is not writable — a non-stored or
        readonly computed field raises rather than storing anything.
        """
        fields_ = self.env[model]._fields
        writable = {}
        for key, value in vals.items():
            field = fields_.get(key)
            if field is None or not field.store:
                continue
            if field.compute and field.readonly:
                continue
            writable[key] = value
        return writable

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
