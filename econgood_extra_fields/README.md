# ECOnGOOD Extra Fields

Adds ECOnGOOD-specific partner fields to `res.partner` and extends the contact form.

## What this module adds

- General fields:
  - `x_letter_salutation`
  - `x_socials`
- Demographics fields:
  - `x_employee_count`
  - `x_inhabitant_count`
- Organization/legal fields:
  - `x_organization_kind_id`
  - `x_ou_type_id`
  - `x_nonprofit_status`
  - `x_code_of_conduct_signed_date`
  - `x_privacy_agreement_signed_date`
  - `x_email_econgood`
- Legacy/integration fields:
  - `x_legacy_id_smartwe`
  - `x_legacy_id_formidable`

## Validation

- Employee and inhabitant counts must be non-negative.
- Signed dates cannot be in the future.
- ECOnGOOD email must be a valid email format.
- Organization Kind and OU Type are only allowed on company contacts.

## Seeded partner vocabularies

- Organization Kind:
  - Company
  - Organization
  - Municipality / Public Body
  - Other Organization
- OU Type:
  - National Association
  - Regional Association
  - Local Chapter
  - Hub
  - Other

## Dependencies

- `base`
- `contacts`
- `partner_company_type`
- `partner_contact_gender`
- `partner_contact_birthdate`
