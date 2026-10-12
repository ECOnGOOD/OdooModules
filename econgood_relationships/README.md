# ECOnGOOD Relationships

ECOnGOOD specifics of partner relations (OCA `partner_multi_relation`).

## Local Chapter Members (Membership > Reporting)

A read-only report (pivot and list) of who belongs to which local chapter (16.6):

- **Rows:** one per relation between a local chapter and a contact that is not itself an OU,
  in either orientation. A local chapter is a contact with the OU type `local_chapter`
  (`econgood_extra_fields`). OUs are contacts marked `is_econgood_ou` and every company's own
  partner.
- **Member (D39):** whoever has such a relation that is open today (filter *Current*, on by
  default). The membership itself is not looked at; `association_membership` is a dependency
  only for the menu, which sits with the other membership reports (membership managers).
- **Parent OU:** the OU the chapter is linked to as a child (the bootstrap's "child OU"
  relation), normally its regional association.
- **Visibility (D40):** a row is visible where its chapter is, through the chapter's
  `company_ids`: a regional company sees its own chapters, a national company all chapters of
  its regions. A chapter without companies is visible to all.

Relations are recognised by the partners on both sides, not by the relation type's name.
