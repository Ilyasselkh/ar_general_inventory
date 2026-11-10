from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    cr.execute('''
        UPDATE araymond_invsession z SET automatic_teams = FALSE
        WHERE z.workflow_state != 'draft' AND EXISTS (
            SELECT 1 FROM araymond_inventory c
            JOIN araymond_inventory_team t ON t.id = c.branch_id
            WHERE c.rec_scan_id = z.id AND t.automatic_slot IS NULL
        )
    ''')
    env = api.Environment(cr, SUPERUSER_ID, {})
    for session in env['araymond.maininvsession'].search([('state', '!=', 'Fermeture')]):
        session._ensure_automatic_teams()
