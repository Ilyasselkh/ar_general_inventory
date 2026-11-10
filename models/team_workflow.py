"""Zone approval, independent team counts and automatic arbitration."""
from collections import defaultdict
from math import isfinite, isclose

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


def quantities(lines, allow_negative=False):
    result = defaultdict(float)
    for reference, lot, quantity in lines:
        value = float(str(quantity or '0').replace(' ', '').replace(',', '.'))
        if not isfinite(value) or (value < 0 and not allow_negative):
            raise ValueError('Quantité invalide')
        result[(str(reference or '').strip(), str(lot or '').strip())] += value
    return dict(result)


def same_quantities(left, right):
    return all(isclose(left.get(key, 0), right.get(key, 0), abs_tol=0.000001, rel_tol=0)
               for key in left.keys() | right.keys())


class InventoryTeam(models.Model):
    _name = 'araymond.inventory.team'
    _description = "Équipe d'inventaire"
    _order = 'sequence, id'

    name = fields.Char(required=True, string='Équipe / branche')
    sequence = fields.Integer(default=10)
    session_id = fields.Many2one('araymond.maininvsession', required=True, ondelete='cascade')
    user_ids = fields.Many2many('res.users', string='Membres', required=True)

    @api.constrains('user_ids', 'session_id')
    def _check_members(self):
        for team in self:
            if not team.user_ids or any(team.user_ids.mapped('share')):
                raise ValidationError('Chaque équipe doit avoir des utilisateurs internes.')
            others = self.search([('session_id', '=', team.session_id.id), ('id', '!=', team.id)])
            if team.user_ids & others.user_ids:
                raise ValidationError('Un opérateur ne peut appartenir à deux équipes de la même session.')

    def write(self, values):
        if self.env['araymond.inventory'].search_count([('branch_id', 'in', self.ids), ('rec_scan_id.workflow_state', '!=', 'draft')]):
            raise UserError('Une équipe engagée dans un scan ne peut plus être modifiée.')
        return super().write(values)

    def unlink(self):
        if self.env['araymond.inventory'].search_count([('branch_id', 'in', self.ids)]):
            raise UserError('Cette équipe possède des emplacements affectés.')
        return super().unlink()


class MainSession(models.Model):
    _inherit = 'araymond.maininvsession'

    team_ids = fields.One2many('araymond.inventory.team', 'session_id', string='Équipes')

    def _sync_zone_closure(self):
        for session in self.sorted('id'):
            self.env.cr.execute(
                'SELECT id FROM araymond_maininvsession WHERE id = %s FOR UPDATE',
                [session.id],
            )
            session.invalidate_recordset(['state', 'sess_records'])
            zones = session.sess_records
            zones.invalidate_recordset(['state'])
            if zones and all(zone.state == 'Fermeture' for zone in zones):
                if session.state == 'Scan':
                    session.state = 'Fermeture'
            elif zones and session.state == 'Fermeture':
                session.state = 'Scan'


