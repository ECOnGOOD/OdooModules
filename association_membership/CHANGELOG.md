# Changelog

## 18.0.6.3.0 — lifecycle, contacts and numbering (WP12)

- **Simpler state machine.** Draft → Waiting / Active · Waiting → Draft / Active · Active →
  Cancelled · Cancelled → Active / Terminated / Draft · Terminated → Draft.
  - "Reopen" is gone; Revert to Draft is available from Waiting, Cancelled and Terminated, also when periods exist.
  - A draft keeps its periods and its number. It gets no new periods, cannot be deleted while it has periods, and closes the "Member Of" relation.
  - A waiting membership is no longer cancelled; it goes back to draft.
  - An end date of today or earlier still terminates in one click, through Cancelled.
  - The importer's direct methods route through the new transitions.
- **Invoice contact and contact person come from the member.** The membership's Invoice
  Contact is computed from the partner's invoice address, so a new or changed invoice
  child reaches every membership. An organisation's Contact Person is shown next to it,
  read-only. An unbilled period takes the invoice contact when its invoice is created.
- **Members is the app.** The root menu is "Members" and opens the members. Submenus:
  Members, Memberships, Periods, Tax Receipts, Reporting, Configuration. The default
  filter is "Current Members" (waiting, active, cancelled), as in the reports.
- **Member numbers come from `ir.sequence` alone.** The default sequence holds the whole
  format (`MEM/%(year)s/`, 5 digits, `no_gap`). Own numbering is a company sequence with
  the same code, edited in Settings (prefix, suffix, digits, next number). The company
  fields for prefix and padding are gone.
- **Migration:** every company switches to the default numbering. Own counters are
  archived, and the default counter is lifted above them. Companies with a prefix of their
  own are logged. Invoice contacts are recomputed.
- The welcome email is also pre-ticked when activating straight from Draft.

## 18.0.6.2.0 — membership types and tiers (WP11)

- **Membership Products lists membership types** (product templates), one row per type
  with company, "Membership For", number of tiers and tax receipt flag. A type's **Tiers**
  button opens its tiers in the membership tier list (reference, tier, company, price),
  in place of Odoo's standard Variants button.
- **Every internal user gets "Manage Product Variants"** (Settings → Variants), so tier
  prices can be edited per value under "Attributes & Variants".

## 18.0.6.1.0 — fixes from the browser test (WP10)

- **Annual tax receipts:** a run with nothing to receipt now shows a notice that names the
  company, the date range and what makes a period count, instead of donation_base's
  "Invalid Operation – No annual tax receipt to generate". The dialog stays open so the
  range can be changed.
- **Tax receipts and periods are linked in the interface:** a **Periods** smart button on
  the receipt form, and a Tax Receipt column in the period lists.
- **Invoice email logged on the membership:** when the activation sends the invoice, the
  membership chatter gets one line naming the invoice and the recipient.
- **Membership number on the form:** preview and override input share one row labelled
  "Membership Number"; the Override box stays on the right.
- **Partner form:** the membership number moved below Tags as a normal labelled field, and
  both partner-form extensions are now one view (no more load-order dependency).
- **Membership Products:** own list with fixed columns (Reference, Name, Tier, Company,
  Sales Price, Membership For, Tax Receipt), sorted by reference. New products default to
  a service that can be sold but not purchased.
- **Removed: Renewal Year Offset.** It only fed the disabled renewal job, which now simply
  targets next year. The column stays in the database until the module is uninstalled;
  nothing reads it.

Needs OCA `donation_base` 18.0.1.2.0 or later: older versions fail on "Send by Email"
with `Deprecated usage of 'default_res_id'` (fixed upstream in OCA commit `7f11dd6`).

## 18.0.6.0.0 — shared member numbering

**Companies now share one member number counter by default.** Member numbers are
globally unique, but every company used to count independently while the bootstrap gave
them all the same prefix `MEM/%(year)s/` — so the second association to create a member
failed with a uniqueness error. Sharing the counter makes that impossible by
construction: the per-company prefix and padding only decide how the number *looks*.

An association that continues its own numbering ticks **Own Member Number Counter**
(`res.company.member_number_own_sequence`) in Settings → Membership. Its counter starts
where the shared one stands, and is lifted to the shared value whenever the setting is
switched on, so numbers already issued are never handed out twice.

`migrations/18.0.6.0.0/post-migrate.py` raises the shared counter above every existing
per-company counter, so upgrading a database cannot re-issue numbers. It reads each
sequence's *live* value (`number_next_actual`), not the `number_next` column, which is
stale for standard sequences. The migration only ever raises, so it is re-runnable.

While the shared counter is in use, **Next Member Number** in a company's settings edits
that shared counter and therefore affects every company using it.

## 18.0.5.0.0 — periods

**Breaking: `membership.contribution` is now `membership.period`.** The model, its
table, `contribution_ids` → `period_ids`, `account.move.line.membership_contribution_id`
→ `membership_period_id`, `res.company.membership_default_contribution_year` →
`membership_default_period_year`, the menus, the reports and the German translation all
follow. `migrations/18.0.5.0.0/pre-migrate.py` renames the existing data in place; the
canonical CSV column names in the importer (`contribution_paid` and friends) keep their
names, because they describe the source data rather than Odoo.

A period now also carries **`date_start` / `date_end`**: 1 January – 31 December of its
year, clipped to the membership's own start and end date.

