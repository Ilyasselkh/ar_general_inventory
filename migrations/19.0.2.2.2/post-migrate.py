from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sessions = env['araymond.maininvsession'].search([
        '|', ('typesession', '=', False), ('typesession', '!=', 'InvGen'),
    ])
    sessions.write({'typesession': 'InvGen'})
