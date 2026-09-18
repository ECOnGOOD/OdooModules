# Association Membership

`association_membership` is a lean Odoo 18 CE module for association membership management in multi-company setups. It models the membership relationship and its yearly billing artifacts on top of standard Odoo accounting and OCA `donation_base`, without forking either.

## Architecture

Two core models, both `mail.thread`-tracked and `_check_company_auto`:

- **`membership.membership`** — the relationship between a partner, a company, and a membership product. Carries the lifecycle state, the start date (cancellation/end dates are kept only on cancelled or terminated memberships), the membership number, the (optionally separate) invoice contact, and the per-membership `amount` (defaults to the product price, editable per membership).
- **`membership.contribution`** — the per-year billing artifact, one per `(membership, year)`. Keeps its own `product_id` and `amount` from the moment it is created, so later tier changes never rewrite history. `amount_invoiced` / `amount_paid` / `billing_status` come from the linked `account.move`, or from "Mark as Paid" (with a payment date) in manual mode. May link to a `donation.tax.receipt`.

`account.move.line` is extended with `membership_id` / `membership_contribution_id` / `membership_year` so invoice lines round-trip to contributions.

### Products

- **Template = membership type, variant = tier** (e.g. an employee range). A membership stores the variant; `product_tmpl_id` is the type. A tier change stays within the membership; a type change means ending it and starting a new one. Open memberships may not overlap per type.
- Membership products are flagged with `membership_ok` and `membership_partner_type` (`any` / `person` / `company`) on the product template. A membership may only use a flagged product of exactly its company (or without company) that matches the member's partner type.
- The price is `lst_price` (template price plus the variant's `price_extra`), through `product.product._get_membership_price()`. No pricelists.
- Retiring a tier = archiving its variant. The renewal wizard skips memberships on an archived tier and says so.

## Key Features

- **Multi-company first.** Settings, sequences, templates, contributions — all per company. Designed for federation hierarchies (national / regional / local as separate companies).
- **One-step onboarding.** The *New Membership* wizard creates, activates and bills a membership and sends the welcome email.
- **Three invoicing strategies** (`manual` / `draft` / `confirm`) per company. The strategy is stored on each contribution when it is created.
- **Tax receipts via OCA `donation_base`**, per payment and annual (see below); the German Zuwendungsbestätigung is in `association_membership_l10n_de`.
- **Email recipients for organisation members** per company: the organisation, its contact person, its invoice contact, or both.
- **Auto-activation on payment** (per-company toggle).
- **Manual annual renewal wizard** that groups eligible memberships by `(invoice partner, company, year, currency)` and creates one invoice per group atomically.
- **Per-company membership-number sequence** with configurable prefix (supports `%(year)s`), padding, and exposed "next number".
- **Pre-built reporting views** — Current/Unpaid/New/Cancelled members, Contribution History, Renewal Candidates, Per-company Member List.

## Dependencies

`account`, `contacts`, `donation_base`, `mail`, `product`. OCA `partner_contact_address_default` is optional: when installed, its `partner_contact_id` is the contact person for member emails.

## Tax Receipts

The module relies on `donation_base` for the receipt model, its annual wizard and its partner option (`tax_receipt_option`: None / Each / Annual). Eligibility is set per product with `tax_receipt_ok`. Receipts are under **Memberships → Tax Receipts** (accounting/invoicing users only, as in `donation_base`).

- **Manual mode** (no invoices): "Mark as Paid" stores a payment date. Receipts are only issued annually: *Create Annual Receipts* collects the paid, eligible contributions of the period for partners with option Annual **or** Each. Contributions without a payment date (e.g. imported history) are never receipted.
- **Invoice mode**: partners with option Each get a receipt automatically once the invoice is fully `paid` (not while `in_payment`). Partners with option Annual get their paid invoices on the annual receipt.
- The annual receipt links the contributions it covers (`membership_contribution_ids`); they are skipped on later runs.
- A refund, or a payment that is unreconciled (e.g. a returned direct debit), does **not** delete a receipt: it adds a to-do activity to reclaim or correct it.

## Membership Lifecycle

States and allowed transitions:

```
draft      ──→ waiting
waiting    ──→ draft │ active │ cancelled │ terminated
active     ──→ cancelled │ terminated │ draft
cancelled  ──→ waiting │ active │ terminated │ draft
terminated ──→ waiting │ draft
```

- `draft` is editable scratch; the Contributions tab is hidden. Memberships can only be deleted from this state.
- Reverting to `draft` and deleting are blocked once a membership has contributions.
- `waiting` allows contribution creation and invoicing.
- `active` is the steady state.
- `cancelled` is "scheduled to end at `date_end`" — still business-active.
- `date_cancelled`, `date_end`, and `cancel_reason` can only be set on `cancelled`/`terminated` memberships (enforced by constraint) and are cleared automatically when reverting to `draft`, reopening or reactivating.
- `terminated` is the final state; `action_reopen_waiting` (used by the importer) reopens it.

## Workflows

### Onboarding a new member

**Memberships → New Membership**, or **Create Membership** on the partner form, opens the wizard: member, invoice contact, company, product (filtered as described under Products), start date, with a preview of the price, the membership number and the email recipients. With **Activate immediately** (default) it also:

- creates the contribution of the current year (in manual mode with status "To Invoice", no invoice),
- sends the welcome email to the recipients shown.

Without it, the membership stays `waiting`; **Activate** later opens the activation wizard, which offers the same contribution and welcome email and, for invoice strategies, confirms and sends an existing draft invoice. When a cancelled membership is reactivated, the welcome email is not ticked again if one was already sent.

### Importing members

Imports run through the repository's `scripts/import_contacts.py`, not through a wizard in the module. The importer activates memberships with `action_activate_direct` (no wizard, no emails).

### Renewal

`Memberships → Configuration → Renewal` opens the renewal wizard. Pick target year, companies (default = all allowed), optional product filter, optional dry-run, and optional invoice date. The wizard groups eligible memberships by `(invoice partner, company, year, currency)` and creates one invoice per group, atomically.

A scheduled `Membership Renewal` cron exists but is disabled by default — annual renewal is intended to be operator-triggered. The `Membership Termination` cron runs daily and is enabled; it moves expired-cancelled memberships to `terminated`.

### Cancellation

Click **Cancel Membership** on the form (available for active and waiting memberships) → opens the Cancel wizard:
- Pick cancel date and end date (defaults to Dec 31 of current year).
- Optional cancellation reason.
- Optional cancellation message via the company's cancellation template (editable in the wizard, recipients per the company setting).
- On confirm: if `date_end <= today` → `terminated`, otherwise `cancelled`.

### Followers

Neither the user who creates a membership nor the sender of a welcome or cancellation email becomes a follower, so staff do not get copies of member emails by default. Follow a membership on purpose to get copies and reply notifications.

## Configuration (per company)

`Settings > Membership`:

- **Email recipients for organisation members** — the organisation, contact person (default), invoice contact, or contact person and invoice contact. Individuals always receive their own emails.
- **Auto-activate on payment** — toggle.
- **Renewal year offset** — the cron defaults to `current_year + offset` (default 1).
- **Invoicing strategy** — `manual` / `draft` / `confirm`.
- **Contribution year override** — empty by default: new contributions default to the current year. Set a future year to pre-create next year's contributions (past values always fall back to the current year).
- **Email templates** — Activation Invoice (account.move), Welcome (membership.membership), Cancellation (membership.membership). Defaults ship with the module (English and German) and are assigned automatically to companies that have none — customize per company.
- **Member numbers** — prefix (`%(year)s` supported), padding, next number, and a live preview of the next generated number. Give every association its own prefix: numbers are unique across all companies.

## Permissions

- `association_membership.group_membership_manager` — full CRUD on memberships and contributions, can run wizards, edit settings.
- `association_membership.group_membership_viewer` — read-only access to memberships and contributions; can see them on partner forms.
- Creating invoices and tax receipts additionally needs the accounting rights of `account` / `donation_base`.

## Testing

```bash
./run_tests.sh association_membership
./run_tests.sh association_membership_l10n_de
```

## Planned Improvements (TODOs)

- **Renewal cron** — currently disabled with a hardcoded next call; review enablement and add coverage for the cron-driven per-company renewal path.
- **Archive exposure** — memberships support archiving (`active` field, kanban ribbon) but the form offers no archive/unarchive action.
- **Reporting** — pre-built views are list-based only; consider dashboards/KPIs (member growth, churn, revenue per year) on top of contributions.
- The open post-launch items are tracked in `docs/membership/association_membership_launch_gaps.md` (WP5) in the main repository.

## Notes

- **Members menu** opens the per-membership kanban (one card per membership record). A partner-aggregated "members overview" (one card per partner) is intentionally not provided.
