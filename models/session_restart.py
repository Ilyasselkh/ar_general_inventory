"""Restart a session without reusing or deleting its previous counts."""
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .complete_flow import internal, trusted


class RestartSession(models.Model):
    _inherit = 'araymond.maininvsession'

    scan_cycle = fields.Integer(default=1, readonly=True, copy=False, string='Passage')
    restart_history_html = fields.Html(readonly=True, copy=False, string='Passages précédents')
    sess_records = fields.One2many(domain=[('flow_archived', '=', False)])
    flow_check_ids = fields.One2many(domain=[
        ('go_current', '=', True), ('phase', '!=', 'export'),
        '|', ('zone_id', '=', False), ('zone_id.flow_archived', '=', False),
        '|', ('phase', 'not in', ['pre', 'post']), ('zone_id', '!=', False),
        '|', ('phase', 'not in', ['pre', 'post']), ('zone_id.checklist_selected', '=', True),
    ])

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self) and any({'scan_cycle', 'restart_history_html'} & vals.keys() for vals in vals_list):
            raise AccessError('Les passages sont gérés par le bouton Modifier.')
        return super().create(vals_list)

    def write(self, values):
        if not trusted(self) and {'scan_cycle', 'restart_history_html'} & values.keys():
            raise AccessError('L’historique des passages est en lecture seule.')
        return super().write(values)

    def _restart_snapshot(self, zones):
        self.ensure_one()
        parts = [Markup('<section><h3>Passage %s — archivé le %s (UTC)</h3><p>Par %s</p>') %
                 (self.scan_cycle, fields.Datetime.now(), self.env.user.name)]
        parts.append(Markup('<h4>Validations avant reprise</h4><ul>'))
        for check in self.flow_check_ids.filtered(lambda c: not c.zone_id or c.zone_id in zones):
            votes = check.validation_votes or {}
            signatures = '; '.join('%s : %s' % (user.name, votes.get(str(user.id)) or 'En attente')
                                   for user in check.validator_ids)
            parts.append(Markup('<li>%s — %s : %s</li>') % (
                dict(check._fields['phase'].selection).get(check.phase),
                check.go_role_name or check.rack_label, signatures or 'Aucun validateur'))
        parts.append(Markup('</ul>'))
        # Freeze the SAP comparison before a subsequent LX03 replacement.
        parts.append(Markup(self.scan_reconciliation_html or ''))
        parts.append(Markup('<h4>Détail des comptages, y compris les saisies en cours</h4>'))
        for zone in zones:
            parts.append(Markup('<h5>%s</h5><table class="table"><thead><tr><th>Emplacement</th><th>Équipe / passage</th><th>Statut</th><th>Saisies</th></tr></thead><tbody>') % zone.display_name)
            for count in zone.recs_scan.sorted('id'):
                labels = Markup('<br/>').join(Markup('%s — %s / lot %s : %s') % (
                    label.numEtiq or '', label.ref or '', label.lot or '', label.qty or '') for label in count.su)
                parts.append(Markup('<tr><td>%s</td><td>%s / %s / %s</td><td>%s</td><td>%s</td></tr>') % (
                    count.emplacement, count.branch_id.name or 'Non affectée',
                    dict(count._fields['count_phase'].selection).get(count.count_phase), count.count_round,
                    'Terminé' if count.ok_done else 'En cours / non commencé',
                    labels or ('Emplacement vide' if count.emplacementVide else 'Aucune saisie')))
            parts.append(Markup('</tbody></table>'))
        parts.append(Markup('</section>'))
        return Markup('').join(parts)

    def action_mod_empl(self):
        self.ensure_one()
        if not self.full_flow:
            return super().action_mod_empl()
        self.check_access('write')
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Seul un administrateur peut relancer le parcours.')
        self._lock_preparation()
        if self.state != 'Scan':
            raise UserError('Le parcours peut être relancé uniquement pendant le scan.')
        zones = self.sess_records
        zones._lock_workflow()
        history = Markup(self.restart_history_html or '') + self._restart_snapshot(zones)
        checks = self.flow_check_ids.filtered(lambda c: not c.zone_id)
        zone_fields = ['magasinPhysique', 'magasinSAP', 'rack', 'level', 'shift', 'nbrEmp',
                       'supervisor_id', 'ummondatory', 'dummondatory', 'typedegalia',
                       'automatic_teams', 'statusSession', 'is_session_scan', 'checklist_selected']
        # Copy only configuration: no labels, completed counts, or signatures.
        configurations = [(zone, {name: zone[name].id if zone._fields[name].type == 'many2one'
                                   else zone[name] for name in zone_fields}) for zone in zones]
        self._flow_store({'state': 'Preparation', 'scan_cycle': self.scan_cycle + 1,
                          'restart_history_html': str(history)})
        internal(zones).write({'statusSession': False, 'flow_archived': True})
        self.invalidate_recordset(['sess_records', 'flow_check_ids', 'rack_check_ids', 'manual_scan_count_ids'])
        internal(checks).write({'validated_by': False, 'validated_at': False})
        internal(checks.line_ids).write({'answer': 'wait'})
        for old_zone, values in configurations:
            values.update(mainsession_id=self.id, state='Creation', scan_cycle=self.scan_cycle)
            new_zone = internal(self.env['araymond.invsession']).create(values)
            for count in old_zone.first_count_ids:
                internal(self.env['araymond.inventory']).create({
                    'rec_scan_id': new_zone.id, 'emplacement': count.emplacement,
                    'branch_id': count.branch_id.id, 'typedegalia': count.typedegalia,
                    'location_rack': count.location_rack,
                    'scan_first': count.scan_first,
                })
        self._make_checklists()
        self._flow_message('Retour à Préparation : passage %s. Validations à refaire ; anciens scans conservés dans Historique.' % self.scan_cycle)
        action = self.action_open_configuration()
        action['context'] = dict(self.env.context, inventory_scan_menu=False)
        return action