class ZoneChecklistLine(models.Model):
    _name = 'araymond.inventory.checkline'
    _description = 'Consigne de préparation de zone'
    _order = 'sequence, id'

    zone_id = fields.Many2one('araymond.invsession', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    instruction = fields.Char(required=True, string='Consigne')
    answer = fields.Selection([('ok', 'OK'), ('nok', 'NOK')], string='Réponse')
    comment = fields.Text(string='Commentaire')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self.env['araymond.invsession'].browse(vals.get('zone_id'))._check_checklist_edit()
        return super().create(vals_list)

    def write(self, values):
        if 'zone_id' in values:
            raise UserError('Une consigne ne peut pas être déplacée vers une autre zone.')
        self.zone_id._check_checklist_edit()
        return super().write(values)

    def unlink(self):
        self.zone_id._check_checklist_edit()
        return super().unlink()


class InventoryZone(models.Model):
    _inherit = 'araymond.invsession'

    supervisor_id = fields.Many2one('res.users', string='Superviseur de zone', domain=[('share', '=', False)])
    checklist_ids = fields.One2many('araymond.inventory.checkline', 'zone_id', string='Checklist')
    checklist_validated = fields.Boolean(string='Checklist validée', readonly=True, copy=False)
    validated_by = fields.Many2one('res.users', string='Validée par', readonly=True, copy=False)
    validated_at = fields.Datetime(string='Validée le', readonly=True, copy=False)
    workflow_state = fields.Selection([
        ('draft', 'Préparation'), ('first', 'Scan des équipes'),
        ('cross', 'Contrôle croisé'), ('arbitration', 'Arbitrage'), ('done', 'Rapproché'),
    ], default='draft', required=True, readonly=True, copy=False, string='Avancement')
    team_ids = fields.One2many(related='mainsession_id.team_ids', readonly=True)
    arbitration_team_id = fields.Many2one('araymond.inventory.team', string="Équipe d'arbitrage")
    first_count_ids = fields.One2many('araymond.inventory', 'rec_scan_id', domain=[('count_phase', '=', 'first')], string='Emplacements à répartir')
    reconciliation = fields.Text(compute='_compute_reconciliation', string='Rapprochement des équipes et SAP')

    def _lock_workflow(self):
        for zone in self.sorted('id'):
            self.env.cr.execute('SELECT id FROM araymond_invsession WHERE id = %s FOR UPDATE', [zone.id])
        self.invalidate_recordset()

    def _check_supervisor(self):
        for zone in self:
            if self.env.user != zone.supervisor_id and not self.env.user.has_group('base.group_system'):
                raise AccessError('Cette opération est réservée au superviseur de la zone.')

    def _check_checklist_edit(self):
        self._lock_workflow()
        self._check_supervisor()
        if any(self.mapped('checklist_validated')):
            raise UserError('La checklist validée est verrouillée pour cette zone et cette session.')

    def _set_workflow(self, values):
        return super(InventoryZone, self).write(values)

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'checklist_validated', 'validated_by', 'validated_at', 'workflow_state'}
        if any(protected.intersection(vals) for vals in vals_list):
            raise AccessError('Utilisez les boutons de validation du workflow.')
        return super().create(vals_list)

    def write(self, values):
        if {'checklist_validated', 'validated_by', 'validated_at', 'workflow_state'} & values.keys():
            raise AccessError('Utilisez les boutons de validation du workflow.')
        self._lock_workflow()
        self._check_supervisor()
        if 'supervisor_id' in values and not self.env.user.has_group('base.group_system'):
            raise AccessError('Seul un administrateur peut désigner le superviseur.')
        structure = {'mainsession_id', 'rack', 'magasinSAP', 'magasinPhysique', 'nbrEmp', 'supervisor_id', 'ummondatory', 'dummondatory'}
        if structure & values.keys() and any(z.checklist_validated and not z.mainsession_id.full_flow for z in self):
            raise UserError('La configuration de la zone est verrouillée après validation.')
        if values.get('state') == 'Fermeture' and any(z.workflow_state != 'done' for z in self):
            raise UserError('Terminez le rapprochement de tous les emplacements avant clôture.')
        result = super().write(values)
        if 'state' in values:
            self.mainsession_id._sync_zone_closure()
        return result

    def unlink(self):
        self._check_supervisor()
        if any(self.mapped('checklist_validated')):
            raise UserError('Une zone validée ne peut pas être supprimée.')
        return super().unlink()

    def action_prepare_checklist(self):
        self.ensure_one()
        self._check_checklist_edit()
        if not self.checklist_ids:
            self.env['araymond.inventory.checkline'].create([
                {'zone_id': self.id, 'sequence': index * 10, 'instruction': instruction}
                for index, instruction in enumerate([
                    'Les mouvements de stock de la zone sont arrêtés.',
                    'Les emplacements et les étiquettes sont identifiés et accessibles.',
                    'Les équipes disposent des consignes et du matériel de scan.',
                ], 1)
            ])

    def action_validate_checklist(self):
        self.ensure_one()
        self._check_checklist_edit()
        if self.env.user != self.supervisor_id:
            raise AccessError('La checklist doit être validée par le superviseur désigné.')
        if not self.checklist_ids or any(not line.answer for line in self.checklist_ids):
            raise ValidationError('Répondez OK ou NOK à toutes les consignes.')
        if any(line.answer == 'nok' and not (line.comment or '').strip() for line in self.checklist_ids):
            raise ValidationError('Chaque réponse NOK nécessite un commentaire.')
        self._set_workflow({'checklist_validated': True, 'validated_by': self.env.uid, 'validated_at': fields.Datetime.now()})

    def action_start_teams(self):
        self.ensure_one()
        self._lock_workflow()
        self._check_supervisor()
        if self.workflow_state != 'draft':
            raise UserError('Le scan des équipes est déjà lancé.')
        if not self.checklist_validated:
            raise UserError('Le superviseur doit valider la checklist avant le scan.')
        if self.mainsession_id.state != 'Scan' or not self.statusSession:
            raise UserError('Activez la zone et passez la session principale en Scan.')
        counts = self.first_count_ids
        if len(counts) != self.nbrEmp or any(not c.emplacement or not c.branch_id for c in counts):
            raise ValidationError('Affectez les emplacements prévus, chacun avec un nom et une équipe.')
        if any(c.su or c.ok_done or c.scanNbr != 1 for c in counts):
            raise UserError('Des scans historiques existent. Créez une nouvelle zone pour le workflow par équipes.')
        teams = counts.branch_id.sorted(lambda t: (t.sequence, t.id))
        if len(teams) < 2:
            raise ValidationError('Répartissez les emplacements entre au moins deux équipes.')
        for team in teams:
            team._check_members()
        for count in counts:
            if len(count.emplacement.strip()) != 4:
                raise ValidationError('Les lecteurs actuels nécessitent des codes emplacement de 4 caractères.')
        self._set_workflow({'workflow_state': 'first', 'state': 'Scan'})

    def _new_count(self, original, team, phase):
        values = {
            'rec_scan_id': self.id, 'mainsession_id': str(self.mainsession_id.id),
            'emplacement': original.emplacement, 'branch_id': team.id,
            'count_phase': phase, 'origin_count_id': original.id,
            'typedegalia': 'RayPro' if self.mainsession_id.full_flow else original.typedegalia, 'scanNbr': 1,
        }
        return super(TeamCount, self.env['araymond.inventory']).create(values)

    def _advance_counts(self):
        self.ensure_one()
        self._lock_workflow()
        counts = self.recs_scan
        counts.invalidate_recordset()
        first = counts.filtered(lambda c: c.count_phase == 'first')
        if self.workflow_state == 'first' and first and all(first.mapped('ok_done')):
            teams = first.branch_id.sorted(lambda t: (t.sequence, t.id))
            for count in first:
                next_team = teams[(list(teams.ids).index(count.branch_id.id) + 1) % len(teams)]
                self._new_count(count, next_team, 'cross')
            self._set_workflow({'workflow_state': 'cross'})
        elif self.workflow_state == 'cross':
            cross = counts.filtered(lambda c: c.count_phase == 'cross')
            if len(cross) != len(first) or not all(cross.mapped('ok_done')):
                return
            for original in first:
                other = cross.filtered(lambda c: c.origin_count_id == original)
                agree = original.emplacementVide == other.emplacementVide and same_quantities(original._quantities(), other._quantities())
                original._set_count({'needs_arbitration': not agree, 'final_count_id': original.id if agree else False})
            self._set_workflow({'workflow_state': 'arbitration' if any(first.mapped('needs_arbitration')) else 'done'})
        elif self.workflow_state == 'arbitration':
            arbitration = counts.filtered(lambda c: c.count_phase == 'arbitration')
            for count in arbitration.filtered('ok_done'):
                count.origin_count_id._set_count({'final_count_id': count.id})
            if first and all(original.final_count_id for original in first):
                self._set_workflow({'workflow_state': 'done'})

    def action_start_arbitration(self):
        self.ensure_one()
        self._lock_workflow()
        self._check_supervisor()
        team = self.arbitration_team_id
        if self.workflow_state != 'arbitration' or not team or team.session_id != self.mainsession_id:
            raise UserError("Sélectionnez une équipe d'arbitrage de cette session après le rapprochement.")
        team._check_members()
        conflicts = self.first_count_ids.filtered(lambda c: c.needs_arbitration and not c.final_count_id)
        for original in conflicts:
            participants = (original | self.recs_scan.filtered(lambda c: c.origin_count_id == original and c.count_phase == 'cross')).branch_id
            if team in participants or team.user_ids & participants.user_ids:
                raise ValidationError("L'arbitrage nécessite une équipe différente des deux équipes ayant compté l'emplacement.")
        for original in conflicts:
            if not self.recs_scan.filtered(lambda c: c.origin_count_id == original and c.count_phase == 'arbitration'):
                self._new_count(original, team, 'arbitration')

    def action_my_counts(self):
        self.ensure_one()
        if not self.checklist_validated or self.workflow_state == 'draft':
            raise UserError('Validez la checklist et lancez le scan des équipes.')
        return {'type': 'ir.actions.act_window', 'name': 'Mes emplacements à scanner',
                'res_model': 'araymond.inventory', 'view_mode': 'list,form',
                'domain': [('rec_scan_id', '=', self.id), ('branch_id.user_ids', 'in', [self.env.uid]), ('ok_done', '=', False)]}

    def action_create_empl(self):
        self.ensure_one()
        self._check_supervisor()
        if self.workflow_state != 'draft':
            raise UserError('Les emplacements sont figés pendant le scan.')
        return super().action_create_empl()

    @api.depends('recs_scan.ok_done', 'recs_scan.final_count_id', 'recs_scan.needs_arbitration',
                 'recs_scan.su.qty', 'recs_scan.su.ref', 'recs_scan.su.lot', 'workflow_state')
    def _compute_reconciliation(self):
        for zone in self:
            rows = []
            for original in zone.first_count_ids.sorted('emplacement'):
                cross = zone.recs_scan.filtered(lambda c: c.origin_count_id == original and c.count_phase == 'cross')
                status = 'En attente des équipes'
                if original.needs_arbitration:
                    status = 'Écart — arbitrage requis'
                final = original.final_count_id
                if final:
                    status = 'Résultat final : %s' % final.branch_id.name
                rows.append('%s : %s' % (original.emplacement or '?', status))
                for count in original | cross:
                    if count.ok_done:
                        rows.append('  %s : %s' % (count.branch_id.name, count._quantities()))
                if final:
                    scan = final._quantities()
                    sap = quantities(((p['article'], p['lot'], p['stock_total']) for p in final.get_lxo3_pivot_data()), allow_negative=True)
                    rows.append('  Final : %s' % scan)
                    differences = ['%s / lot %s : SAP %g, final %g, écart %+g' % (key[0], key[1], sap.get(key, 0), scan.get(key, 0), scan.get(key, 0) - sap.get(key, 0))
                                   for key in sorted(sap.keys() | scan.keys()) if not same_quantities({key: sap.get(key, 0)}, {key: scan.get(key, 0)})]
                    rows.append('  SAP : ' + (' ; '.join(differences) if differences else 'Aucun écart'))
            zone.reconciliation = '\n'.join(rows)


