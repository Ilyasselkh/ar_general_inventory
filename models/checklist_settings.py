from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError
from markupsafe import escape


class ChecklistInstruction(models.Model):
    _name = 'araymond.inventory.instruction'
    _description = 'Consigne de checklist inventaire'
    _order = 'sequence, id'
    _rec_name = 'instruction'

    instruction = fields.Char(string='Consigne', required=True)
    sequence = fields.Integer(default=10, string='Ordre')
    active = fields.Boolean(default=True)

    def _sync_pending_zones(self):
        self.env['araymond.invsession'].with_user(SUPERUSER_ID).search([
            ('checklist_validated', '=', False), ('state', '!=', 'Fermeture'),
        ])._sync_checklist()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_pending_zones()
        return records

    def write(self, values):
        result = super().write(values)
        self._sync_pending_zones()
        return result

    def unlink(self):
        if self.env['araymond.inventory.checkline'].search_count([('instruction_id', 'in', self.ids)]):
            raise UserError('Archivez cette consigne pour préserver les checklists existantes.')
        return super().unlink()


class ChecklistAnswer(models.Model):
    _inherit = 'araymond.inventory.checkline'

    instruction_id = fields.Many2one('araymond.inventory.instruction', string='Consigne source', ondelete='restrict', readonly=True)
    validated = fields.Boolean(related='zone_id.checklist_validated')
    _instruction_zone_unique = models.Constraint('UNIQUE(zone_id, instruction_id)', 'Cette consigne est déjà présente dans la checklist.')

    def write(self, values):
        if {'answer', 'comment'} & values.keys() and any(
            line.zone_id.mainsession_id.state != 'Checklist' for line in self
        ):
            raise UserError('Remplissez la checklist pendant l’étape Check-list de la session.')
        return super().write(values)

    def action_answer_ok(self):
        self.write({'answer': 'ok'})

    def action_answer_nok(self):
        # A comment is checked when validating, so the user can select NOK first.
        self.write({'answer': 'nok'})


