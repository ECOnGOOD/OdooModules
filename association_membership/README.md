# Association Membership

`association_membership` is a lean Odoo 18 CE module for association membership management in multi-company setups.

## Purpose

The module tracks one membership record per member, company, and membership product, then manages yearly contributions, renewals, and invoice linkage around that record.

## Key Features

- Memberships are modeled with `membership.membership`.
- Yearly contributions are modeled with `membership.contribution`.
- Membership types come from products in the configured Membership product category.
- Imports support idempotent upserts.
- Renewal runs are handled by a dedicated wizard and scheduled action.
- Accounting stays on standard `account.move` records.

## Company-Specific Settings

Settings are stored per company in `Settings > Association Membership`.

Each company can configure:

- the Membership product category
- auto-activate on payment
- renewal year offset
- whether scheduled renewal invoices are auto-posted

## Typical Workflow

1. Create or import a membership for a partner.
2. The membership uses a membership product and belongs to one company.
3. Create the yearly contribution for the target membership year.
4. Run renewals from the renewal wizard or cron.
5. Cancel memberships explicitly when needed.

## Invoicing Behavior

- Creating a paid contribution automatically creates a draft customer invoice unless the context disables invoice creation.
- Renewal processing groups invoices by billing contact, company, year, and currency.
- Renewal runs are atomic per group: if invoice creation fails, that group rolls back cleanly.
- Free contributions are recorded without invoice creation.

## Security And Visibility

- Manager actions, wizards, and settings are restricted to the membership manager group.
- Internal users have read access to memberships and contributions.
- Membership data is also visible on the partner form for users who can access Contacts.

## Testing

Run the module test suite with:

```bash
./run_tests.sh association_membership
```