Invoicing:

- **An unbilled period follows the current strategy; it freezes when it is billed.**
  `membership_invoicing_strategy` used to be frozen at creation, so a period created in
  manual mode suppressed the invoice for good — activating with `draft` or `confirm`
  afterwards silently did nothing. It is now refreshed whenever the period is about to be
  invoiced, and only while it has no invoice, no refund and no recorded payment. Imported
  "paid" history is unaffected, which is what gap-doc decision 2.4 protects.
- The activation wizard **invoices an existing, never-billed period**, not only one it
  creates itself.
- **`draft` means draft.** The wizard's "Confirm Invoice" checkbox is gone: the strategy
  alone decides whether the invoice is left in draft (`draft`) or posted (`confirm`). It
  used to post the invoice in `draft` mode too.
- **"Send Invoice Email" only appears for `confirm`**, the one strategy that posts the
  invoice; a draft invoice cannot be sent.
- The activation wizard **warns when the fee is 0** and no invoice can therefore be
  created, instead of doing nothing.

Lifecycle and UI:

- The **New Membership wizard is gone.** "New" opens the membership form, which already
  offers the product domain, the member-number preview and the fee. **Activate** is now
  available straight from `draft` and passes through `waiting` itself, so onboarding is
  New → Save → Activate.
- A **cancellation reason is mandatory** in the cancel wizard. The field on the membership
  stays optional: imported historical cancellations have none.
- **No welcome email on reactivation.** It used to be pre-ticked whenever
  `date_welcome_sent` was empty, which is true for every imported member; it is now
  pre-ticked only for a genuine first activation.
- The **Members kanban shows the membership number**.
- **`2,026` is fixed.** The year fields used `options="{'format': false}"`, which Odoo 18
  ignores; the option is `enable_formatting`. The three Char shadow fields that existed
  only to work around it (`membership_year_display`, `membership_year_text`,
  `res.config.settings.membership_default_contribution_year_text`) are removed, along
  with `date_refund`.

## 18.0.4.0.0 — invoicing strategies and lifecycle

Invoicing:

- **Create Invoice** on a contribution, in every strategy. `manual` and `draft` produce a draft invoice, `confirm` a posted one. Contributions created outside the wizards are no longer stuck at "To Invoice".
- **An invoice, when there is one, always wins.** `billing_status`, `amount_invoiced` and `amount_paid` now follow the invoice in all three strategies, so registering a payment updates the contribution even in manual mode. Only contributions *without* an invoice keep the manual behaviour ("Mark as Paid", imported history).
- **Unmark as Paid** undoes "Mark as Paid"; it refuses once a tax receipt was issued. "Mark as Paid" refuses when an invoice exists — register the payment there instead.
- The activation wizard now **confirms and can send the invoice it creates itself**. Previously it resolved the invoice before creating the contribution, so a draft invoice was left unconfirmed and unsent and the activation-invoice email was unreachable.
- **Invoicing strategy per membership** (blank = company default), editable in the New Membership and activation wizards. Each contribution still freezes the strategy that applied when it was created; the field is now labelled "Applied Invoicing Strategy".
- The company default is now **manual** for new companies.

Lifecycle:

- **Reopen** on a terminated membership: the transition existed but nothing exposed it.
- A cancellation's **end date and reason can be corrected** without reactivating first ("Edit Cancellation"). Re-cancelling no longer silently dropped the new values.
- The cancel wizard lists the **unpaid contributions** of the cancellation year onward and can cancel their draft invoices and remove them. Posted invoices are never touched.
- Contributions can no longer be created on a **draft** membership, which used to lock it out of "Revert to Draft" and delete.
- The importer cancels through `action_cancel_direct` instead of writing `state` directly.
- `_schedule_termination` coerces `date_end` with `fields.Date.to_date()`. It compared a string to a date whenever the caller passed an ISO string, which every RPC caller does; only the wizard's `date` objects worked before.

Removed:

- **Auto-activate on payment** (company setting, `action_activate_from_payment`, and the hook call). Activation happens before payment; the New Membership wizard's "Activate Immediately" covers it.
- The unreachable `none` billing status, the no-op `_normalize_state_value`, and the dead `create_membership_invoice` context branch.

Other:

- The renewal wizard skips cancelled memberships whose end date has passed, reports dry runs as "would create", and reads the membership's strategy.
- "Cancellations" is split into **Scheduled to End** and **Former Members**.
- Help texts for the invoicing strategy and the renewal year offset; the activation invoice template is hidden in manual mode.

## 18.0.3.0.0 — first release

- Memberships per company with the lifecycle draft → waiting → active → cancelled → terminated, yearly contributions, and invoicing strategies manual / draft / confirm.
- Membership products: template = membership type, variant = tier (price via `price_extra`); `membership_ok` and `membership_partner_type` on the product; contributions keep their own product and amount.
- *New Membership* wizard: create, activate, create the contribution and send the welcome email in one step.
- Email recipients for organisation members per company; optional use of `partner_contact_address_default`.
- Tax receipts through OCA `donation_base`: per payment (invoice mode, once paid) and annual (also for manually paid contributions with a payment date); refunds and reversed payments flag the receipt for correction.
- German mail templates and user interface (`i18n/de.po`).
- German Zuwendungsbestätigung and Sammelbestätigung with Anlage in `association_membership_l10n_de` (18.0.2.0.0).