class RestartZone(models.Model):
    _inherit = 'araymond.invsession'

    flow_archived = fields.Boolean(default=False, readonly=True, copy=False)
    scan_cycle = fields.Integer(default=1, readonly=True, copy=False)

    def _check_supervisor(self):
        if not trusted(self) and any(self.mapped('flow_archived')):
            raise UserError('Les niveaux des passages archivés sont en lecture seule.')
        return super()._check_supervisor()

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self) and any({'flow_archived', 'scan_cycle'} & vals.keys() for vals in vals_list):
            raise AccessError('Les passages sont gérés par le bouton Modifier.')
        for values in vals_list:
            session = self.env['araymond.maininvsession'].browse(values.get('mainsession_id'))
            values.setdefault('scan_cycle', session.scan_cycle or 1)
        return super().create(vals_list)

    def write(self, values):
        if not trusted(self) and (any(self.mapped('flow_archived')) or {'flow_archived', 'scan_cycle'} & values.keys()):
            raise AccessError('Les niveaux des passages archivés sont en lecture seule.')
        return super().write(values)


class RestartCount(models.Model):
    _inherit = 'araymond.inventory'

    def _check_scan(self):
        if any(self.rec_scan_id.mapped('flow_archived')):
            raise UserError('Ce comptage appartient à un passage archivé.')
        return super()._check_scan()

    def _require_supervisor(self):
        if any(self.rec_scan_id.mapped('flow_archived')):
            raise UserError('Ce comptage appartient à un passage archivé.')
        return super()._require_supervisor()

    @api.model_create_multi
    def create(self, vals_list):
        if any(self.env['araymond.invsession'].browse(vals.get('rec_scan_id')).flow_archived for vals in vals_list):
            raise UserError('Impossible d’ajouter un comptage à un passage archivé.')
        return super().create(vals_list)

    def write(self, values):
        if any(self.rec_scan_id.mapped('flow_archived')):
            raise UserError('Les comptages archivés sont en lecture seule.')
        return super().write(values)

    def unlink(self):
        if any(self.rec_scan_id.mapped('flow_archived')):
            raise UserError('Conservez les comptages archivés.')
        return super().unlink()


class RestartLabel(models.Model):
    _inherit = 'araymond.etiquette'

    def _duplicate_domain(self, vals):
        count = self.env['araymond.inventory'].browse(vals.get('su_id'))
        return super()._duplicate_domain(vals) + [('su_id.rec_scan_id.scan_cycle', '=', count.rec_scan_id.scan_cycle)]

    @api.depends('ref', 'hu', 'lot', 'mainsession_id', 'scanNbr', 'branch_id', 'count_phase', 'su_id.rec_scan_id.scan_cycle')
    def ch_resume(self):
        super().ch_resume()
        for label in self:
            cycle = label.su_id.rec_scan_id.scan_cycle
            if cycle > 1:
                label.resume = '%s_cycle_%s' % (label.resume, cycle)
