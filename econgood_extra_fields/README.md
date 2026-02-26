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
  - `x_is_nonprofit`
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

## Dependencies

- `base`
- `contacts`
- `partner_company_type`
- `partner_contact_gender`
- `partner_contact_birthdate`