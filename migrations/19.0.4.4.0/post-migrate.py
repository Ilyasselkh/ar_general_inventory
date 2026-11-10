from odoo import api, SUPERUSER_ID
from odoo.addons.ar_general_inventory.models.complete_flow import internal


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    assignments = internal(env['araymond.flow.assignment'])
    configs = env['araymond.flow.team.config'].search([])
    for session in env['araymond.maininvsession'].search([('full_flow', '=', True)]):
        sources = [('entry', session.entry_user_ids, session.entry_shift),
                   ('control', session.control_user_ids, session.control_shift)]
        teams = session._ensure_automatic_teams(('1', '2', '3'))
        for team in teams:
            members = team.user_ids if session.state == 'Fermeture' else configs.filtered(lambda c: c.slot == team.automatic_slot).user_ids
            sources.append(('scan' + team.automatic_slot, members, False))
        for role, users, shift in sources:
            for user in users:
                if not assignments.search_count([('session_id', '=', session.id), ('role', '=', role), ('user_id', '=', user.id)]):
                    assignments.create({'session_id': session.id, 'role': role, 'user_id': user.id, 'shift': shift})
