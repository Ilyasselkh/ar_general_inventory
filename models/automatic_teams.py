from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class AutomaticTeam(models.Model):
    _inherit = 'araymond.inventory.team'

    automatic_slot = fields.Selection([('1', 'Équipe 1'), ('2', 'Équipe 2'), ('3', 'Équipe 3')], readonly=True, copy=False)
    user_ids = fields.Many2many(required=False)
    _automatic_slot_unique = models.Constraint('UNIQUE(session_id, automatic_slot)', 'Cette branche automatique existe déjà dans la session.')

    @api.constrains('user_ids', 'session_id', 'automatic_slot')
    def _check_members(self):
        return super(AutomaticTeam, self.filtered(lambda t: not t.automatic_slot))._check_members()


class AutomaticSession(models.Model):
    _inherit = 'araymond.maininvsession'

    def _ensure_automatic_teams(self, slots=('1', '2')):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM araymond_maininvsession WHERE id = %s FOR UPDATE', [self.id])
        team_model = self.env['araymond.inventory.team'].sudo()
        teams = team_model.search([('session_id', '=', self.id), ('automatic_slot', 'in', list(slots))])
        for slot in slots:
            if slot not in teams.mapped('automatic_slot'):
                teams |= team_model.create({'session_id': self.id, 'automatic_slot': slot, 'sequence': int(slot), 'name': 'Équipe ' + slot})
        return teams.sorted('automatic_slot')

    @api.model_create_multi
    def create(self, vals_list):
        sessions = super().create(vals_list)
        for session in sessions:
            session._ensure_automatic_teams()
        return sessions


