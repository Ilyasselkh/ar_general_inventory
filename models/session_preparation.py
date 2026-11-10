from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .session_checklists import PREPARATION, INVENTORY_DAY


class PreparedSession(models.Model):
    _inherit = 'araymond.maininvsession'

    # False for existing rows on upgrade; create enables the new workflow.
    preparation_workflow = fields.Boolean(default=False, readonly=True, copy=False)
    state = fields.Selection([
        ('Preparation', 'Préparation'), ('Racks', 'Affectation des racks'),
        ('InventoryDay', 'Jour d’inventaire'), ('LX03', 'Import LX03'),
        ('Scan', 'Scan'), ('Fermeture', 'Clôture'),
        ('CreationMain', 'Import historique'), ('Creation', 'Configuration historique'), ('Checklist', 'Checklist historique'),
    ], default='Preparation')
    typesession = fields.Selection([('InvGen', 'Inventaire général')], default='InvGen')
    checklist_ids = fields.One2many('araymond.session.checklist', 'session_id', copy=False)
    preparation_line_ids = fields.Many2many('araymond.session.checklist.line', compute='_compute_preparation_lines', inverse='_inverse_preparation_lines')
    day_line_ids = fields.Many2many('araymond.session.checklist.line', compute='_compute_preparation_lines', inverse='_inverse_preparation_lines')
    post_line_ids = fields.Many2many('araymond.session.checklist.line', compute='_compute_preparation_lines', inverse='_inverse_preparation_lines')
    scans_completed = fields.Boolean(compute='_compute_scans_completed', string='Tous les scans terminés')

    @api.depends('sess_records.workflow_state', 'sess_records.statusSession')
    def _compute_scans_completed(self):
        for session in self:
            zones = session.sess_records.filtered('statusSession')
            session.scans_completed = bool(zones) and all(zone.workflow_state == 'done' for zone in zones)

    arbitration_zone_ids = fields.Many2many(
        'araymond.invsession', compute='_compute_arbitration_zones',
        inverse='_inverse_arbitration_zones', string='Racks à recompter',
    )

    @api.depends('sess_records.workflow_state', 'sess_records.statusSession', 'sess_records.state')
    def _compute_arbitration_zones(self):
        for session in self:
            session.arbitration_zone_ids = session.sess_records.filtered(
                lambda zone: zone.statusSession and zone.state != 'Fermeture'
                and zone.workflow_state == 'arbitration')

    def _inverse_arbitration_zones(self):
        # Membership is computed; edits to the existing racks keep their own
        # supervisor permissions and third-team independence checks.
        pass

    @api.depends('checklist_ids.line_ids', 'checklist_ids.phase')
    def _compute_preparation_lines(self):
        for session in self:
            for phase, field in [('preparation', 'preparation_line_ids'), ('day', 'day_line_ids'), ('post', 'post_line_ids')]:
                session[field] = session.checklist_ids.filtered(lambda c: c.phase == phase).line_ids

    def _inverse_preparation_lines(self):
        pass

    def _lock_preparation(self):
        for session in self.sorted('id'):
            self.env.cr.execute('SELECT id FROM araymond_maininvsession WHERE id = %s FOR UPDATE', [session.id])
        self.invalidate_recordset()

    def _check_approver(self, phase):
        if not self.env['araymond.inventory.approver'].search_count([
            ('user_ids', 'in', [self.env.uid]), ('phase', 'in', [phase, 'all']),
        ]):
            raise AccessError('Vous n’êtes pas affecté à la validation de cette checklist dans Paramètres → Responsables.')

    def _set_preparation_state(self, state):
        return super(PreparedSession, self).write({'state': state})

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        if 'preparation_workflow' in field_names:
            values['preparation_workflow'] = True
        if 'preparation_line_ids' in field_names:
            values['preparation_line_ids'] = [
                (0, 0, {'sequence': number, 'instruction': instruction})
                for number, instruction in enumerate(PREPARATION, 1)
            ]
        return values

    @api.model_create_multi
    def create(self, vals_list):
        draft_answers = []
        for values in vals_list:
            if values.get('state', 'Preparation') != 'Preparation' or values.get('checklist_ids'):
                raise UserError('Une nouvelle session commence par la checklist de préparation.')
            values.update(preparation_workflow=True, state='Preparation')
            answers = {}
            for position, command in enumerate(values.pop('preparation_line_ids', []), 1):
                if command[0] != 0:
                    raise ValidationError('Créez les points de préparation à partir du formulaire de la session.')
                line = command[2]
                # Readonly columns may be omitted by web_save. The fixed list
                # retains template order when those columns are not submitted.
                number = line.get('sequence') or position
                if number not in range(1, len(PREPARATION) + 1) or number in answers:
                    raise ValidationError('Les points de préparation sont invalides ou dupliqués.')
                answers[number] = {key: line[key] for key in ('responsible_id', 'answer', 'comment', 'attachment_ids') if key in line}
            draft_answers.append(answers)
        sessions = super().create(vals_list)
        for session, answers in zip(sessions, draft_answers):
            for phase, instructions, offset in [('preparation', PREPARATION, 0), ('day', INVENTORY_DAY[:10], 0), ('post', INVENTORY_DAY[10:], 10)]:
                self.env['araymond.session.checklist'].sudo().create({
                    'session_id': session.id, 'phase': phase,
                    'line_ids': [(0, 0, {'sequence': number, 'instruction': text,
                        'responsible_role': 'Key user / Superviseur magasin' if phase == 'preparation' and 8 <= number <= 14 else 'Key user'})
                        for number, text in enumerate(instructions, offset + 1)],
                })
            for line in session.checklist_ids.filtered(lambda c: c.phase == 'preparation').line_ids:
                if answers.get(line.sequence):
                    line.write(answers[line.sequence])
        return sessions

    def write(self, values):
        for session in self.filtered('preparation_workflow'):
            session._lock_preparation()
            if {'preparation_workflow', 'checklist_ids', 'state'} & values.keys():
                raise AccessError('Utilisez les boutons pour avancer dans les étapes de la session.')
            if {'datesession', 'typesession', 'desc'} & values.keys() and session.state != 'Preparation':
                raise UserError('Les informations de la session sont figées après validation de la préparation.')
            if {'lx03_file', 'lx03_filename', 'lxo3_records', 'file_loaded'} & values.keys() and session.state != 'LX03':
                raise UserError('Le fichier LX03 se charge après validation de la checklist du jour.')
        if 'lx03_file' in values:
            values = dict(values, file_loaded=False)
        return super().write(values)

    def action_validate_preparation(self):
        self.ensure_one()
        if not (self.desc or '').strip():
            raise ValidationError('Renseignez la description de la session.')
        return self.checklist_ids.filtered(lambda c: c.phase == 'preparation').action_validate()

    def action_validate_day(self):
        self.ensure_one()
        return self.checklist_ids.filtered(lambda c: c.phase == 'day').action_validate()

    def action_validate_post(self):
        self.ensure_one()
        return self.checklist_ids.filtered(lambda c: c.phase == 'post').action_validate()

    def action_confirm_racks(self):
        self.ensure_one()
        self._lock_preparation()
        if self.state != 'Racks':
            raise UserError('La préparation doit être validée avant de configurer les racks.')
        zones = self.sess_records.filtered('statusSession')
        if not zones:
            raise ValidationError('Ajoutez au moins un rack actif.')
        zones._check_rack_assignments()
        keys = [(z.magasinPhysique, z.magasinSAP, z.rack) for z in zones]
        if len(keys) != len(set(keys)):
            raise ValidationError('Un rack ne peut apparaître qu’une fois pour un même magasin physique et magasin SAP.')
        self._set_preparation_state('InventoryDay')

    def action_load_file(self):
        for session in self.filtered('preparation_workflow'):
            session._lock_preparation()
            if session.state != 'LX03':
                raise UserError('Validez la checklist du jour avant de charger LX03.')
        return super().action_load_file()

    def action_validate_lx03(self):
        self.ensure_one()
        self._lock_preparation()
        if self.state != 'LX03' or not self.file_loaded or not self.lxo3_records:
            raise UserError('Joignez puis chargez le fichier LX03 avant validation.')
        zones = self.sess_records.filtered('statusSession')
        zones._check_rack_assignments()
        # The session approvals replace the legacy per-zone checklists.
        for zone in zones.sudo():
            expected_count = zone.nbrEmp
            zone._create_locations_from_lx03()
            if zone.nbrEmp != expected_count:
                raise ValidationError('Rack %s : LX03 contient %s emplacements, contre %s prévus.' % (zone.rack, zone.nbrEmp, expected_count))
            zone._set_workflow({'checklist_validated': True,
                'validated_by': self.checklist_ids.filtered(lambda c: c.phase == 'day').validated_by.id,
                'validated_at': self.checklist_ids.filtered(lambda c: c.phase == 'day').validated_at})
        self._set_preparation_state('Scan')
        for zone in zones.sudo():
            zone.action_start_teams()
        return self.action_open_scan()

    def action_open_scan(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'views': [(self.env.ref('ar_general_inventory.view_scan_session_form').id, 'form')]}

    def action_open_legacy_configuration(self):
        self.ensure_one()
        if self.preparation_workflow:
            return self.action_open_configuration()
        return super().action_open_configuration()

    def action_open_configuration(self):
        self.ensure_one()
        if not self.preparation_workflow:
            return super().action_open_configuration()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'views': [(self.env.ref('ar_general_inventory.view_session_preparation_form').id, 'form')]}

    def action_close_scan(self):
        for session in self.filtered('preparation_workflow'):
            session._lock_preparation()
            if not session.checklist_ids.filtered(lambda c: c.phase == 'post').validated_at:
                raise UserError('Faites valider la checklist après scan avant la clôture.')
        return super().action_close_scan()

    def _sync_zone_closure(self):
        legacy = self.filtered(lambda s: not s.preparation_workflow)
        super(PreparedSession, legacy)._sync_zone_closure()
        for session in self - legacy:
            session._lock_preparation()
            zones = session.sess_records.filtered('statusSession')
            if zones and all(z.state == 'Fermeture' for z in zones):
                session._set_preparation_state('Fermeture')

    def action_mod_empl(self):
        if any(self.mapped('preparation_workflow')):
            raise UserError('Les étapes déjà validées sont verrouillées.')
        return super().action_mod_empl()

    def action_creation(self):
        if any(self.mapped('preparation_workflow')):
            return self.action_validate_lx03()
        return super().action_creation()

    def action_scan_ok(self):
        if any(self.mapped('preparation_workflow')):
            return self.action_confirm_racks()
        return super().action_scan_ok()

    def action_start_scan(self):
        if any(self.mapped('preparation_workflow')):
            return self.action_validate_lx03()
        return super().action_start_scan()

    def unlink(self):
        for session in self.filtered('preparation_workflow'):
            session._lock_preparation()
            if session.state != 'Preparation':
                raise UserError('Une session déjà validée doit être conservée dans l’historique.')
        return super().unlink()