class TeamCount(models.Model):
    _inherit = 'araymond.inventory'

    branch_id = fields.Many2one('araymond.inventory.team', string='Équipe / branche', ondelete='restrict', index=True)
    count_phase = fields.Selection([('first', 'Initial'), ('cross', 'Contrôle croisé'), ('arbitration', 'Arbitrage')], default='first', required=True, string='Mission')
    origin_count_id = fields.Many2one('araymond.inventory', string="Emplacement d'origine", ondelete='restrict', copy=False)
    final_count_id = fields.Many2one('araymond.inventory', string='Résultat final', ondelete='restrict', readonly=True, copy=False)
    needs_arbitration = fields.Boolean(string='Écart entre équipes', readonly=True, copy=False)
    checklist_ready = fields.Boolean(related='rec_scan_id.checklist_validated')
    zone_workflow_state = fields.Selection(related='rec_scan_id.workflow_state')

    _team_location_unique = models.Constraint('UNIQUE(rec_scan_id, emplacement, count_phase)', 'Cet emplacement existe déjà pour cette mission dans la zone.')

    @api.depends('su', 'su.scanNbr', 'scanNbr')
    def _count_nbr_bx(self):
        for count in self:
            count.count_box_scanne = len(count.su.filtered(lambda line: line.scanNbr == count.scanNbr))

    def _set_count(self, values):
        return super(TeamCount, self).write(values)

    def _check_scan(self):
        for count in self:
            zone = count.rec_scan_id
            zone._lock_workflow()
            count.invalidate_recordset()
            if not zone.checklist_validated or zone.workflow_state != count.count_phase or zone.state != 'Scan' or not zone.statusSession or zone.mainsession_id.state != 'Scan':
                raise UserError('Le scan est bloqué : vérifiez la checklist et la mission en cours.')
            if not count.branch_id or (not count.branch_id.automatic_slot and self.env.user not in count.branch_id.user_ids):
                raise AccessError('Seuls les membres de cette équipe peuvent scanner cet emplacement.')
            if count.ok_done:
                raise UserError('Ce relevé est terminé et verrouillé.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            zone = self.env['araymond.invsession'].browse(vals.get('rec_scan_id'))
            if not zone:
                raise ValidationError('Une zone est obligatoire.')
            zone._lock_workflow()
            if zone.is_session_scan:
                self._check_initial_team(zone, self.env['araymond.inventory.team'].browse(vals.get('branch_id')))
            else:
                zone._check_supervisor()
            if zone.workflow_state != 'draft' and not (zone.is_session_scan and zone.workflow_state == 'first'):
                raise UserError('Les missions sont générées automatiquement après le démarrage.')
            if any(vals.get(key) for key in ('ok_done', 'final_count_id', 'needs_arbitration', 'origin_count_id', 'su')) or vals.get('count_phase', 'first') != 'first' or vals.get('scanNbr', 1) != 1:
                raise AccessError('Créez uniquement des affectations initiales vides.')
            vals['mainsession_id'] = str(zone.mainsession_id.id)
            if vals.get('emplacement'):
                vals['emplacement'] = vals['emplacement'].strip().upper()
        return super().create(vals_list)

    @api.constrains('branch_id', 'rec_scan_id')
    def _check_team_session(self):
        for count in self:
            if count.branch_id and count.branch_id.session_id != count.rec_scan_id.mainsession_id:
                raise ValidationError("L'équipe doit appartenir à la même session.")

    def write(self, values):
        protected = {'count_phase', 'origin_count_id', 'final_count_id', 'needs_arbitration', 'scanNbr', 'ok_done', 'rec_scan_id', 'mainsession_id', 'active'}
        for count in self:
            count.rec_scan_id._lock_workflow()
            changed = {key: value for key, value in values.items()
                       if key not in protected or value != (count[key].id if key in ('origin_count_id', 'final_count_id', 'rec_scan_id') else count[key])}
            if protected & changed.keys():
                raise AccessError('Utilisez les actions du workflow pour modifier le statut du relevé.')
            if count.rec_scan_id.workflow_state == 'draft':
                if count.rec_scan_id.is_session_scan:
                    if count.branch_id or not values.get('branch_id'):
                        count._check_initial_team(count.rec_scan_id, count.branch_id)
                    if 'branch_id' in values:
                        count._check_initial_team(count.rec_scan_id, self.env['araymond.inventory.team'].browse(values['branch_id']))
                else:
                    count.rec_scan_id._check_supervisor()
                if set(changed) - {'emplacement', 'branch_id', 'typedegalia', 'commentaire', 'location_rack'}:
                    raise UserError('Le scan nécessite une checklist validée et un démarrage des équipes.')
            else:
                pending_configuration = (count.rec_scan_id.is_session_scan and count.scan_first
                    and not count.emplacement and not count.su and not count.ok_done
                    and count.count_phase == 'first' and count.rec_scan_id.workflow_state == 'first'
                    and count.rec_scan_id.statusSession and count.rec_scan_id.mainsession_id.state == 'Scan'
                    and set(values) <= {'location_rack', 'branch_id'})
                if pending_configuration:
                    count._check_initial_team(count.rec_scan_id, count.branch_id)
                    if 'branch_id' in values:
                        count._check_initial_team(count.rec_scan_id, self.env['araymond.inventory.team'].browse(values['branch_id']))
                else:
                    count._check_scan()
                if 'branch_id' in values and values['branch_id'] != count.branch_id.id and not pending_configuration:
                    raise UserError("L'équipe affectée est verrouillée.")
                if 'emplacement' in values and values['emplacement'] != count.emplacement and not (count.scan_first and not count.emplacement and count.count_phase == 'first'):
                    raise UserError('Scannez uniquement l’emplacement affecté à cette mission.')
        return super().write(values)

    def unlink(self):
        self.rec_scan_id._lock_workflow()
        for count in self:
            if count.rec_scan_id.is_session_scan:
                count._check_initial_team(count.rec_scan_id, count.branch_id)
            else:
                count.rec_scan_id._check_supervisor()
        if any((c.rec_scan_id.workflow_state != 'draft' and not (
                c.rec_scan_id.is_session_scan and c.scan_first and not c.emplacement
                and c.count_phase == 'first')) or c.su for c in self):
            raise UserError('Un relevé commencé ne peut pas être supprimé.')
        return super().unlink()

    def _quantities(self):
        self.ensure_one()
        try:
            return quantities((line.ref, line.lot, line.qty) for line in self.su)
        except (ValueError, TypeError):
            raise ValidationError('Corrigez les quantités invalides du relevé avant de continuer.')

    def action_view_form_inv(self):
        self._check_scan()
        action = super().action_view_form_inv()
        action.update(target='new', name='Scanner : %s' % self.emplacement)
        return action

    def action_close(self):
        self.ensure_one()
        self._check_scan()
        if not self.su and not self.emplacementVide:
            raise ValidationError('Scannez les étiquettes ou déclarez explicitement l’emplacement vide.')
        if self.su and self.emplacementVide:
            raise ValidationError('Un emplacement vide ne peut pas contenir des étiquettes.')
        if any(not line.ref for line in self.su):
            raise ValidationError('Une étiquette ne contient pas de référence valide.')
        self._quantities()
        previous_phase = self.rec_scan_id.workflow_state
        self._set_count({'ok_done': True})
        self.rec_scan_id._advance_counts()
        if previous_phase == 'cross' and self.rec_scan_id.workflow_state == 'arbitration' and self.rec_scan_id.automatic_teams:
            locations = sorted(set(self.rec_scan_id.first_count_ids.filtered('needs_arbitration').mapped('emplacement')))
            return {
                'type': 'ir.actions.client', 'tag': 'ar_inventory_arbitration_notice',
                'params': {'message': 'Écart détecté sur : %s. Un troisième comptage est nécessaire. Tous les emplacements de la zone sont affectés à l’équipe 3.' % ', '.join(locations)},
            }
        return {'type': 'ir.actions.act_window_close'}

    def action_show_ecarts_sapvs1scan(self):
        self.ensure_one()
        original = self.origin_count_id or self
        if not original.final_count_id:
            raise UserError('Le résultat final sera disponible après le rapprochement des équipes.')
        return original.final_count_id.compare_pivot_data_returnTxt()

    def action_show_ecarts_1vs2scan(self):
        raise UserError('Utilisez le rapprochement des équipes dans la zone.')

    def action_show_ecarts_2vs3scan(self):
        raise UserError('Utilisez le rapprochement des équipes dans la zone.')


class TeamLabel(models.Model):
    _inherit = 'araymond.etiquette'

    branch_id = fields.Many2one(related='su_id.branch_id', store=True, index=True)
    count_phase = fields.Selection(related='su_id.count_phase', store=True)

    _team_label_unique = models.Constraint('UNIQUE(branch_id, count_phase, resume)', 'Cette unité est déjà scannée par cette équipe pour cette mission.')

    def _duplicate_domain(self, vals):
        count = self.env['araymond.inventory'].browse(vals.get('su_id'))
        return [('numEtiq', '=', vals.get('numEtiq')), ('su_id.rec_scan_id.mainsession_id', '=', count.rec_scan_id.mainsession_id.id),
                ('branch_id', '=', count.branch_id.id), ('count_phase', '=', count.count_phase)]

    @api.depends('ref', 'hu', 'lot', 'mainsession_id', 'scanNbr', 'branch_id', 'count_phase')
    def ch_resume(self):
        for line in self:
            line.resume = '%s_%s_%s_%s_%s_%s' % (line.ref, line.hu, line.lot, line.mainsession_id, line.branch_id.id, line.count_phase)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            count = self.env['araymond.inventory'].browse(values.get('su_id'))
            if not count:
                raise ValidationError('Une mission de scan est obligatoire.')
            count._check_scan()
            # Serialize duplicate checks across zones for the same team.
            self.env.cr.execute('SELECT id FROM araymond_inventory_team WHERE id = %s FOR UPDATE', [count.branch_id.id])
            if count.emplacementVide:
                raise ValidationError('L’emplacement est déclaré vide.')
            values.update({'mainsession_id': str(count.rec_scan_id.mainsession_id.id), 'scanNbr': 1})
        records = self.browse()
        # Per-record creation also catches duplicates inside a batch.
        for values in vals_list:
            records |= super().create([values])
        return records

    def write(self, values):
        self.su_id._check_scan()
        if {'su_id', 'scanNbr', 'mainsession_id', 'branch_id', 'count_phase'} & values.keys():
            raise AccessError('Une étiquette ne peut pas être déplacée vers une autre mission.')
        if 'numEtiq' in values:
            raise UserError('Supprimez puis scannez à nouveau l’étiquette à corriger.')
        return super().write(values)

    def unlink(self):
        self.su_id._check_scan()
        return super().unlink()
