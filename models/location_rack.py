"""Rack-specific requirements for locations in the shared scan pool."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError, UserError
from .complete_flow import trusted


class RackRequirements(models.Model):
    _inherit = 'araymond.invsession'

    @api.constrains('rack', 'ummondatory', 'dummondatory', 'statusSession', 'mainsession_id')
    def _check_rack_requirements(self):
        for zone in self.filtered(lambda z: z.statusSession and not z.is_session_scan and z.mainsession_id.full_flow):
            siblings = zone.mainsession_id.sess_records.filtered(
                lambda z: z.statusSession and not z.is_session_scan and z.rack == zone.rack)
            if len({(z.ummondatory, z.dummondatory) for z in siblings}) > 1:
                raise ValidationError('Harmonisez les options UM/DUM de toutes les lignes du rack %s.' % zone.rack)


class LocationRack(models.Model):
    _inherit = 'araymond.inventory'

    @api.model
    def _location_rack_choices(self):
        choices = self.env['araymond.invsession']._fields['rack']._description_selection(self.env)
        session = self.env['araymond.maininvsession'].browse(self.env.context.get('manual_location_session_id'))
        if session:
            racks = session.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan).mapped('rack')
            choices = [(key, label) for key, label in choices if key in racks]
        return choices

    location_rack = fields.Selection(selection='_location_rack_choices', string='Rack')

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        session = self.env['araymond.maininvsession'].browse(self.env.context.get('manual_location_session_id'))
        if session and 'location_rack' in names:
            racks = set(session.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan).mapped('rack'))
            if len(racks) == 1:
                values['location_rack'] = next(iter(racks))
        return values

    def _rack_zones(self):
        self.ensure_one()
        return self.env['araymond.invsession'].search([
            ('mainsession_id', '=', self.rec_scan_id.mainsession_id.id),
            ('scan_cycle', '=', self.rec_scan_id.scan_cycle),
            ('is_session_scan', '=', False), ('rack', '=', self.location_rack),
            '|', ('statusSession', '=', True), ('flow_archived', '=', True)])

    @api.depends('rec_scan_id.ummondatory', 'location_rack',
                 'rec_scan_id.mainsession_id.sess_records.ummondatory')
    def get_um_data(self):
        super().get_um_data()
        for count in self.filtered(lambda c: c.rec_scan_id.is_session_scan):
            count.ummondatory = any(count._rack_zones().mapped('ummondatory'))

    @api.depends('rec_scan_id.dummondatory', 'location_rack',
                 'rec_scan_id.mainsession_id.sess_records.dummondatory')
    def get_dum_data(self):
        super().get_dum_data()
        for count in self.filtered(lambda c: c.rec_scan_id.is_session_scan):
            count.dummondatory = any(count._rack_zones().mapped('dummondatory'))

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            zone = self.env['araymond.invsession'].browse(values.get('rec_scan_id'))
            if zone.is_session_scan and not values.get('location_rack'):
                origin = self.browse(values.get('origin_count_id'))
                racks = set(zone.mainsession_id.sess_records.filtered(
                    lambda z: z.statusSession and not z.is_session_scan).mapped('rack'))
                values['location_rack'] = origin.location_rack or (next(iter(racks)) if len(racks) == 1 else False)
                if not values['location_rack'] and not trusted(self) and not (values.get('scan_first') and not values.get('emplacement')):
                    raise ValidationError('Sélectionnez le rack de cet emplacement.')
        return super().create(vals_list)

    @api.constrains('location_rack', 'rec_scan_id')
    def _check_location_rack(self):
        for count in self.filtered(lambda c: c.rec_scan_id.is_session_scan and c.location_rack):
            zones = count._rack_zones()
            if not zones:
                raise ValidationError('Choisissez un rack configuré dans cette session.')
            if len({(z.ummondatory, z.dummondatory) for z in zones}) > 1:
                raise ValidationError('Harmonisez les options UM/DUM de ce rack avant de scanner.')

    def write(self, values):
        if 'location_rack' in values and not trusted(self):
            for count in self:
                if values['location_rack'] != count.location_rack and (count.location_scanned or count.su or count.um or count.dum or count.ok_done or count.count_phase != 'first'):
                    raise UserError('Le rack est verrouillé après le début du scan.')
                if count.rec_scan_id.is_session_scan and not values['location_rack']:
                    raise ValidationError('Le rack est obligatoire.')
            if all(c.rec_scan_id.is_session_scan and not c.location_rack for c in self):
                self.check_access('write')
                for count in self:
                    if count.rec_scan_id.flow_archived or not count.rec_scan_id.statusSession:
                        raise UserError('Ce passage est archivé.')
                    count._check_initial_team(count.rec_scan_id, count.branch_id)
                self._set_count({'location_rack': values['location_rack']})
                values = {k: v for k, v in values.items() if k != 'location_rack'}
        return super().write(values)

    def _check_scan(self):
        super()._check_scan()
        for count in self.filtered(lambda c: c.rec_scan_id.is_session_scan):
            if not count.location_rack:
                raise UserError('Sélectionnez le rack de cet emplacement avant de scanner.')
            count._check_location_rack()