class PreparedZone(models.Model):
    _inherit = 'araymond.invsession'

    level = fields.Selection([('SOL', 'SOL'), ('N1', 'N1'), ('N2', 'N2'), ('N3', 'N3')], string='Niveau')
    scan1_user_ids = fields.Many2many('res.users', 'ar_zone_scan1_user_rel', string='Scan 1', domain=[('share', '=', False)])
    scan2_user_ids = fields.Many2many('res.users', 'ar_zone_scan2_user_rel', string='Scan 2', domain=[('share', '=', False)])
    scan3_user_ids = fields.Many2many('res.users', 'ar_zone_scan3_user_rel', string='Équipe 3 — recomptage', domain=[('share', '=', False)])
    preparation_workflow = fields.Boolean(related='mainsession_id.preparation_workflow')

    def _check_rack_assignments(self):
        for zone in self:
            if not zone.supervisor_id or not zone.level or zone.nbrEmp < 1 or not zone.scan1_user_ids or not zone.scan2_user_ids or not zone.scan3_user_ids:
                raise ValidationError('Chaque rack doit avoir un responsable, un niveau, un nombre d’emplacements et des personnes pour les équipes 1, 2 et 3.')
            if any((zone.scan1_user_ids | zone.scan2_user_ids | zone.scan3_user_ids | zone.supervisor_id).mapped('share')):
                raise ValidationError('Les responsables et les opérateurs doivent être des utilisateurs internes.')

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            session = self.env['araymond.maininvsession'].browse(values.get('mainsession_id'))
            if session.preparation_workflow:
                session._lock_preparation()
                if session.state != 'Racks':
                    raise UserError('Ajoutez les racks après validation de la préparation.')
                values['automatic_teams'] = True
        return super().create(vals_list)

    def write(self, values):
        structural = {'level', 'scan1_user_ids', 'scan2_user_ids', 'rack', 'magasinSAP', 'magasinPhysique',
                      'nbrEmp', 'supervisor_id', 'statusSession', 'automatic_teams', 'mainsession_id', 'typedegalia'}
        for zone in self.filtered('preparation_workflow'):
            zone.mainsession_id._lock_preparation()
            if structural & values.keys() and zone.mainsession_id.state != 'Racks':
                # LX03 generation verifies the previously declared number, never changes it.
                if set(values) != {'nbrEmp'} or values['nbrEmp'] != zone.nbrEmp:
                    raise UserError('La configuration des racks est verrouillée après confirmation.')
            if 'scan3_user_ids' in values and zone.workflow_state != 'arbitration' and zone.mainsession_id.state != 'Racks':
                raise UserError('Affectez l’équipe 3 pendant la préparation ou lorsqu’un recomptage est requis.')
            if 'scan3_user_ids' in values and zone.recs_scan.filtered(lambda c: c.count_phase == 'arbitration' and (c.su or c.ok_done)):
                raise UserError('L’équipe 3 ne peut plus être modifiée après le début du recomptage.')
            if values.get('state') == 'Fermeture' and not zone.mainsession_id.checklist_ids.filtered(lambda c: c.phase == 'post').validated_at:
                raise UserError('Validez les contrôles après scan avant de clôturer.')
        result = super().write(values)
        if {'scan1_user_ids', 'scan2_user_ids', 'scan3_user_ids'} & values.keys():
            self.filtered('preparation_workflow')._check_rack_assignments()
        return result

    def unlink(self):
        for zone in self.filtered('preparation_workflow'):
            zone.mainsession_id._lock_preparation()
            if zone.mainsession_id.state != 'Racks':
                raise UserError('Les racks sont verrouillés après confirmation.')
        return super().unlink()

    def _sync_checklist(self):
        return super(PreparedZone, self.filtered(lambda z: not z.preparation_workflow))._sync_checklist()

    def action_validate_checklist(self):
        if any(self.mapped('preparation_workflow')):
            raise UserError('Les checklists se valident au niveau de la session.')
        return super().action_validate_checklist()


