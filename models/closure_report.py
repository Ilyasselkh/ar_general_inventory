from markupsafe import escape

from odoo import api, fields, models
from odoo.exceptions import UserError

from .team_workflow import quantities, same_quantities


REPORT_NAME = 'inventory_closure_report.html'


class ZoneClosureReport(models.Model):
    _inherit = 'araymond.invsession'

    closure_report_html = fields.Html(compute='_compute_closure_report', sanitize=True)
    history_report_html = fields.Html(compute='_compute_history_report', sanitize=True)

    @api.depends('closure_report_html', 'state', 'reconciliation')
    def _compute_history_report(self):
        for zone in self:
            if zone.closure_report_html:
                zone.history_report_html = zone.closure_report_html
            elif zone.state == 'Fermeture':
                try:
                    zone.history_report_html = zone._build_closure_report(historical=True)
                except UserError:
                    zone.history_report_html = '<p>Les données de cette ancienne zone ne permettent pas de reconstituer un bilan final complet.</p><pre>%s</pre>' % escape(zone.reconciliation or '')
            else:
                zone.history_report_html = False

    @api.depends('state')
    def _compute_closure_report(self):
        for zone in self:
            report = self.env['ir.attachment'].search([
                ('res_model', '=', zone._name), ('res_id', '=', zone.id),
                ('name', '=', REPORT_NAME),
            ], order='id desc', limit=1)
            zone.closure_report_html = report.raw.decode('utf-8') if report else False

    def _build_closure_report(self, historical=False):
        self.ensure_one()
        rows, has_difference = [], False
        for original in self.first_count_ids.sorted('emplacement'):
            counts = original | self.recs_scan.filtered(lambda c: c.origin_count_id == original)
            third = counts.filtered(lambda c: c.count_phase == 'arbitration')
            if not original.final_count_id or any(not c.ok_done for c in counts):
                raise UserError('Tous les comptages doivent être terminés avant clôture.')
            if not historical and self.automatic_teams and self.arbitration_team_id and not third:
                raise UserError('L’équipe 3 doit compter tous les emplacements avant clôture.')
            final = original.final_count_id
            actual = final._quantities()
            sap = quantities(((p['article'], p['lot'], p['stock_total'])
                              for p in final.get_lxo3_pivot_data()), allow_negative=True)
            values = [(c, c._quantities()) for c in counts]
            keys = set(sap) | set(actual)
            for _, result in values:
                keys.update(result)
            for key in sorted(keys) or [('', '')]:
                team_gap = any(not same_quantities({key: result.get(key, 0)}, {key: actual.get(key, 0)})
                               or c.emplacementVide != final.emplacementVide for c, result in values)
                sap_gap = not same_quantities({key: sap.get(key, 0)}, {key: actual.get(key, 0)})
                has_difference |= team_gap or sap_gap
                details = '<br/>'.join('%s (%s) : %g' % (
                    escape(c.branch_id.name or 'Équipe'),
                    escape(dict(c._fields['count_phase'].selection)[c.count_phase]), result.get(key, 0))
                    for c, result in values)
                rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%g</td><td>%g</td><td>%+g</td><td>%s</td></tr>' % (
                    escape(original.emplacement), escape(key[0] or 'Emplacement vide'), escape(key[1] or '—'),
                    details, actual.get(key, 0), sap.get(key, 0), actual.get(key, 0)-sap.get(key, 0),
                    ' / '.join(filter(None, ['Écart entre équipes' if team_gap else '', 'Écart SAP' if sap_gap else ''])) or 'Sans écart'))
        if not rows:
            raise UserError('Aucun comptage final à clôturer.')
        title = 'Clôturé avec écarts' if has_difference else 'Clôturé sans écart'
        provenance = ('Bilan reconstitué à partir des données disponibles. Aucun bilan daté n’avait été enregistré lors de cette ancienne clôture.'
                      if historical else 'Bilan enregistré le %s (UTC), par %s. Les écarts signalés ne bloquent pas la clôture.' % (fields.Datetime.now(), escape(self.env.user.name)))
        return '<div class="o_ar_reconcile_zone"><h4>%s — %s</h4><p>%s</p><div class="table-responsive"><table class="table"><thead><tr><th>Emplacement</th><th>Référence</th><th>Lot</th><th>Comptages des équipes</th><th>Final</th><th>SAP</th><th>Écart SAP</th><th>Résultat</th></tr></thead><tbody>%s</tbody></table></div></div>' % (
            title, escape(self.resume), provenance, ''.join(rows))

    def action_close_invsession(self):
        self._lock_workflow()
        self._check_supervisor()
        pending = self.filtered(lambda z: z.state != 'Fermeture')
        reports = {zone.id: zone._build_closure_report() for zone in pending}
        result = super().action_close_invsession()
        for zone in pending:
            self.env['ir.attachment'].create({
                'name': REPORT_NAME, 'res_model': zone._name, 'res_id': zone.id,
                'type': 'binary', 'mimetype': 'text/html', 'raw': reports[zone.id].encode('utf-8'),
            })
        self.invalidate_recordset(['closure_report_html'])
        return result


class SessionClosureReport(models.Model):
    _inherit = 'araymond.maininvsession'

    closure_report_html = fields.Html(compute='_compute_closure_report', sanitize=True)

    @api.depends('sess_records.state', 'sess_records.closure_report_html')
    def _compute_closure_report(self):
        for session in self:
            session.closure_report_html = ''.join(filter(None, session.sess_records.mapped('closure_report_html'))) or False

