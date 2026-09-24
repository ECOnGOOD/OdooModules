# Association Membership

`association_membership` is a lean Odoo 18 CE module for association membership management in multi-company setups. It models the membership relationship and its yearly billing artifacts on top of standard Odoo accounting and OCA `donation_base`, without forking either.

Multi-company first: settings, sequences, templates and periods are all per company, designed for federation hierarchies (national / regional / local as separate companies).

## Data Model

```mermaid
erDiagram
    RES_PARTNER   ||--o{ MEMBERSHIP : "member / invoice contact"
    RES_COMPANY   ||--o{ MEMBERSHIP : owns
    PRODUCT       ||--o{ MEMBERSHIP : "type (tmpl) + tier (variant)"
    MEMBERSHIP    ||--o{ PERIOD : "one per year"
    PERIOD  |o--o| ACCOUNT_MOVE : "invoice / refund"
    PERIOD  |o--o| TAX_RECEIPT : "per payment or annual"
    ACCOUNT_MOVE  ||--o{ MOVE_LINE : "lines"
    MOVE_LINE     |o--o| PERIOD : "round-trip metadata"
```

### `membership.membership`

The relationship between a partner, a company and a membership product. `mail.thread` + `mail.activity.mixin`, `_check_company_auto`, ordered by `date_start desc`.

| Field | Type | Notes |
| --- | --- | --- |
| `name` | Char | computed, stored — display name |
| `partner_id` | M2o `res.partner` | **Member**, required, tracked |
| `invoice_partner_id` | M2o `res.partner` | computed, stored: the member's invoice address (`address_get(["invoice"])`, i.e. its invoice-type child, else the member) |
| `contact_partner_id` | M2o `res.partner` | computed: an organisation's contact person (`address_get(["contact"])`), empty when that is the organisation itself |
| `company_id` | M2o `res.company` | required, default = current company |
| `product_id` | M2o `product.product` | **tier** (variant), required, tracked |
| `product_tmpl_id` | related, stored | **membership type** (template) |
| `state` | Selection | `draft` / `waiting` / `active` / `cancelled` / `terminated` |
| `date_start` | Date | required, default today |
| `date_end`, `date_cancelled`, `cancel_reason` | Date / Date / Text | only on `cancelled` / `terminated` (constraint) |
| `date_welcome_sent` | Date | set when the welcome mail goes out |
| `membership_active` | Boolean | computed, stored — live today (`active` or `cancelled`) |
| `membership_number` | Char | globally unique (SQL constraint); the next value of the company's member number sequence |
| `override_membership_number` | Boolean | allows a manual number |
| `membership_number_preview` | Char | computed preview of the next number |
| `amount` | Monetary | computed from the product price, editable per membership |
| `currency_id` | related | company currency |
| `invoicing_strategy` | Selection | empty = follow the company setting |
| `period_ids` | O2m | per-year billing artifacts |
| `period_count`, `last_period_year`, `last_billing_status` | computed | list/kanban columns |
| `duplicate_period_year_warning` | Char | computed UI warning |
| `active` | Boolean | archiving |

Key state sets used by reports, partner filters and the number display:
`BUSINESS_ACTIVE_STATES = (active, cancelled)` · `CURRENT_MEMBER_STATES = (waiting, active, cancelled)`.

### `membership.period`

The per-year billing artifact, one per `(membership, year)` (SQL constraint). Keeps its own `product_id` and `amount` from creation, so later tier changes never rewrite history.

| Field | Type | Notes |
| --- | --- | --- |
| `membership_id` | M2o | required, `ondelete=restrict` |
| `membership_year` | Integer | required; the identity of the period |
| `date_start`, `date_end` | Date | computed, stored — 1 Jan–31 Dec, clipped to the membership |
| `product_id`, `amount` | M2o / Monetary | **frozen at creation**, not re-derived |
| `is_free` | Boolean | computed, stored — `amount == 0` |
| `membership_invoicing_strategy` | Selection | the strategy that actually applied — refreshed while unbilled, then frozen |
| `invoice_id`, `invoice_line_id`, `refund_move_id` | M2o `account.move` / line | |
| `amount_invoiced`, `amount_paid` | Monetary | computed+stored, `readonly=False` (manual mode writes) |
| `date_paid` | Date | the day the money came in: "Mark as Paid" (manual) or the invoice's latest payment; tax receipts use it |
| `date_invoice` | related, stored | from the invoice |
| `billing_status` | Selection | computed+stored, `readonly=False` — see below |
| `tax_receipt_id` | M2o `donation.tax.receipt` | readonly |
| `partner_id`, `company_id`, `currency_id` | related, stored | from the membership |
| `invoice_partner_id`, `note` | M2o / Text | the invoice contact is taken from the membership when the invoice is created; an issued invoice keeps it |