class AssignedCount(models.Model):
    _inherit = 'araymond.inventory'

    def _check_scan(self):
        for count in self:
            zone = count.rec_scan_id
            if zone.preparation_workflow:
                zone.mainsession_id._lock_preparation()
                slot = count.branch_id.automatic_slot
                members = {'1': zone.scan1_user_ids, '2': zone.scan2_user_ids, '3': zone.scan3_user_ids}.get(slot, self.env['res.users'])
                if self.env.user not in members:
                    raise AccessError('Seules les personnes affectées à cette équipe pour ce rack peuvent scanner.')
                if zone.mainsession_id.checklist_ids.filtered(lambda c: c.phase == 'post').validated_at:
                    raise UserError('Les contrôles après scan sont déjà validés.')
        return super()._check_scan()


class PreparedLX03(models.Model):
    _inherit = 'araymond.lxo3'

    def _check_import_stage(self, sessions):
        for session in sessions.filtered('preparation_workflow'):
            session._lock_preparation()
            if session.state != 'LX03':
                raise UserError('Les données LX03 sont modifiables uniquement pendant l’étape d’import.')

    @api.model_create_multi
    def create(self, vals_list):
        self._check_import_stage(self.env['araymond.maininvsession'].browse([v['session_id'] for v in vals_list if v.get('session_id')]))
        return super().create(vals_list)

    def write(self, values):
        sessions = self.session_id
        if values.get('session_id'):
            sessions |= self.env['araymond.maininvsession'].browse(values['session_id'])
        self._check_import_stage(sessions)
        return super().write(values)

    def unlink(self):
        self._check_import_stage(self.session_id)
        return super().unlink()
