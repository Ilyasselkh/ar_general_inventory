from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for session in env['araymond.maininvsession'].search([
        ('full_flow', '=', True), ('state', '!=', 'Fermeture'),
    ]):
        session._make_checklists()
