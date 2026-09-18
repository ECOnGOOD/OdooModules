# Changelog

## 18.0.3.0.0 — first release

- Memberships per company with the lifecycle draft → waiting → active → cancelled → terminated, yearly contributions, and invoicing strategies manual / draft / confirm.
- Membership products: template = membership type, variant = tier (price via `price_extra`); `membership_ok` and `membership_partner_type` on the product; contributions keep their own product and amount.
- *New Membership* wizard: create, activate, create the contribution and send the welcome email in one step.
- Email recipients for organisation members per company; optional use of `partner_contact_address_default`.
- Tax receipts through OCA `donation_base`: per payment (invoice mode, once paid) and annual (also for manually paid contributions with a payment date); refunds and reversed payments flag the receipt for correction.
- German mail templates and user interface (`i18n/de.po`).
- German Zuwendungsbestätigung and Sammelbestätigung with Anlage in `association_membership_l10n_de` (18.0.2.0.0).
