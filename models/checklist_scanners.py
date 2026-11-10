from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .complete_flow import internal, trusted, audit, snapshot

SCANNER_FIELDS = {'scanner1_ids', 'scanner2_ids', 'scanner3_ids'}


class ChecklistScanners(models.Model):
    _inherit = 'araymond.flow.checklist'

    scanner1_ids = fields.Many2many('res.users', 'ar_check_scanner1_rel', string='Équipe 1')
    scanner2_ids = fields.Many2many('res.users', 'ar_check_scanner2_rel', string='Équipe 2')
    scanner3_ids = fields.Many2many('res.users', 'ar_check_scanner3_rel', string='Équipe 3 — arbitrage')
    available_scanner1_ids = fields.Many2many('res.users', compute='_compute_available_scanners')
    available_scanner2_ids = fields.Many2many('res.users', compute='_compute_available_scanners')
    available_scanner3_ids = fields.Many2many('res.users', compute='_compute_available_scanners')
    scanners_locked = fields.Boolean(compute='_compute_available_scanners')

    @api.depends('session_id.assignment_ids.user_id', 'session_id.assignment_ids.role',
                 'session_id.assignment_ids.shift', 'validation_votes', 'validated_at', 'session_id.state')
    def _compute_available_scanners(self):
        for check in self:
            for slot in ('1', '2', '3'):
                check['available_scanner%s_ids' % slot] = self.env['res.users'].search([('share', '=', False)])
            check.scanners_locked = bool(check.validation_votes or check.validated_at or check.session_id.state != 'Scan')

    def _check_scanner_selection(self, required=False):
        for check in self.filtered(lambda c: c.phase == 'pre'):
            for slot in ('1', '2', '3'):
                users = check['scanner%s_ids' % slot]
                if required and not users:
                    raise ValidationError('Sélectionnez au moins une personne dans chacune des équipes 1, 2 et 3 avant de valider la checklist.')
                if any(user.share or not user.active for user in users):
                    raise ValidationError('Choisissez des utilisateurs internes actifs pour les équipes de scan.')

    def write(self, values):
        if not (set(values) & SCANNER_FIELDS) or trusted(self):
            return super().write(values)
        self.check_access('write')
        before = {}
        for check in self:
            check.session_id._lock_preparation()
            check._check_stage()
            if check.phase != 'pre' or check.validation_votes or check.validated_at:
                raise UserError('Sélectionnez les scanneurs avant la première signature de la checklist avant scan. Utilisez Modifier pour recommencer.')
            before[check.id] = snapshot(check, SCANNER_FIELDS)
        other = {k: v for k, v in values.items() if k not in SCANNER_FIELDS}
        if other:
            super().write(other)
        result = super(ChecklistScanners, internal(self)).write({k: v for k, v in values.items() if k in SCANNER_FIELDS})
        self._check_scanner_selection()
        for check in self:
            audit(check, check.session_id, before[check.id], snapshot(check, SCANNER_FIELDS), check.phase, 'Sélection des scanneurs')
        return result

    def action_validate(self):
        self.ensure_one()
        self.session_id._lock_preparation()
        if self.phase == 'pre':
            self._check_scanner_selection(required=True)
        return super().action_validate()


class SelectedScanCount(models.Model):
    _inherit = 'araymond.inventory'

    def _check_scan(self):
        for count in self:
            session = count.rec_scan_id.mainsession_id
            if not session.full_flow:
                continue
            checks = session.flow_check_ids.filtered(lambda c: c.phase == 'pre')
            if not count.rec_scan_id.is_session_scan:
                checks = checks.filtered(lambda c: c.zone_id == count.rec_scan_id)
            slot = count.branch_id.automatic_slot
            if slot not in ('1', '2', '3') or self.env.user not in checks.mapped('scanner%s_ids' % slot):
                raise AccessError('Vous devez être sélectionné dans cette équipe sur une checklist avant scan concernée.')
        return super()._check_scan()

    def _check_initial_team(self, zone, team):
        result = super()._check_initial_team(zone, team)
        if not trusted(self) and zone.is_session_scan:
            checks = zone.mainsession_id.flow_check_ids.filtered(lambda c: c.phase == 'pre')
            if team.automatic_slot not in ('1', '2') or self.env.user not in checks.mapped('scanner%s_ids' % team.automatic_slot):
                raise AccessError('Cette personne doit être sélectionnée dans son équipe sur une checklist avant scan.')
        return result


class ChecklistScanSession(models.Model):
    _inherit = 'araymond.maininvsession'

    def _scan_assigned_users(self, slot):
        users = super()._scan_assigned_users(slot)
        if self.full_flow and str(slot) in ('1', '2', '3'):
            checks = self.flow_check_ids.filtered(lambda check: check.phase == 'pre' and check.zone_id.statusSession)
            users |= checks.mapped('scanner%s_ids' % slot).filtered(lambda user: user.active and not user.share)
        return users
