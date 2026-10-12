from odoo import fields, models, tools


class EcongoodChapterMemberReport(models.Model):
    """Members per local chapter (16.6).

    One row per relation between a local chapter and a contact that is not itself an OU, in
    either orientation. An association's company partner counts as an OU even when it is not
    marked as one. Whoever has such a relation counts as a member of the chapter (D39);
    the membership itself is not looked at. Relations are recognised by the partners on both
    sides, not by the relation type's name, which differs between databases.

    The parent OU is the OU the chapter is linked to as a child (the bootstrap's "child OU"
    relation), normally its regional association; for some chapters the International
    Federation.

    Visibility follows the chapter (D40, rule in ``econgood_relationships_security.xml``): a
    chapter is visible to the companies in its ``company_ids``, which the bootstrap sets to its
    regional, national and international companies.
    """

    _name = "econgood.chapter.member.report"
    _description = "Local Chapter Members"
    _auto = False
    _order = "chapter_id, member_id"
    _rec_name = "member_id"

    chapter_id = fields.Many2one("res.partner", string="Local Chapter", readonly=True)
    parent_ou_id = fields.Many2one("res.partner", string="Parent OU", readonly=True)
    member_id = fields.Many2one("res.partner", string="Member", readonly=True)
    member_is_company = fields.Boolean(string="Organisation", readonly=True)
    relation_id = fields.Many2one("res.partner.relation", string="Relation", readonly=True)
    type_id = fields.Many2one("res.partner.relation.type", string="Relation Type", readonly=True)
    date_start = fields.Date(string="Since", readonly=True)
    date_end = fields.Date(string="Until", readonly=True)
    is_current = fields.Boolean(string="Current", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH ou AS (
                    SELECT id FROM res_partner WHERE is_econgood_ou
                    UNION
                    SELECT partner_id FROM res_company
                ),
                chapter AS (
                    SELECT p.id
                      FROM res_partner p
                      JOIN res_partner_ou_type t ON t.id = p.ou_type_id
                     WHERE t.code = 'local_chapter'
                ),
                link AS (
                    SELECT r.id, r.type_id, r.date_start, r.date_end,
                           r.right_partner_id AS chapter_id, r.left_partner_id AS member_id
                      FROM res_partner_relation r
                     WHERE r.right_partner_id IN (SELECT id FROM chapter)
                    UNION ALL
                    SELECT r.id, r.type_id, r.date_start, r.date_end,
                           r.left_partner_id AS chapter_id, r.right_partner_id AS member_id
                      FROM res_partner_relation r
                     WHERE r.left_partner_id IN (SELECT id FROM chapter)
                )
                SELECT link.id AS id,
                       link.id AS relation_id,
                       link.type_id,
                       link.chapter_id,
                       (SELECT parent.right_partner_id
                          FROM res_partner_relation parent
                         WHERE parent.left_partner_id = link.chapter_id
                           AND parent.right_partner_id IN (SELECT id FROM ou)
                         ORDER BY parent.id
                         LIMIT 1) AS parent_ou_id,
                       link.member_id,
                       COALESCE(member.is_company, FALSE) AS member_is_company,
                       link.date_start,
                       link.date_end,
                       (COALESCE(link.date_start <= CURRENT_DATE, TRUE)
                        AND COALESCE(link.date_end >= CURRENT_DATE, TRUE)) AS is_current
                  FROM link
                  JOIN res_partner member ON member.id = link.member_id
                 WHERE link.member_id NOT IN (SELECT id FROM ou)
                   AND member.active
            )
            """
        )