### Extended standard models

| Model | Added |
| --- | --- |
| `account.move.line` | `membership_id`, `membership_period_id`, `membership_year` — invoice lines round-trip to periods |
| `account.move` | `_post` / `_invoice_paid_hook` — drive period status and per-payment receipts |
| `account.partial.reconcile` | `unlink` — an unreconciled payment raises a to-do instead of deleting a receipt |
| `product.template` | `membership_ok`, `membership_partner_type` (`any` / `person` / `company`) |
| `product.product` | `_membership_product_domain()`, `_get_membership_price()` |
| `res.partner` | `membership_ids`, `membership_period_ids`, number displays, **Create Membership** |
| `res.company` | all membership settings (see Configuration) |
| `donation.tax.receipt` | `membership_period_ids` + annual collection of paid periods |

## Membership Lifecycle

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> waiting : Submit
    draft --> active : Activate (through waiting)
    waiting --> active : Activate (wizard / direct)
    waiting --> draft : Revert to Draft
    active --> cancelled : Cancel
    cancelled --> terminated : cron (date_end passed), or Cancel with an end date reached
    cancelled --> active : Reactivate
    cancelled --> draft : Revert to Draft
    terminated --> draft : Revert to Draft
```

| State | Meaning | Rules |
| --- | --- | --- |
| `draft` | not in force: new, or taken back for correction | **only** state that can be deleted (without periods); keeps existing periods and its number, but gets no new periods |
| `waiting` | submitted, not yet active | periods and invoicing allowed |
| `active` | steady state | ends only through Cancel |
| `cancelled` | *scheduled to end at `date_end`* | still business-active |
| `terminated` | ended | back only through Revert to Draft |

- An active membership is never reverted to draft: it is corrected by editing, or cancelled and then reverted.
- A membership that was never active is not cancelled: it goes back to draft and is deleted (without periods) or archived.
- A cancellation whose end date is today or earlier terminates at once, through `cancelled`.
- `date_cancelled` / `date_end` / `cancel_reason` are only valid on `cancelled` / `terminated` and are cleared automatically on revert or reactivation.
- Reverting to draft closes the "Member Of" relation (`partner_multi_relation`, when installed) unless another membership keeps it open.
- Re-running a cancel on an already cancelled membership corrects its dates/reason rather than failing.
- The importer's `action_reopen_waiting` goes through draft, and `action_cancel_direct` activates first when needed.

## Period Billing Status

An invoice, when there is one, always wins — in every strategy. Only without an invoice do strategies differ.

```mermaid
flowchart TD
    A[period] --> B{posted refund?}
    B -- yes --> R[refunded]
    B -- no --> C{invoice linked?}
    C -- yes --> D{invoice state}
    D -- cancel --> X[cancelled]
    D -- paid / in_payment --> P[paid]
    D -- partial --> PP[partially paid]
    D -- other --> I[invoiced]
    C -- no --> M{strategy manual<br/>and status already set?}
    M -- yes --> K[keep: Mark as Paid / import]
    M -- no --> F{amount == 0?}
    F -- yes --> W[waived]
    F -- no --> T[to invoice]
