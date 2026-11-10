from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['araymond.maininvsession'].search([
        ('full_flow', '=', True), ('state', '!=', 'Fermeture'),
    ])._sync_go_roles()
