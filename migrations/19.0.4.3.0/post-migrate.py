from odoo import api, SUPERUSER_ID
from odoo.addons.ar_general_inventory.models.complete_flow import internal


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("UPDATE araymond_maininvsession SET state = 'Scan' WHERE full_flow AND state = 'PreScan'")
    for session in env['araymond.maininvsession'].search([('full_flow', '=', True), ('state', '!=', 'Fermeture')]):
        old_checks = env['araymond.flow.checklist'].search([('session_id', '=', session.id), ('phase', 'in', ['pre', 'post']), ('zone_id', '=', False)])
        for old in old_checks:
            for zone in session.sess_records.filtered('statusSession'):
                if env['araymond.flow.checklist'].search_count([('session_id', '=', session.id), ('phase', '=', old.phase), ('zone_id', '=', zone.id)]):
                    continue
                check = internal(env['araymond.flow.checklist']).create({'session_id': session.id, 'phase': old.phase, 'zone_id': zone.id})
                for line in old.with_context(active_test=False).line_ids:
                    internal(line).copy({'checklist_id': check.id, 'active': line.active})
        session._make_checklists()
