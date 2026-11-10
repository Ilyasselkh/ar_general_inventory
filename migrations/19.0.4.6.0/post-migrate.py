from odoo import api, SUPERUSER_ID
from odoo.addons.ar_general_inventory.models.complete_flow import internal


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env['araymond.flow.line'].search([('is_lx03', '=', True), ('session_id.full_flow', '=', True)])
    for session in lines.session_id:
        # Prefer the file already imported; preserve all original attachments.
        if not session.lx03_file:
            attachments = lines.filtered(lambda line: line.session_id == session).attachment_ids
            excel = attachments.filtered(lambda item: item.name.lower().endswith('.xlsx'))
            if len(excel) == 1:
                internal(session).write({'lx03_file': excel.datas, 'lx03_filename': excel.name})
    internal(lines).write({'active': False})