```

`amount_invoiced` / `amount_paid` follow the invoice line and its residual whenever there is an invoice; in manual mode without one they keep what **Mark as Paid** or the importer wrote.

## Products

- **Template = membership type, variant = tier** (e.g. an employee range). A membership stores the variant; a tier change stays within the membership, a type change means ending it and starting a new one. Open memberships may not overlap per type.
- Products are flagged `membership_ok` with a `membership_partner_type`. A membership may only use a flagged product of exactly its company (or without company) matching the member's partner type.
- The price is `lst_price` (template price + variant `price_extra`) via `_get_membership_price()`. No pricelists.
- Retiring a tier = archiving its variant; the renewal wizard skips those memberships and says so.

## Processes

### Onboarding a new member

**Memberships → New**, or **Create Membership** on the partner form. There is no separate
creation wizard: the form already previews the member number and the fee and filters the
products by partner type.

```mermaid
flowchart LR
    W[Membership form<br/>member · invoice contact · company<br/>product · start date · price/number preview] --> SV[Save: state = draft]
    SV --> A{next step}
    A -- Activate --> AC[state = active, via waiting]
    A -- Submit --> WT[state = waiting]
    WT -.-> AC
    AC --> CO[period for the default year<br/>created, or an unbilled one reused]
    CO --> S{strategy}
    S -- manual --> TI[status: To Invoice, no invoice]
    S -- draft --> DI[draft invoice, left in draft]
    S -- confirm --> PI[posted invoice + optional email]
    AC --> WM[welcome email to computed recipients]
```

The welcome email is pre-ticked only for a first activation — never when reactivating a
cancelled membership or activating one that was reverted to draft. A fee of 0 cannot be invoiced; the
wizard says so instead of silently skipping it.

### Renewal

**Memberships → Configuration → Renewal** — the wizard is the intended path; the `Membership Renewal` cron exists but is **disabled** by default.

```mermaid
flowchart LR
    I[target year · companies · optional product filter<br/>dry run · invoice date] --> E[eligible: active + cancelled<br/>whose date_end still covers the year]
    E --> G[group by invoice partner × company × year × currency]
    G --> V[one invoice per group, atomically]
    V --> L[result lines: status · message · amount · invoice]
```

### Cancellation

**Cancel Membership** (active/waiting) or **Edit Cancellation** (already cancelled) → Cancel wizard.

```mermaid
flowchart LR
    C[cancel date · end date default Dec 31<br/>reason, required] --> U{unpaid periods<br/>of the cancellation year onward}
    U -- keep --> M
    U -- drop --> D[cancel draft invoices + delete periods<br/>posted invoices never touched]
    D --> M[optional cancellation email<br/>per company template, editable]
    M --> R{end date reached?}
    R -- yes --> T[terminated]
    R -- no --> CA[cancelled]
