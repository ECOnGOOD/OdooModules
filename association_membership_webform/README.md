# Association Membership — Webform Intake

Receives membership signups from the ECOnGOOD WordPress form (Formidable) and turns them into
a `res.partner` plus a `membership.membership` in state `waiting`, ready for staff to activate.

One endpoint, one shared secret, no public read surface. `association_membership` itself stays
free of any HTTP surface; installing this module is the deliberate act that opens one.

## The endpoint

```
POST /membership/webform/submit
Content-Type: application/json
X-Webform-Token: <shared secret>
```

The token is compared in constant time against the system parameter
`association_membership_webform.token`. **An unset or empty parameter refuses every request**,
so the endpoint is closed until it is deliberately configured. The token travels in a header,
so the endpoint must be served over HTTPS.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | `{"partner_id", "membership_id", "membership_state", "company_id", "product_id", "amount", "warnings"}` | Accepted |
| `401` | `{"error": "unauthorized"}` | Bad or missing token, or no token configured |
| `400` | `{"error": "invalid_json" \| "invalid_payload"}` | Body is not a JSON object |
| `413` | `{"error": "payload_too_large"}` | Body above 256 KiB |
| `422` | `{"error": "<code>", "message": "..."}` | Well-formed, but not mappable |
| `500` | `{"error": "internal_error" \| "intake_user_misconfigured"}` | Bug, or a bad `user_id` parameter |

`422` codes: `company_unresolved`, `product_unresolved`, `rejected_by_odoo` (a model constraint
refused it; the message carries Odoo's own text).

Warnings on a `200` (the submission was accepted, but something needs a human):
`tier_clamped`, `employee_count_missing`, `product_ref_unknown`, `payment_frequency_not_yearly`,
`contact_person_not_linked`, `payment_mode_unavailable`, `mandate_module_missing`,
`mandate_without_iban`.

## Why it does not simply run as Public

`sudo()` only flips the superuser flag; it keeps the uid, which on an `auth="public"` route is
the Public user. Odoo deliberately runs computed fields declared without `compute_sudo` as the
*real* user (`Field.compute_value` calls `records.sudo(False)`), and with OCA
`base_multi_company` installed `res.partner.company_id` is exactly such a field — so writing a
partner as Public raises an AccessError from inside the compute, however much you sudo around
it. The controller therefore rebinds the request to a real internal user before doing any work,
configurable through `association_membership_webform.user_id`.

## What a submission produces

- **Member** — an individual, company or association partner, per the form's "I am a" block.
  Consent dates, employee count and the Formidable entry id land on the
  `econgood_extra_fields` partner fields.
- **Contact person** — a child partner for organisations. When the optional OCA
  `partner_contact_address_default` is installed, it also becomes `partner_contact_id`, which is
  what `membership._get_communication_partners()` resolves to — so member emails reach the named
  person rather than the general head-office address.
- **Membership** — in `waiting`, via `action_submit()`. **No period is created**: periods belong
  to activation, which stays a staff action.
- **Bank account and payment mode** — IBAN/BIC as a `res.partner.bank`; `Überweisung` /
  `Lastschrift` and a SEPA mandate when the optional OCA modules are installed.
- **Chatter** — the donation amount, service opt-ins, note, local chapter and every intake
  warning, so nothing submitted is silently dropped.

## How the product is chosen

The CRM's own type label is a last resort. The association, the organisation type and the
employee count are enough to infer the product structurally:

1. `membership_product_ref` → `product.product.default_code` (`MEM_DE_COMP_101-200`), when the
   form sends it.
2. **Inference**: type code (`IND` / `COMP` / `ORG` / `MUN`) from the form block and the
   charitable flag → `product.product._membership_product_domain(company, partner)`, which
   enforces `membership_ok`, company visibility and partner type → variant by employee count.
3. Membership type label, `ilike` against the template name.

**Tier gaps clamp rather than fail.** Tier ranges are data, and data can have gaps. A count that
no range covers takes the nearest tier, reports `tier_clamped` and says so in the chatter, rather
than losing the signup. Clamping upward bills at the next published tier, so a clamped signup is
a pricing decision someone should confirm — which is why it is a warning and not silent.

## Idempotency

There is no staging model: WordPress is the staging area, since every Formidable entry is stored
there permanently and the hook records Odoo's response on the entry. Re-posting is therefore
normal and must be safe:

- **Partner** — matched on `legacy_id_formidable`, then email **and** normalized name. An
  email-only match is deliberately refused; shared family and office addresses make email alone
  unsafe as an identity.
- **Membership** — an open membership for the same (partner, company, product template) is
  updated, not duplicated.

The whole mapping runs in one savepoint, so a failure half way through leaves no partner behind.

## Security

The token is the only thing standing between the internet and partner creation, so treat it as
a credential with write access:

- **Serve the endpoint over HTTPS.** The token travels in a header; over plain HTTP it is
  readable in transit. The module cannot enforce this.
- **Give it a dedicated internal user** through `association_membership_webform.user_id`. The
  default is the administrator, which is far more than the intake needs, and a dedicated account
  makes every record the endpoint writes attributable.
- **Rate-limit at the reverse proxy.** Odoo has no throttle of its own. Every refused token is
  logged with its source address, so a rate limiter or fail2ban has something to act on. Behind
  a proxy, run Odoo in proxy mode so that address is the client rather than the proxy.
- **Rotate the token** by changing the system parameter and the caller's copy together; an
  empty parameter closes the endpoint rather than opening it.

What a token holder can do, by design: create and update partners, bank accounts, mandates and
memberships in `waiting`. It cannot read anything back — the endpoint has no read surface — but
a `422` returns Odoo's own constraint message, which can name a record. Anyone who can read the
caller's stored responses can therefore see that much.

Bodies above 256 KiB are refused — a backstop on what reaches the database, not a shield for the
server, since Odoo reads the request before the handler runs. Put a body-size limit on the proxy
as well. Submitted values are escaped before they are used in `ilike` patterns and before they
reach the chatter, so neither a `%` nor a `<script>` in a form field changes what the endpoint
matches or renders.

## Setup

1. Install the module (from the command line for a new module, not through the running server).
2. Set `association_membership_webform.token` to a long random string.
3. Set `association_membership_webform.user_id` to a dedicated internal user.
4. Put the token in the caller's configuration — for the WordPress bridge, in `wp-config.php`
   as `ECONGOOD_ODOO_TOKEN` — and activate it.

The full payload contract, the WordPress checklist and the deployment hardening notes live with
the integration documentation in the deployment repository.

## Testing

```bash
./run_tests.sh association_membership_webform
```
