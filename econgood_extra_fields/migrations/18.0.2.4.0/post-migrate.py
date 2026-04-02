def migrate(cr, version):
    cr.execute(
        """
        UPDATE res_partner
           SET x_nonprofit_status = CASE
               WHEN COALESCE(x_is_nonprofit, FALSE) THEN 'confirmed'
               WHEN x_nonprofit_status IS NULL OR x_nonprofit_status = '' THEN 'unknown'
               ELSE x_nonprofit_status
           END
         WHERE COALESCE(x_is_nonprofit, FALSE)
            OR x_nonprofit_status IS NULL
            OR x_nonprofit_status = ''
        """
    )
