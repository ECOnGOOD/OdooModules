# Association Membership

`association_membership` is a lean Odoo 18 CE module for association membership management in multi-company setups. It models the membership relationship and its yearly billing artifacts on top of standard Odoo accounting and OCA `donation_base`, without forking either.

## Architecture

Two core models, both `mail.thread`-tracked and `_check_company_auto`:

- **`membership.membership`** — the relationship between a partner, a company, and a membership product. Carries the lifecycle state, the start/end/cancel dates, the membership number, the (optionally separate) invoice contact, and the per-membership `amount` (defaults to the product's `list_price`, editable per membership).
- **`membership.contribution`** — the per-year billing artifact, one per `(membership, year)`. Holds a writable `amount` (defaults to the membership's `amount` at creation), and computed `amount_invoiced` / `amount_paid` / `is_free` / `billing_status` derived from the linked `account.move`. May also link to a `donation.tax.receipt`.

`account.move.line` is extended with `membership_id` / `membership_contribution_id` / `membership_year` so invoice lines round-trip to contributions. Membership products are identified by their category (`res.company.membership_product_category_id`).

## Key Features

- **Multi-company first.** Settings, sequences, templates, contributions — all per-company. Designed for federation hierarchies (national / regional / local as separate companies).
- **Tax receipts via OCA `donation_base`.** Per-payment receipts are auto-issued on invoice payment for partners with option "Each"; annual receipts are produced via OCA's wizard for partners with option "Annual".
- **Three invoicing strategies** (`manual` / `draft` / `confirm`) per company, with a per-activation override in the activation wizard.
- **Auto-activation on payment** (per-company toggle) — paying a contribution invoice activates the membership.
- **Manual annual renewal wizard** that groups eligible memberships by `(invoice partner, company, year, currency)` and creates one invoice per group atomically.
- **Bulk import wizard** (CSV/XLSX) with dry-run preview and idempotent upserts.
- **Per-company membership-number sequence** with configurable prefix (supports `%(year)s`), padding, and exposed "next number".
- **Pre-built reporting views** — Current/Unpaid/New/Cancelled members, Contribution History, Renewal Candidates, Per-company Member List.

## Tax Receipts

The module relies on `donation_base` for the receipt artifact:

1. Mark eligible products with `tax_receipt_ok = True` on the product form.
2. Set the partner's `tax_receipt_option` (None / Each / Annual).

**Per-payment receipts ("Each")** — automatic. When an invoice with a contribution line is marked paid, eligible contributions auto-create a `donation.tax.receipt` and stamp it on `contribution.tax_receipt_id`. Bulk send via the OCA list-action "Print Tax Receipts" on `Accounting → Donations → Tax Receipts`.

**Annual receipts ("Annual")** — manual at year-end via OCA's wizard at `Accounting → Donations → Annual Tax Receipts`. The module hooks into `donation.tax.receipt.update_tax_receipt_annual_dict`: each annual partner's paid contributions for the chosen window are aggregated into one annual receipt per partner. Contributions covered by the new receipt get `tax_receipt_id` stamped; already-stamped contributions are skipped on subsequent runs. Stamping uses calendar-year scope (Jan 1 – Dec 31 of the receipt's `donation_date`).

## Membership Lifecycle

States and allowed transitions:

```
draft      ──→ waiting
waiting    ──→ draft │ active
active     ──→ cancelled │ terminated │ draft
cancelled  ──→ active │ terminated │ draft
terminated ──→ draft
```

- `draft` is editable scratch; the Contributions tab is hidden. Memberships can only be deleted from this state.
- `waiting` allows contribution creation and invoicing.
- `active` is the steady state.
- `cancelled` is "scheduled to end at `date_end`" — still business-active.
- `terminated` is the final state. Reverting to `draft` is the only way out (and clears cancellation fields).

## Workflows

### Onboarding a new member

1. Create the partner (or pick an existing one).
2. Use **Create Membership** on the partner form, pick the membership product, leave state at `draft` while filling details.
3. Submit → `waiting`. Create the yearly contribution (the configured invoicing strategy determines whether an invoice is raised in draft or posted).
4. Click **Activate** → opens the Activation wizard:
   - If a draft invoice exists for the current year, the wizard offers to confirm it (and optionally email it via the activation invoice template).
   - The wizard offers to send the welcome message using the welcome template (membership-model template, editable in the wizard).
   - On confirm: state → `active`, invoice posted (if chosen), invoice email sent (if chosen), welcome message sent (if chosen, with welcome-sent date stamped).

### Bulk import

`Membership → Configuration → … (Import)` opens the import wizard. Upload a CSV or XLSX with columns including `partner_external_ref`, `partner_name`, `product_code`/`product_name`, `date_start`, optional `membership_number`, `membership_year`, `amount`, `state`, etc. Dry-run preview is available; results are reported per row (created / updated / error).

### Renewal

`Membership → Configuration → Renewal` opens the renewal wizard. Pick target year, companies (default = all allowed), optional product filter, optional dry-run, and optional invoice date. The wizard groups eligible memberships by `(invoice partner, company, year, currency)` and creates one invoice per group, atomically.

A scheduled `Membership Renewal` cron exists but is disabled by default — annual renewal is intended to be operator-triggered. The `Membership Termination` cron runs daily and is enabled; it moves expired-cancelled memberships to `terminated`.

### Cancellation

Click **Cancel Membership** on the form → opens the Cancel wizard:
- Pick cancel date and end date (defaults to Dec 31 of current year).
- Optional cancellation reason.
- Optional cancellation message via the company's cancellation template (membership-model template, editable in the wizard, recipients pre-filled with the member partner).
- On confirm: if `date_end <= today` → `terminated`, otherwise `cancelled`.

### Year-end tax receipts

For partners with `tax_receipt_option = 'annual'`: open `Accounting → Donations → Annual Tax Receipts`, pick the year, run. Per-payment receipts (option `each`) are issued automatically as invoices are paid — no operator action needed.

## Configuration (per company)

`Settings > Association Membership`:

- **Membership product category** — root category that identifies membership products.
- **Auto-activate on payment** — toggle.
- **Renewal year offset** — the cron defaults to `current_year + offset` (default 1).
- **Invoicing strategy** — `manual` / `draft` / `confirm`.
- **Email templates** — Activation Invoice (account.move), Welcome (membership.membership), Cancellation (membership.membership).
- **Member numbers** — prefix (`%(year)s` supported), padding, next number.

## Permissions

- `association_membership.group_membership_manager` — full CRUD on memberships and contributions, can run wizards, edit settings.
- Internal users (`base.group_user`) — read-only access to memberships and contributions; can see them on partner forms.

## Testing

```bash
./run_tests.sh association_membership
```

The test suite is currently a minimal smoke pass after the donation refactor — additional scenarios should be added as the module grows.