class AutomaticZone(models.Model):
    _inherit = 'araymond.invsession'

    # Compatibility for already running, manually assigned inventories.
    automatic_teams = fields.Boolean(default=True, copy=False)
    typedegalia = fields.Selection(default='RayPro', required=True, string='Type de galia')

    @api.model_create_multi
    def create(self, vals_list):
        zones = super().create(vals_list)
        for session in zones.filtered('automatic_teams').mainsession_id:
            session._ensure_automatic_teams()
        return zones

    def _initial_galia(self):
        self.ensure_one()
        return 'RayPro' if self.mainsession_id.full_flow or self.typedegalia == 'Hybride' else self.typedegalia

    def write(self, values):
        if 'automatic_teams' in values and any(z.workflow_state != 'draft' for z in self):
            raise UserError('Le mode de répartition est verrouillé après le lancement.')
        if 'typedegalia' in values:
            self._lock_workflow()
            if any(z.workflow_state != 'draft' and not z.mainsession_id.full_flow for z in self):
                raise UserError('Le type de galia est verrouillé après le lancement des équipes.')
        result = super().write(values)
        if 'typedegalia' in values:
            for zone in self.filtered('automatic_teams'):
                counts = zone.first_count_ids
                if zone.mainsession_id.full_flow:
                    counts = zone.recs_scan.filtered(lambda count: not count.ok_done and not count.su)
                counts._set_count({'typedegalia': zone._initial_galia()})
        return result

    def action_start_teams(self):
        self.ensure_one()
        if not self.automatic_teams:
            return super().action_start_teams()
        self._lock_workflow()
        self._check_supervisor()
        if self.workflow_state != 'draft':
            raise UserError('Les équipes ont déjà été lancées.')
        if not self.checklist_validated:
            raise UserError('Le superviseur doit valider la checklist avant le scan.')
        if self.mainsession_id.state != 'Scan' or not self.statusSession:
            raise UserError('Activez la zone et passez la session principale en Scan.')
        counts = self.first_count_ids.sorted(lambda c: (c.emplacement or '', c.id))
        if self.nbrEmp < 1 or len(counts) != self.nbrEmp:
            raise ValidationError('Créez exactement le nombre d’emplacements prévu pour cette zone.')
        if any(not c.emplacement or len(c.emplacement.strip()) != 4 for c in counts):
            raise ValidationError('Chaque emplacement doit avoir un code de quatre caractères.')
        if any(c.su or c.ok_done or c.scanNbr != 1 for c in counts):
            raise UserError('Cette zone contient déjà des scans. Créez une nouvelle zone.')
        teams = self.mainsession_id._ensure_automatic_teams()
        split = (len(counts) + 1) // 2
        for index, count in enumerate(counts):
            count._set_count({'branch_id': teams[0 if index < split else 1].id, 'typedegalia': self._initial_galia()})
        self._set_workflow({'workflow_state': 'first', 'state': 'Scan'})

    def _advance_counts(self):
        self.ensure_one()
        if not self.automatic_teams:
            return super()._advance_counts()
        self._lock_workflow()
        first = self.first_count_ids
        first.invalidate_recordset()
        if self.workflow_state == 'first' and first and all(first.mapped('ok_done')):
            teams = self.mainsession_id._ensure_automatic_teams()
            for count in first:
                self._new_count(count, teams.filtered(lambda team: team != count.branch_id)[:1], 'cross')
            self._set_workflow({'workflow_state': 'cross'})
        else:
            super()._advance_counts()
        if self.workflow_state == 'arbitration':
            self._assign_automatic_arbitration()

    def _assign_automatic_arbitration(self):
        self.ensure_one()
        self._lock_workflow()
        conflicts = self.first_count_ids.filtered(lambda c: c.needs_arbitration and not c.final_count_id)
        if not conflicts:
            return
        team = self.mainsession_id._ensure_automatic_teams(('3',))
        self._set_workflow({'arbitration_team_id': team.id})
        for original in self.first_count_ids:
            if not self.recs_scan.filtered(lambda c: c.origin_count_id == original and c.count_phase == 'arbitration'):
                original._set_count({'final_count_id': False})
                self._new_count(original, team, 'arbitration')

    def action_start_arbitration(self):
        self.ensure_one()
        if not self.automatic_teams:
            return super().action_start_arbitration()
        self._check_supervisor()
        if self.workflow_state != 'arbitration':
            raise UserError('Aucun arbitrage à lancer.')
        self._assign_automatic_arbitration()

    def action_my_counts(self):
        self.ensure_one()
        if not self.automatic_teams:
            return super().action_my_counts()
        if not self.checklist_validated or self.workflow_state == 'draft':
            raise UserError('Validez la checklist et lancez les équipes.')
        return self._prepare_count_action({'type': 'ir.actions.act_window', 'name': 'Emplacements par équipe',
                'res_model': 'araymond.inventory', 'view_mode': 'list,form',
                'domain': [('rec_scan_id', '=', self.id), ('count_phase', '=', self.workflow_state), ('ok_done', '=', False)],
                'context': dict(self.env.context, group_by='branch_id')})


class AutomaticCount(models.Model):
    _inherit = 'araymond.inventory'

    full_flow_scan = fields.Boolean(related='rec_scan_id.mainsession_id.full_flow')
    automatic_zone = fields.Boolean(related='rec_scan_id.automatic_teams')
    zone_galia = fields.Selection(related='rec_scan_id.typedegalia')

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            zone = self.env['araymond.invsession'].browse(values.get('rec_scan_id'))
            if zone and zone.automatic_teams:
                values['typedegalia'] = zone._initial_galia()
                if not zone.is_session_scan:
                    values['branch_id'] = False
        return super().create(vals_list)

    def write(self, values):
        for count in self:
            if count.rec_scan_id.automatic_teams:
                if not count.rec_scan_id.is_session_scan and 'branch_id' in values and values['branch_id'] != count.branch_id.id:
                    raise UserError('Les équipes sont réparties automatiquement.')
                if 'typedegalia' in values and not count.full_flow_scan and count.rec_scan_id.typedegalia != 'Hybride' and values['typedegalia'] != count.rec_scan_id._initial_galia():
                    raise UserError('Le type de galia se définit au niveau de la zone.')
        return super().write(values)
