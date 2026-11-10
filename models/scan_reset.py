"""Redo one completed count without deleting other teams' measurements."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from .complete_flow import internal, trusted


class ResetScanZone(models.Model):
    _inherit = 'araymond.invsession'

    recheck_after_reset = fields.Boolean(default=False, readonly=True, copy=False)

    def write(self, values):
        if 'recheck_after_reset' in values and not trusted(self):
            raise AccessError('Utilisez Réinitialiser le scan.')
        return super().write(values)

    def _new_count(self, original, team, phase):
        if self.recheck_after_reset:
            existing = self.recs_scan.filtered(lambda c: c.origin_count_id == original
                and c.branch_id == team and c.count_phase == phase)
            if existing:
                return existing.sorted('count_round')[-1:]
        return super()._new_count(original, team, phase)

    def _advance_counts(self):
        result = super()._advance_counts()
        if self.recheck_after_reset:
            # Cross/third counts already completed by other teams are reused.
            for step in range(3):
                previous = self.workflow_state
                if previous not in ('cross', 'arbitration'):
                    break
                super()._advance_counts()
                if self.workflow_state == previous:
                    break
            if self.workflow_state == 'done':
                self._set_workflow({'recheck_after_reset': False})
        return result


class ResetScanCount(models.Model):
    _inherit = 'araymond.inventory'

    can_reset_scan = fields.Boolean(compute='_compute_can_reset_scan')

    @api.depends_context('uid')
    @api.depends('ok_done', 'rec_scan_id.mainsession_id.state', 'rec_scan_id.flow_archived', 'branch_id')
    def _compute_can_reset_scan(self):
        for count in self:
            session = count.rec_scan_id.mainsession_id
            count.can_reset_scan = (count.ok_done and count.rec_scan_id.is_session_scan
                and session.state == 'Scan' and not count.rec_scan_id.flow_archived
                and self.env.user in session._scan_assigned_users(count.branch_id.automatic_slot))

    def action_reset_scan(self):
        self.ensure_one()
        self.check_access('write')
        zone = self.rec_scan_id
        session = zone.mainsession_id
        session._lock_preparation()
        zone._lock_workflow()
        self.invalidate_recordset()
        if not zone.is_session_scan or session.state != 'Scan' or zone.flow_archived or not zone.statusSession:
            raise UserError('Réinitialisez un relevé de la session de scan en cours.')
        if not self.ok_done:
            raise UserError('Seul un scan terminé peut être réinitialisé.')
        if self.env.user not in session._scan_assigned_users(self.branch_id.automatic_slot):
            raise AccessError('Seuls les membres de cette équipe peuvent réinitialiser son scan.')
        original = self.origin_count_id or self
        original._set_count({'final_count_id': False, 'needs_arbitration': self.count_phase == 'arbitration',
            'resolution_needed': False, 'supervisor_choice_id': False,
            'resolved_by': False, 'resolved_at': False, 'resolution_comment': False})
        internal(session.flow_check_ids.filtered(lambda c: c.phase == 'post')).write({
            'validated_by': False, 'validated_at': False, 'validation_votes': {}})
        self._set_count({'ok_done': False, 'location_scanned': False, 'scan_first': True,
            'um': False, 'um2': False, 'dum': False, 'emplacementVide': False,
            'scan_input': False, 'scanraypro': False, 'scanlog': False, 'scanrecp': False,
            'ref': False, 'qty': False, 'lot': False, 'hu': False, 'numEtiquet': False})
        # Reopen this phase while retaining every other team's records.
        zone._set_workflow({'workflow_state': self.count_phase, 'recheck_after_reset': True})
        self.su.unlink()
        # Multiple simultaneous resets resume at the earliest unfinished phase.
        pending = zone.recs_scan.filtered(lambda c: not c.ok_done)
        phase = next(p for p in ('first', 'cross', 'arbitration') if p in pending.mapped('count_phase'))
        zone._set_workflow({'workflow_state': phase})
        zone._sync_session_scan_progress()
        session._flow_message('Scan réinitialisé : %s — %s — %s. Les étiquettes de ce relevé ont été supprimées ; les autres comptages sont conservés.' % (
            self.emplacement, self.branch_id.display_name, self.count_phase))
        return False
