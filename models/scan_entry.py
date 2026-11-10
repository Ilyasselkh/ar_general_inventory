from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ScanEntrySession(models.Model):
    _inherit = 'araymond.maininvsession'

    def action_new_scan_location(self):
        self.ensure_one()
        if self.state != 'Scan' or not self.pre_scan_ready:
            raise UserError('Validez les checklists avant scan avant de créer un emplacement.')
        self.write({'manual_scan_count_ids': [(0, 0, {'scan_first': True})]})
        # Refresh the form data without replacing the current notebook.
        return False


class NewScanLocation(models.TransientModel):
    _name = 'araymond.scan.location.wizard'
    _description = 'Nouvel emplacement à scanner'

    session_id = fields.Many2one('araymond.maininvsession', required=True)
    rack = fields.Selection(selection=lambda self: self.env['araymond.inventory']._location_rack_choices(), required=True)
    team_id = fields.Many2one('araymond.inventory.team', string='Équipe de scan', required=True)
    team_ids = fields.Many2many(related='session_id.location_team_choice_ids')

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        session = self.env['araymond.maininvsession'].browse(values.get('session_id'))
        if session:
            teams = session.location_team_choice_ids.sorted('automatic_slot')
            if teams:
                values['team_id'] = teams[0].id
            racks = set(session.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan).mapped('rack'))
            if len(racks) == 1:
                values['rack'] = next(iter(racks))
        return values

    def action_create_location(self):
        self.ensure_one()
        self.session_id.check_access('write')
        self.session_id.write({'manual_scan_count_ids': [(0, 0, {
            'location_rack': self.rack, 'branch_id': self.team_id.id, 'scan_first': True})]})
        return {'type': 'ir.actions.act_window_close'}


class ScanEntry(models.Model):
    _inherit = 'araymond.inventory'

    scan_first = fields.Boolean(default=False, copy=False)
    location_scanned = fields.Boolean(default=False, copy=False)
    scan_input = fields.Char('Scanner emplacement / UM / DUM / étiquette')
    shared_scan = fields.Boolean(related='rec_scan_id.is_session_scan')

    def action_open_scan_record(self):
        self.ensure_one()
        self.check_access('read')
        if not self.ok_done and self.rec_scan_id.mainsession_id.state == 'Scan' and not self.rec_scan_id.flow_archived:
            return self.action_view_form_inv()
        return {'type': 'ir.actions.act_window', 'name': 'Scan : %s' % (self.emplacement or ''),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new',
                'views': [(self.env.ref('ar_general_inventory.scan_consultation_form').id, 'form')]}

    def _require_scan_headers(self):
        for count in self.filtered('shared_scan'):
            if not count.emplacement or (count.scan_first and not count.location_scanned):
                raise UserError('Scannez l’emplacement en premier.')
            if count.ummondatory and not count.um:
                raise UserError('Scannez l’UM avant les étiquettes.')
            if count.dummondatory and not count.dum:
                raise UserError('Scannez le DUM avant les étiquettes.')

    @api.onchange('scan_input')
    def _onchange_scan_input(self):
        barcode = (self.scan_input or '').strip()
        self.scan_input = False
        if not barcode or not self._origin.id:
            return
        count = self._origin
        count._check_scan()
        if not count.emplacement or (count.scan_first and not count.location_scanned) or len(barcode) == 4:
            if len(barcode) != 4:
                return {'warning': {'title': 'Emplacement attendu', 'message': 'Scannez le code emplacement de quatre caractères en premier.'}}
            if count.emplacement and barcode.upper() != count.emplacement:
                return {'warning': {'title': 'Emplacement incorrect', 'message': 'Scannez l’emplacement affecté à cette mission.'}}
            count.write({'emplacement': barcode.upper(), 'location_scanned': True})
            self.emplacement = count.emplacement
            self.location_scanned = True
            return
        if len(barcode) == 10 and barcode.startswith('600') and barcode.isdigit():
            count.write({'um': ','.join(filter(None, [count.um, barcode]))})
            self.um = count.um
            return
        if len(barcode) == 17 and barcode.startswith(('3', '4')):
            count.write({'dum': barcode})
            self.dum = count.dum
            return
        try:
            count._require_scan_headers()
        except UserError as error:
            return {'warning': {'title': 'Scan obligatoire', 'message': str(error)}}
        self.um, self.dum = count.um, count.dum
        if self.typedegalia == 'RayPro':
            self.scanraypro = barcode
            return self.onchange_scan()
        if self.typedegalia == 'logistique':
            self.scanlog = barcode
            return self.onchange_scanlog()
        if self.typedegalia == 'ReceptionSAP':
            self.scanrecp = barcode
            return self.onchange_scanrecp()
        return {'warning': {'title': 'Mode manuel', 'message': 'Renseignez les données de l’étiquette puis cliquez sur Enregistrer.'}}

    def action_manuel_save(self):
        self._require_scan_headers()
        return super().action_manuel_save()

    def action_close(self):
        self._require_scan_headers()
        return super().action_close()


class ScanEntryLabel(models.Model):
    _inherit = 'araymond.etiquette'

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self.env['araymond.inventory'].browse(values.get('su_id'))._require_scan_headers()
        return super().create(vals_list)
