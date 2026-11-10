"""Session-wide locations, independent of configured rack levels."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .complete_flow import internal, trusted, FullZone


class SessionLocations(models.Model):
    _inherit = 'araymond.maininvsession'

    pre_scan_ready = fields.Boolean(compute='_compute_pre_scan_ready')
    configured_rack_ids = fields.One2many(
        'araymond.invsession', 'mainsession_id', string='Racks configurés', copy=False,
        domain=[('is_session_scan', '=', False), ('flow_archived', '=', False)])

    def _location_creation_teams(self):
        self.ensure_one()
        checks = self.flow_check_ids.filtered(lambda c: c.phase == 'pre' and c.zone_id.statusSession)
        return self.team_ids.filtered(lambda t: t.automatic_slot in ('1', '2')
            and self.env.user in (checks.mapped('scanner%s_ids' % t.automatic_slot)
                                 if self.full_flow else self._scan_assigned_users(t.automatic_slot)))
    location_team_choice_ids = fields.Many2many('araymond.inventory.team', compute='_compute_location_team_choices')
    show_location_team_choice = fields.Boolean(compute='_compute_location_team_choices')

    @api.depends_context('uid')
    @api.depends('team_ids', 'assignment_ids', 'assignment_ids.user_id', 'assignment_ids.role',
                 'assignment_ids.shift', 'flow_check_ids.scanner1_ids', 'flow_check_ids.scanner2_ids')
    def _compute_location_team_choices(self):
        for session in self:
            teams = session._location_creation_teams()
            session.location_team_choice_ids = teams
            session.show_location_team_choice = len(teams) > 1

    @api.depends('flow_check_ids', 'flow_check_ids.phase', 'flow_check_ids.validated_at',
                 'flow_check_ids.validation_votes', 'flow_check_ids.validator_ids',
                 'flow_check_ids.zone_id.statusSession')
    def _compute_pre_scan_ready(self):
        for session in self:
            checks = session.flow_check_ids.filtered(lambda c: c.phase == 'pre' and c.zone_id.statusSession)
            session.pre_scan_ready = bool(checks) and all(c._all_validators_signed() for c in checks)

    def _session_scan_pool(self):
        self.ensure_one()
        return self.sess_records.filtered(lambda z: z.is_session_scan and z.statusSession)

    def _get_or_create_session_scan_pool(self):
        self.ensure_one()
        self._lock_preparation()
        if self.state != 'Scan':
            raise UserError('Ajoutez les emplacements à l’étape Scan.')
        if not self.pre_scan_ready:
            raise UserError('Validez toutes les checklists avant scan avant de créer les emplacements.')
        pool = self._session_scan_pool()
        if pool:
            return pool
        zones = self.sess_records.filtered('statusSession')
        if not zones:
            raise UserError('Configurez les racks de la session avant le scan.')
        counts = zones.recs_scan
        if any(z.workflow_state != 'draft' for z in zones) or any(c.su or c.ok_done or c.count_phase != 'first' for c in counts):
            raise UserError('Cette session utilise déjà des comptages par niveau. Utilisez Modifier pour relancer le parcours avant de saisir des emplacements communs.')
        names = [(count.emplacement or '').strip().upper() for count in counts]
        if len(names) != len(set(names)):
            raise ValidationError('Des codes emplacement sont en double. Chaque emplacement doit avoir un code unique dans la session.')
        types = set(zones.mapped('typedegalia'))
        pool = internal(self.env['araymond.invsession']).create({
            'mainsession_id': self.id, 'is_session_scan': True,
            'magasinPhysique': 'All', 'magasinSAP': zones[0].magasinSAP,
            'rack': 'Autre', 'level': False, 'shift': zones[0].shift,
            'supervisor_id': zones[0].supervisor_id.id, 'nbrEmp': 1,
            'typedegalia': types.pop() if len(types) == 1 else 'Hybride',
            'statusSession': True, 'state': 'Creation',
        })
        # Unstarted locations (including those recreated by Modifier) can safely
        # join the session scope. Historical counts remain on their archived levels.
        for count in counts:
            count._set_count({'location_rack': count.rec_scan_id.rack,
                              'rec_scan_id': pool.id, 'typedegalia': pool._initial_galia()})
        self.invalidate_recordset(['manual_scan_count_ids'])
        return pool

    def _start_session_scan_pool(self):
        self.ensure_one()
        pool = self._session_scan_pool()
        checks = self.flow_check_ids.filtered(lambda c: c.phase == 'pre')
        if not pool or pool.workflow_state != 'draft':
            return
        if not pool.first_count_ids:
            return
        if not checks or any(not c._all_validators_signed() for c in checks):
            return
        values = {'checklist_validated': True, 'validated_by': self.env.uid,
                  'validated_at': fields.Datetime.now()}
        internal(pool).write({'nbrEmp': len(pool.first_count_ids)})
        pool._set_workflow(values)
        internal(pool).action_start_teams()
        for zone in self.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan):
            zone._set_workflow(dict(values, workflow_state=pool.workflow_state, state='Scan'))


class SessionLocationZone(models.Model):
    _inherit = 'araymond.invsession'

    is_session_scan = fields.Boolean(default=False, readonly=True, copy=False)

    def action_start_teams(self):
        self.ensure_one()
        if not self.is_session_scan:
            return super().action_start_teams()
        self._lock_workflow()
        self._check_supervisor()
        if self.workflow_state != 'draft' or not self.checklist_validated:
            raise UserError('Validez les checklists avant de démarrer les équipes.')
        if self.mainsession_id.state != 'Scan' or not self.statusSession:
            raise UserError('La session doit être à l’étape Scan.')
        counts = self.first_count_ids
        if any(not c.branch_id or c.branch_id.automatic_slot not in ('1', '2') for c in counts):
            raise ValidationError('Chaque emplacement doit être rattaché à une équipe de scan.')
        if not counts:
            return
        if any((not c.emplacement and not c.scan_first) or (c.emplacement and len(c.emplacement.strip()) != 4) for c in counts):
            raise ValidationError('Chaque emplacement doit avoir un code de quatre caractères.')
        if any(c.su or c.ok_done or c.scanNbr != 1 for c in counts):
            raise UserError('Ces emplacements contiennent déjà des scans.')
        # Ownership comes from the creating team, never from an automatic split.
        self._set_workflow({'workflow_state': 'first', 'state': 'Scan'})

    def _new_count(self, original, team, phase):
        if not self.is_session_scan:
            return super()._new_count(original, team, phase)
        from .team_workflow import TeamCount
        # The same location can belong to both initial lists. Keep third counts
        # distinct so the barcode duplicate checks remain scoped to each mission.
        siblings = self.recs_scan.filtered(lambda c: c.emplacement == original.emplacement
                                          and c.branch_id == team and c.count_phase == phase)
        return super(TeamCount, self.env['araymond.inventory']).create({
            'rec_scan_id': self.id, 'mainsession_id': str(self.mainsession_id.id),
            'emplacement': original.emplacement, 'branch_id': team.id,
            'location_rack': original.location_rack,
            'scan_first': original.scan_first,
            'count_phase': phase, 'origin_count_id': original.id,
            'typedegalia': 'RayPro' if self.mainsession_id.full_flow else original.typedegalia, 'scanNbr': 1,
            'count_round': max(siblings.mapped('count_round') or [0]) + 1,
        })

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self) and any('is_session_scan' in vals for vals in vals_list):
            raise AccessError('Les emplacements communs sont gérés par la session.')
        return super().create(vals_list)

    def write(self, values):
        if 'is_session_scan' in values and not trusted(self):
            raise AccessError('Le mode des emplacements ne peut pas être modifié.')
        return super().write(values)

    @api.depends('resume', 'level', 'mainsession_id.full_flow', 'is_session_scan')
    def _compute_display_name(self):
        super()._compute_display_name()
        for zone in self.filtered('is_session_scan'):
            zone.display_name = 'Emplacements de la session'

    def _advance_counts(self):
        if self.is_session_scan and self.workflow_state == 'first':
            self._lock_workflow()
            if set(self.first_count_ids.branch_id.mapped('automatic_slot')) != {'1', '2'}:
                return
        result = super()._advance_counts()
        self._sync_session_scan_progress()
        return result

    def _sync_session_scan_progress(self):
        for pool in self.filtered('is_session_scan'):
            for zone in pool.mainsession_id.sess_records.filtered(lambda z: z.statusSession and not z.is_session_scan):
                zone._set_workflow({'workflow_state': pool.workflow_state})

    def action_close_invsession(self):
        self.ensure_one()
        pool = self.mainsession_id._session_scan_pool()
        if not pool:
            return super().action_close_invsession()
        checks = self.mainsession_id.flow_check_ids.filtered(lambda c: c.phase == 'post')
        if not checks or any(not c._all_validators_signed() for c in checks) or pool.workflow_state != 'done':
            raise UserError('Terminez les scans et validez toutes les checklists après scan.')
        if not self.is_session_scan:
            self._set_workflow({'state': 'Fermeture'})
            return
        # The shared pool has no checklist of its own; all level checklists gate it.
        return super(FullZone, self).action_close_invsession()


class SessionLocationCount(models.Model):
    _inherit = 'araymond.inventory'

    _team_location_unique = models.Constraint(
        'UNIQUE(rec_scan_id, emplacement, count_phase, count_round, branch_id)',
        'Cet emplacement existe déjà pour cette équipe et ce passage.')

    @api.model
    def _check_initial_team(self, zone, team):
        if trusted(self):
            return
        if not team or team.session_id != zone.mainsession_id or team.automatic_slot not in ('1', '2'):
            raise ValidationError('Votre affectation doit permettre de déterminer une équipe de scan (1 ou 2).')
        if self.env.user not in zone.mainsession_id._scan_assigned_users(team.automatic_slot):
            raise AccessError('Seuls les membres affectés à cette équipe peuvent gérer ses emplacements.')

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        session_id = self.env.context.get('manual_location_session_id')
        if session_id and 'branch_id' in names:
            session = self.env['araymond.maininvsession'].browse(session_id)
            teams = session._location_creation_teams()
            if teams:
                values['branch_id'] = teams.sorted('automatic_slot')[0].id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        pending = set()
        for values in vals_list:
            zone = self.env['araymond.invsession'].browse(values.get('rec_scan_id'))
            if zone.is_session_scan and not trusted(self):
                teams = zone.mainsession_id._location_creation_teams()
                if not teams:
                    raise AccessError('Vous devez être sélectionné dans une équipe de scan sur la checklist avant scan pour créer un emplacement.')
                if values.get('branch_id') and values['branch_id'] not in teams.ids:
                    raise AccessError('L’emplacement doit appartenir à votre équipe de scan.')
                values['branch_id'] = values.get('branch_id') or teams.sorted('automatic_slot')[0].id
            if zone.is_session_scan:
                zone._lock_workflow()
                if not trusted(self) and not zone.mainsession_id.pre_scan_ready:
                    raise UserError('Validez toutes les checklists avant scan avant de créer les emplacements.')
                team = self.env['araymond.inventory.team'].browse(values.get('branch_id'))
                self._check_initial_team(zone, team)
                name = (values.get('emplacement') or '').strip().upper()
                if not name and values.get('scan_first'):
                    values['emplacement'] = False
                    continue
                key = (zone.id, team.id, name)
                if key in pending or self.with_context(active_test=False).search_count([
                    ('rec_scan_id', '=', zone.id), ('branch_id', '=', team.id),
                    ('emplacement', '=', name), ('count_phase', '=', 'first'), ('count_round', '=', 1),
                ]):
                    raise ValidationError('Cet emplacement existe déjà pour votre équipe ; utilisez la ligne existante.')
                pending.add(key)
        records = super().create(vals_list)
        if not trusted(self):
            for session in records.filtered(lambda c: c.rec_scan_id.is_session_scan).rec_scan_id.mainsession_id:
                session._start_session_scan_pool()
        return records

    @api.constrains('emplacement', 'branch_id', 'rec_scan_id', 'count_phase', 'count_round')
    def _check_location_duplicates(self):
        # Preserve legacy per-level uniqueness, and cover unassigned draft rows
        # (SQL UNIQUE treats a null team as distinct).
        for count in self:
            count.rec_scan_id._lock_workflow()
            if count.rec_scan_id.is_session_scan and count.scan_first and not count.emplacement:
                continue
            if count.rec_scan_id.is_session_scan and len((count.emplacement or '').strip()) != 4:
                raise ValidationError('Chaque emplacement doit avoir un code de quatre caractères.')
            domain = [('rec_scan_id', '=', count.rec_scan_id.id), ('emplacement', '=', count.emplacement),
                      ('count_phase', '=', count.count_phase), ('count_round', '=', count.count_round)]
            if count.rec_scan_id.is_session_scan:
                domain.append(('branch_id', '=', count.branch_id.id))
            if self.with_context(active_test=False).search_count(domain) > 1:
                raise ValidationError('Cet emplacement existe déjà pour cette équipe et ce passage.')

    def _session_lx03_rows(self):
        self.ensure_one()
        return self.rec_scan_id.mainsession_id.lxo3_records.filtered(
            lambda row: (row.emplacement or '').strip().upper() == (self.emplacement or '').strip().upper())

    def get_lxo3_pivot_data(self):
        if not self.rec_scan_id.is_session_scan:
            return super().get_lxo3_pivot_data()
        grouped = {}
        for row in self._session_lx03_rows():
            key = (self._normalize_compare_text(row.article), self._normalize_compare_text(row.lot))
            item = grouped.setdefault(key, {'session_id': row.session_id, 'article': key[0], 'lot': key[1], 'stock_total': 0.0})
            item['stock_total'] += row.stock_total
        return list(grouped.values())

    def get_lxo3_su(self):
        if not self.rec_scan_id.is_session_scan:
            return super().get_lxo3_su()
        return self._session_lx03_rows().filtered('su').mapped('su')

    def action_supervisor_accept(self):
        result = super().action_supervisor_accept()
        self.rec_scan_id._sync_session_scan_progress()
        return result
