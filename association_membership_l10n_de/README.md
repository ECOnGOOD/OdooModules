# Association Membership — Germany

Adds a legally-formatted German `Zuwendungsbestätigung` PDF report on top of OCA `donation.tax.receipt`. Designed for German `e.V.` associations issuing tax-deductible receipts for members and donors.

## What it does

- Adds a "Zuwendungsbestätigung" report action on `donation.tax.receipt` (alongside OCA's generic receipt). Operators select receipts in the list view and print via the report menu.
- Renders the BMF-compliant German layout: issuer, donor, amount in numbers + words, day of donation, waiver flag, charitable purpose, Finanzamt and tax-number reference, signature block.
- Adds per-company configuration in `Settings > Association Membership > Zuwendungsbestätigung (DE)`:
  - Finanzamt name
  - Steuernummer
  - Date and assessment period of the latest Freistellungsbescheid
  - Charitable purpose (Förderungszweck)
  - Signatory name and role
  - "Receipts cover Mitgliedsbeiträge" toggle (controls the §10b clause text)
- Adds two fields on `donation.tax.receipt`:
  - `de_verzicht_aufwendungen` — checkbox shown on the receipt for "Verzicht auf Erstattung von Aufwendungen"
  - `de_amount_in_words` — computed from `amount` via `num2words` (Python dependency)

## Dependencies

- `association_membership` (base module)
- `donation_base` (OCA — provides the receipt model)
- `num2words` (PyPI — for amount-in-words rendering; without it the field stays empty)

## Notes and limitations

- The QWeb template is editable. Per-tenant adjustments (e.g. logo placement, exact §-references) are done by overriding the template in a downstream l10n module.
- The Mitgliedsbeitrag § 10b clause is included or omitted based on the per-company toggle. If your tenant's Satzung covers activities listed in § 10b Abs. 1 Satz 8 EStG (Sport, Heimatpflege, etc.), Mitgliedsbeiträge are NOT receipt-eligible — disable the toggle and only one-off donations will print.
- Receipt validity ultimately depends on the Finanzamt-issued Freistellungsbescheid being current (it must cover the donation date's tax year). The module surfaces this as configuration but does not enforce it.
- For other jurisdictions, mirror this module structure: `association_membership_l10n_<cc>`.
