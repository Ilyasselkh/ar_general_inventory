from odoo import api, SUPERUSER_ID, fields
from odoo.addons.ar_general_inventory.models.complete_flow import internal


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("UPDATE araymond_maininvsession SET state = 'Preparation' WHERE full_flow AND state = 'Risk'")
    for check in env['araymond.flow.checklist'].search([]):
        config = env['araymond.flow.configuration'].search([('phase', '=', check.phase)], limit=1)
        users = config.responsible_ids
        votes = {}
        if check.validated_by and check.validated_at:
            votes[str(check.validated_by.id)] = fields.Datetime.to_string(check.validated_at)
        values = {'validator_ids': [(6, 0, users.ids)]}
        if check.session_id.state != 'Fermeture' and (not users or not all(str(user.id) in votes for user in users)):
            values.update(validated_by=False, validated_at=False)
        internal(check).write(values)
        internal(check).write({'validation_votes': votes})
