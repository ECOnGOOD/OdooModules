def _assign_default_membership_templates(env):
    """Assign the shipped default mail templates to companies that have none.

    Runs on install and upgrade. Companies that already picked a template
    are left untouched, so custom choices are never overwritten.
    """
    assignments = (
        (
            "membership_activation_invoice_template_id",
            "association_membership.mail_template_membership_activation_invoice",
        ),
        (
            "membership_welcome_template_id",
            "association_membership.mail_template_membership_welcome",
        ),
        (
            "membership_cancellation_template_id",
            "association_membership.mail_template_membership_cancellation",
        ),
    )
    for company in env["res.company"].search([]):
        for field_name, xmlid in assignments:
            if company[field_name]:
                continue
            template = env.ref(xmlid, raise_if_not_found=False)
            if template:
                company[field_name] = template.id


def post_init_hook(env):
    _assign_default_membership_templates(env)