class ChecklistZone(models.Model):
    _inherit = 'araymond.invsession'

    state = fields.Selection(selection_add=[('Checklist', 'Check-list'), ('Scan',)], default='Creation')

    statusSession = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        zones = super().create(vals_list)
        zones._sync_checklist()
        return zones

    def _sync_checklist(self):
        templates = self.env['araymond.inventory.instruction'].search([])
        for zone in self.with_user(SUPERUSER_ID).sorted('id'):
            zone._lock_workflow()
            if zone.checklist_validated:
                continue
            existing = zone.checklist_ids.filtered('instruction_id')
            obsolete = existing.filtered(lambda line: line.instruction_id not in templates)
            existing -= obsolete
            obsolete.unlink()
            for template in templates:
                line = existing.filtered(lambda item: item.instruction_id.id == template.id)
                if not line:
                    line = zone.checklist_ids.filtered(lambda item: not item.instruction_id and item.instruction == template.instruction)[:1]
                    if line:
                        line.write({'instruction_id': template.id})
                if not line:
                    self.env['araymond.inventory.checkline'].with_user(SUPERUSER_ID).create({
                        'zone_id': zone.id, 'instruction_id': template.id,
                        'instruction': template.instruction, 'sequence': template.sequence,
                    })
                else:
                    values = {}
                    if line.instruction != template.instruction:
                        values.update(instruction=template.instruction, answer=False, comment=False)
                    if line.sequence != template.sequence:
                        values['sequence'] = template.sequence
                    if values:
                        # Template synchronization may reset unanswered preparation lines.
                        super(ChecklistAnswer, line).write(values)

    def action_prepare_checklist(self):
        self._check_checklist_edit()
        self._sync_checklist()
        if not self.checklist_ids:
            raise UserError('Ajoutez au moins une consigne dans Paramètres → Consignes de checklist.')

    def _create_locations_from_lx03(self):
        for zone in self:
            zone._lock_workflow()
            zone._check_supervisor()
            if not zone.automatic_teams or zone.workflow_state != 'draft':
                continue
            rows = zone.mainsession_id.lxo3_records
            if not rows:
                continue  # Preserve inventories configured manually without an import.
            normalize = lambda value: (value or '').strip().upper()
            if zone.rack == 'Autre' or zone.magasinPhysique in ('All', 'Autre'):
                raise UserError('Précisez le magasin et le rack pour générer les emplacements LX03 sans ambiguïté.')
            locations = sorted({normalize(row.emplacement) for row in rows
                if normalize(row.type_magasin) == zone.magasinPhysique
                and normalize(row.magasin) == zone.magasinSAP
                and normalize(row.emplacement).startswith(zone.rack)})
            if not locations:
                raise UserError('Aucun emplacement LX03 pour la zone %s / %s / %s. Vérifiez le magasin physique, le magasin SAP et le rack.' % (zone.magasinPhysique, zone.magasinSAP, zone.rack))
            if any(len(location) != 4 for location in locations):
                raise UserError('Les emplacements LX03 doivent avoir quatre caractères pour les lecteurs actuels.')
            existing = zone.first_count_ids
            names = [normalize(count.emplacement) for count in existing]
            if len(names) != len(set(names)) or set(names) - set(locations):
                raise UserError('Les emplacements déjà créés contiennent des doublons ou ne correspondent pas au LX03. Vérifiez la zone avant validation.')
            if zone.checklist_validated and (set(names) != set(locations) or zone.nbrEmp != len(locations)):
                raise UserError('La zone est déjà validée : sa configuration ne peut plus être modifiée.')
            if zone.nbrEmp != len(locations):
                zone.write({'nbrEmp': len(locations)})
            missing = [location for location in locations if location not in names]
            if missing:
                self.env['araymond.inventory'].create([
                    {'rec_scan_id': zone.id, 'emplacement': location,
                     'mainsession_id': str(zone.mainsession_id.id)}
                    for location in missing
                ])

    def action_open_zone(self):
        self.ensure_one()
        self.check_access('read')
        self._sync_checklist()
        return {'type': 'ir.actions.act_window', 'name': 'Checklist et scan de zone',
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'views': [(self.env.ref('ar_general_inventory.view_araymond_inventory_session_form').id, 'form')]}

    def action_start_teams(self):
        self.ensure_one()
        if any(not zone.checklist_validated for zone in self.mainsession_id.sess_records.filtered('statusSession')):
            raise UserError('Validez les checklists de toutes les zones actives avant de lancer les équipes.')
        return super().action_start_teams()

    def action_my_counts(self):
        return self._prepare_count_action(super().action_my_counts())

    def _prepare_count_action(self, action):
        # A zone opened from a session carries its own form_view_ref. Do not
        # reuse that form when navigating to the inventory-count model.
        context = dict(action.get('context', self.env.context))
        for key in ('form_view_ref', 'list_view_ref', 'tree_view_ref'):
            context.pop(key, None)
        action.update({
            'context': context,
            'views': [
                (self.env.ref('ar_general_inventory.view_araymond_inventory_N_tree').id, 'list'),
                (self.env.ref('ar_general_inventory.view_araymond_inventory_form').id, 'form'),
            ],
        })
        return action

    def action_validate_checklist(self):
        self.ensure_one()
        # Serialize validations from different supervisors in the same session.
        self.env.cr.execute('SELECT id FROM araymond_maininvsession WHERE id = %s FOR UPDATE', [self.mainsession_id.id])
        self._check_checklist_edit()
        if any(zone.mainsession_id.state != 'Checklist' for zone in self):
            raise UserError('La session doit être à l’étape Check-list pour valider les checklists.')
        self._create_locations_from_lx03()
        self._sync_checklist()
        super().action_validate_checklist()
        session = self.mainsession_id
        zones = session.sess_records.filtered('statusSession')
        zones.invalidate_recordset(['checklist_validated'])
        if zones and all(zones.mapped('checklist_validated')):
            session.action_start_scan()
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class ScanSession(models.Model):
    _inherit = 'araymond.maininvsession'

    state = fields.Selection(selection_add=[('Checklist', 'Check-list'), ('Scan',)])
    scan_count_ids = fields.Many2many('araymond.inventory', compute='_compute_scan_counts', string='Emplacements à scanner')
    scan_history_ids = fields.Many2many('araymond.inventory', compute='_compute_scan_history', string='Historique des comptages')
    scan_reconciliation = fields.Text(compute='_compute_scan_reconciliation', string='Rapprochement')
    scan_reconciliation_html = fields.Html(compute='_compute_scan_reconciliation_html', string='Rapprochement', sanitize=True)

    @api.depends('sess_records.reconciliation', 'sess_records.closure_report_html')
    def _compute_scan_reconciliation_html(self):
        for session in self:
            cards = []
            for zone in session.sess_records:
                if session._session_scan_pool() and not zone.is_session_scan:
                    continue
                if zone.closure_report_html:
                    cards.append(zone.closure_report_html)
                    continue
                rows = []
                for original in zone.first_count_ids.sorted('emplacement'):
                    final = original.final_count_id
                    status, tone = ('Résultat final disponible', 'success') if final else (
                        ('Troisième comptage requis', 'warning') if original.needs_arbitration
                        else ('En attente des équipes', 'secondary'))
                    results = []
                    counts = original | zone.recs_scan.filtered(lambda c: c.origin_count_id == original)
                    for count in counts.filtered('ok_done'):
                        values = '; '.join('%s · lot %s : %g' % (ref, lot or '—', qty)
                                           for (ref, lot), qty in sorted(count._quantities().items())) or 'Emplacement vide'
                        results.append('<div><strong>%s</strong> · %s<br/>%s</div>' % (
                            escape(count.branch_id.name or 'Équipe'),
                            escape(dict(count._fields['count_phase'].selection).get(count.count_phase)), escape(values)))
                    sap = 'Disponible après le rapprochement'
                    if final:
                        from .team_workflow import quantities, same_quantities
                        actual = final._quantities()
                        expected = quantities(((p['article'], p['lot'], p['stock_total']) for p in final.get_lxo3_pivot_data()), allow_negative=True)
                        differences = ['%s · lot %s : SAP %g / final %g / écart %+g' % (key[0], key[1] or '—', expected.get(key, 0), actual.get(key, 0), actual.get(key, 0)-expected.get(key, 0))
                                       for key in sorted(expected.keys() | actual.keys())
                                       if not same_quantities({key: expected.get(key, 0)}, {key: actual.get(key, 0)})]
                        sap = '; '.join(differences) or 'Conforme au stock SAP'
                    rows.append('<tr><td><strong>%s</strong></td><td><span class="badge text-bg-%s">%s</span></td><td>%s</td><td>%s</td></tr>' % (
                        escape(original.emplacement or '—'), tone, escape(status),
                        ''.join(results) or 'Aucun comptage terminé', escape(sap)))
                cards.append('<div class="o_ar_reconcile_zone"><h4>%s</h4><div class="table-responsive"><table class="table"><thead><tr><th>Emplacement</th><th>Avancement</th><th>Résultats des équipes</th><th>Comparaison SAP</th></tr></thead><tbody>%s</tbody></table></div></div>' % (
                    'Emplacements de la session' if zone.is_session_scan else '%s · Rack %s — Magasin SAP %s' % (
                    escape(zone.magasinPhysique), escape(zone.rack), escape(zone.magasinSAP)), ''.join(rows) or '<tr><td colspan="4">Aucun emplacement configuré.</td></tr>'))
            session.scan_reconciliation_html = ''.join(cards) or '<p>Aucune zone à rapprocher.</p>'

    @api.depends('sess_records.recs_scan.ok_done', 'sess_records.recs_scan.count_phase')
    def _compute_scan_history(self):
        for session in self:
            session.scan_history_ids = session.sess_records.recs_scan.filtered('ok_done').sorted(
                lambda c: (c.emplacement or '', c.scanNbr, c.id))

    @api.depends('sess_records.reconciliation', 'sess_records.resume')
    def _compute_scan_reconciliation(self):
        for session in self:
            session.scan_reconciliation = '\n\n'.join(
                '%s\n%s' % (zone.resume, zone.reconciliation or '')
                for zone in session.sess_records if zone.reconciliation)

    @api.depends('sess_records.statusSession', 'sess_records.workflow_state',
                 'sess_records.recs_scan.count_phase', 'sess_records.recs_scan.final_count_id')
    def _compute_scan_counts(self):
        for session in self:
            counts = self.env['araymond.inventory']
            for zone in session.sess_records.filtered('statusSession'):
                if zone.workflow_state == 'done':
                    counts |= zone.first_count_ids.mapped('final_count_id')
                else:
                    counts |= zone.recs_scan.filtered(lambda c: c.count_phase == zone.workflow_state)
            session.scan_count_ids = counts.sorted(lambda c: (c.emplacement or '', c.branch_id.id, c.id))

    def action_close_scan(self):
        self.ensure_one()
        zones = self.sess_records.filtered(lambda z: z.statusSession and z.state != 'Fermeture')
        if self.state != 'Scan' or not zones:
            raise UserError('Aucune zone en scan à clôturer.')
        zones._check_supervisor()
        if any(zone.workflow_state != 'done' for zone in zones):
            raise UserError('Terminez les comptages, le contrôle croisé et le rapprochement de toutes les zones avant clôture.')
        zones.action_close_invsession()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
    checklist_line_ids = fields.Many2many(
        'araymond.inventory.checkline', compute='_compute_checklist_lines',
        inverse='_inverse_checklist_lines', string='Checklists des zones',
    )

    @api.depends('sess_records.checklist_ids', 'sess_records.statusSession')
    def _compute_checklist_lines(self):
        for session in self:
            session.checklist_line_ids = session.sess_records.filtered('statusSession').checklist_ids

    def _inverse_checklist_lines(self):
        # Only existing answers are editable; membership is owned by each zone.
        pass

    def action_validate_session_checklist(self):
        self.ensure_one()
        zones = self.sess_records.filtered(lambda z: z.statusSession and not z.checklist_validated)
        if not self.env.user.has_group('base.group_system'):
            zones = zones.filtered(lambda z: z.supervisor_id == self.env.user)
        if not zones:
            raise UserError('Aucune checklist ne reste à valider pour vous. Attendez la validation des autres superviseurs.')
        for zone in zones.sorted('id'):
            zone.action_validate_checklist()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_scan_ok(self):
        for session in self:
            if session.state != 'Creation':
                raise UserError('Terminez la création des zones avant la Check-list.')
            zones = session.sess_records.filtered('statusSession')
            if not zones:
                raise UserError('Ajoutez au moins une zone active.')
            zones._create_locations_from_lx03()
            session.state = 'Checklist'
            zones.filtered(lambda z: z.workflow_state == 'draft').write({'state': 'Checklist'})

    def action_start_scan(self):
        for session in self:
            if session.state != 'Checklist':
                raise UserError('Passez par l’étape Check-list avant de démarrer le scan.')
            session.write({'state': 'Scan'})
            # Each checklist was explicitly approved by its supervisor. The last
            # approval starts all active zones, including other supervisors' zones.
            for zone in session.sess_records.filtered(lambda z: z.statusSession and z.workflow_state == 'draft').sudo():
                zone.action_start_teams()

    def write(self, values):
        if values.get('state') == 'Scan':
            for session in self.filtered(lambda record: not record.full_flow):
                zones = session.sess_records.filtered('statusSession')
                if not zones or any(not zone.checklist_validated for zone in zones):
                    raise UserError('Validez les checklists de toutes les zones actives avant de passer au scan.')
        return super().write(values)

    def action_open_configuration(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Configuration de la session',
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'views': [(self.env.ref('ar_general_inventory.view_araymond_inventory_parametres_main_form').id, 'form')]}