```

### Tax receipts

Built on `donation_base`: its receipt model, annual wizard and partner option (`tax_receipt_option`: None / Each / Annual). Eligibility per product via `tax_receipt_ok`. Receipts live under **Members → Tax Receipts** (accounting users only), with *Create Annual Receipts* and *Print Receipts*.

| Mode | Option *Each* | Option *Annual* |
| --- | --- | --- |
| **Invoice** (`draft` / `confirm`) | receipt issued automatically once the invoice is fully `paid` (not `in_payment`) | paid invoices land on the annual receipt of the year they were paid |
| **Manual** (no invoice) | no per-payment receipt — collected annually | *Create Annual Receipts* collects paid, eligible periods with a `date_paid` |

A fee belongs to the year it was **paid**. `date_paid` is the donation date of a per-payment receipt, selects the periods of an annual run, and is listed in the German Anlage. It is set by "Mark as Paid", or from the invoice's latest payment when the invoice becomes `paid` (cleared again when the payment is unreconciled). Periods without a payment date (e.g. imported history) are never receipted. The annual receipt links the periods it covers (`membership_period_ids`), which are skipped on later runs. A refund or an unreconciled payment does **not** delete a receipt — it adds a to-do activity to reclaim or correct it.

- **Annual run:** the dialog shows what would be created (donors, periods, total) before anything is written; *Preview* opens those periods grouped by member. *Only These Donors* limits the run. Donors that already have an annual receipt in the range are skipped and listed, instead of aborting the run. An annual receipt is dated the day it is created; its donation date is the end of the range.
- **Sending:** *Send* on a receipt, or on several selected in the list (Action → Send): one email per receipt with its own PDF, logged on the receipt. The template is set per company (Settings → Communications → Tax Receipt Email Template); empty means the default, which `association_membership_l10n_de` makes the Zuwendungsbestätigung for German companies. Emails go to the donor.
- **Printing:** *Print Receipts* prints every receipt not yet printed, with the company's receipt PDF (the Zuwendungsbestätigung for German companies with `association_membership_l10n_de`), and records the print date.

### Scheduled actions

| Cron | Interval | Default | Does |
| --- | --- | --- | --- |
| `Membership Termination` | daily | **enabled** | moves expired-`cancelled` memberships to `terminated` |
| `Membership Renewal` | yearly | disabled | per-company renewal at `current_year + offset` |

### Importing members

Imports run through the repository's `scripts/import_contacts.py`, not through a wizard. The importer uses `action_activate_direct` / `action_cancel_direct` / `action_reopen_waiting` — no wizards, no emails.

### Followers

Neither the creator of a membership nor the sender of a welcome or cancellation email becomes a follower, so staff do not get copies of member emails by default. Follow a membership on purpose to get copies and reply notifications.

## Configuration (per company)

`Settings > Membership`. The settings apply to the company selected in the company switcher (shown at the top of the page in multi-company); a membership follows the settings of its own company.

| Setting | Field | Default |
| --- | --- | --- |
| Email recipients for organisation members | `membership_company_mail_recipients` | contact person — also: the organisation, invoice contact, or both. Individuals always get their own emails |
| Invoicing strategy | `membership_invoicing_strategy` | `manual` — `draft` / `confirm` create an invoice on activation and renewal; overridable per membership (the form shows the company's value next to it, the activation wizard says which one applies) |
| Period year override | `membership_default_period_year` | `0` = current year; a future year pre-creates next year's periods |
| Email templates | activation invoice / welcome / cancellation | shipped EN + DE, auto-assigned to companies without one |
| Organisation templates | `membership_*_org_template_id` | empty — optional; organisation members get these when set, else the templates above (`membership._get_mail_template(kind)`). A single template can also differ with `t-if="object.partner_id.is_company"` sections |
| Tax receipt email template | `membership_tax_receipt_template_id` | empty = `donation_base`'s template, or the Zuwendungsbestätigung for German companies with `association_membership_l10n_de` |
| Own member numbering | `member_number_own_sequence` | **off by default: every company uses the default numbering**, the `ir.sequence` with code `association.membership.number.seq` and no company (`MEM/%(year)s/`, 5 digits, `no_gap`). On = a sequence of that code for this company, starting where the default stands; off again archives it |
| Number format | prefix, suffix, digits, next number | the fields of the effective sequence; editable here only with own numbering. The default format is changed on its sequence by an administrator |

## Reporting

Pre-built list views: Current / Unpaid / New members, Scheduled to End, Former Members, Period History, Renewal Candidates, Per-company Member List.

The **Members** menu opens the per-membership kanban (one card per membership record). A partner-aggregated overview is intentionally not provided.

## Permissions

| Group | Can |
| --- | --- |
| `group_membership_manager` | full CRUD on memberships and periods, run wizards, edit settings |
| `group_membership_viewer` | read-only memberships and periods, incl. on partner forms |

Creating invoices and tax receipts additionally needs the accounting rights of `account` / `donation_base`.

## Dependencies

`account`, `contacts`, `donation_base`, `mail`, `product`. OCA `partner_contact_address_default` is optional: when installed, its `partner_contact_id` is the contact person for member emails. The German Zuwendungsbestätigung lives in `association_membership_l10n_de`.

## Testing

```bash
./run_tests.sh association_membership
./run_tests.sh association_membership_l10n_de
```

## Planned Improvements (TODOs)

- **Renewal cron** — currently disabled with a hardcoded next call; review enablement and add coverage for the cron-driven per-company renewal path.
- **Archive exposure** — memberships support archiving (`active` field, kanban ribbon) but the form offers no archive/unarchive action.
- **Reporting** — views are list-based only; consider dashboards/KPIs (member growth, churn, revenue per year) on top of periods.
- Open post-launch items are tracked in `docs/membership/association_membership_launch_gaps.md` in the main repository.
