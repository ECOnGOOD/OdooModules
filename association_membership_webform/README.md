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

`422` codes: `company_unresolved`, `product_unresolved`, `duplicate_signup` (see below),
`rejected_by_odoo` (a model constraint refused it; the message carries Odoo's own text).

Warnings on a `200` (the submission was accepted, but something needs a human):
`tier_clamped`, `employee_count_missing`, `product_ref_unknown`, `fee_mismatch`,
`payment_frequency_not_yearly`, `contact_person_not_linked`, `payment_mode_unavailable`,
`mandate_module_missing`, `mandate_without_iban`, `regional_association_unresolved`,
`regional_association_mismatch`, `regional_product_missing`, `regional_product_not_free`,
`regional_membership_failed`, `chapter_unresolved`, `relation_module_missing`,
`chapter_relation_type_missing`.

Every one of them is a configuration gap rather than a bad submission; what each needs is in the
deployment repository's per-company prerequisites note.

## Why it does not simply run as Public

`sudo()` flips the superuser flag but keeps the uid, and the uid is what lands in `create_uid` /
`write_uid` and authors every chatter message. On an `auth="public"` route that uid is the
Public user, so every member the form creates would be filed as having been created by "Public
user" — worthless in an audit and indistinguishable from a genuine portal action. The controller
therefore rebinds the request to a real internal user before doing any work, configurable
through `association_membership_webform.user_id`.

This is **not** an access control. The mapping runs under `sudo()`, so rights and record rules
are bypassed whatever the uid is. Rebinding buys attribution, and one place to tighten if the
`sudo()` is ever narrowed.

## The intake user

| | |
| --- | --- |
| **Required** | An **active** user that is **not a share user** — i.e. a member of `base.group_user` (Internal User), since `res.users.share` is exactly "not in that group". Anything else is refused with `500 intake_user_misconfigured` |
| **Not required** | Any further group, and access to the member's company. The mapping runs under `sudo()`, so ACLs and record rules do not apply to it |
| **Recommended** | `association_membership.group_membership_manager`, and the companies that can receive signups in *Allowed Companies* — so the account still works if the `sudo()` is ever narrowed, and so a human opening it sees what it has been doing |
| **Keep off** | Settings / Administration. The account never logs in, so give it no password and no API key |

Archiving the user closes the endpoint (`500`), which is a blunt but effective off switch.

## What a submission produces

- **Member** — an individual, company or association partner, per the form's "I am a" block.
  Consent dates, employee count, the stated payment method and the Formidable entry id land on
  the `econgood_extra_fields` partner fields. The member is **scoped to the associations they
  joined** rather than left visible to the whole federation.
- **Contact person** — a child partner for organisations. When the optional OCA
  `partner_contact_address_default` is installed, it also becomes `partner_contact_id`, which is
  what `membership._get_communication_partners()` resolves to — so member emails reach the named
  person rather than the general head-office address.
- **Invoice contact** — a child partner of type `invoice` when the form sends `invoice_email`.
  That is what `membership.invoice_partner_id` resolves to: the module's default reads
  `address_get(["invoice"])` at create time, so the child only has to exist first.
- **Membership** — in `waiting`, via `action_submit()`. **No period is created**: periods belong
  to activation, which stays a staff action.
- **A second, regional membership** — when the form names a regional association, on that
  association's own (normally free) product. The two live in different companies, which is what
  `_check_date_overlap` allows, and each gets its own member number.
- **Local chapter relation** — `res.partner.relation` of type `Local Chapter`. The "Member Of"
  relation to the association is deliberately *not* written here: `association_membership` owns
  it and creates it on activation, because a signup in `waiting` is not yet a member.
- **Bank account and payment mode** — IBAN/BIC as a `res.partner.bank`; `Überweisung` /
  `Lastschrift` and a SEPA mandate when the optional OCA modules are installed.
- **Chatter** — the donation amount, service opt-ins, note, local chapter and every intake
  warning, so nothing submitted is silently dropped.

## Duplicates and replays

Re-posting the **same** `entry_id` is a replay: it updates in place, and the intake note is not
duplicated. A **different** `entry_id` that resolves to someone who already has an open
membership with that association is a second signup: it is refused with `422 duplicate_signup`,
nothing is written, and a short note is left on the existing membership. Send `force_duplicate`
to override when the second membership is genuine.

The note is posted *after* the transaction rolls back, not inside it — a note written inside
would be discarded along with everything else.

## How the product is chosen

The CRM's own type label is a last resort. The association, the organisation type and the
employee count are enough to infer the product structurally:

1. `membership_product_ref` → `product.product.default_code` (`MEM_DE_COMP_101-200`), when the
   form sends it.
2. **Inference**: type code (`IND` / `COMP` / `ORG` / `MUN`) from the form block and the
   charitable flag → `product.product._membership_product_domain(company, partner)`, which
   enforces `membership_ok`, company visibility and partner type → variant by employee count.
3. Membership type label, `ilike` against the template name.

The submitted fee (`fee_normal`) only breaks a tie when several types remain, and is then
cross-checked against the chosen product's price — a disagreement is reported as `fee_mismatch`
and named in the chatter. Price comes from another system, so it never gets to overrule the
structural inference.

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
The single exception is the regional membership, which has a savepoint of its own: that record is
normally free, and losing a paid national signup because it could not be created would be the
worse failure. A problem there degrades to a warning.

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
