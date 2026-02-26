# Membership Contract Glue (Odoo 18 CE)

## Purpose
Adds a lightweight bridge between `membership` workflows and OCA `contract`.

## Main Features
- Adds **Create Membership Contract** button in the existing Partner **Membership** tab (for Membership Administrators).
- Opens standard contract form as a dialog with defaults:
  - Customer = current partner
  - Name = `<Partner Name> - <Current User Company>`
  - Marks contract as `Membership Contract`
- Shows partner contracts in a table below the button.
- Company option to default membership contracts to:
  - yearly recurrence (`Invoice Every = 1 year`)
  - auto-create first invoice when contract is saved
  - set next invoice date to next Jan 1 after first invoice creation

## Configuration
1. Go to **Settings > Users & Companies > Companies**.
2. Open the company.
3. In section **Membership Contracts**, set **Default annual cycle for membership contracts**.

## Notes
- If first invoice is not created on save, verify at least one invoiceable contract line exists.
- Contract list is read-only; click a row to open contract form.
