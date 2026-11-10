"""Preserve immutable SAP snapshots when restarting a scan cycle."""
from odoo import api, fields, models
from odoo.exceptions import AccessError
from .complete_flow import internal, trusted


class HistoryZone(models.Model):
    _inherit = 'araymond.invsession'

    history_sap_snapshot = fields.Json(copy=False, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self) and any('history_sap_snapshot' in values for values in vals_list):
            raise AccessError('Le rapprochement archivé est en lecture seule.')
        return super().create(vals_list)

    def write(self, values):
        if 'history_sap_snapshot' in values and not trusted(self):
            raise AccessError('Le rapprochement archivé est en lecture seule.')
        return super().write(values)


class HistorySnapshot(models.Model):
    _inherit = 'araymond.maininvsession'

    def _restart_snapshot(self, zones):
        for zone in zones:
            data = {}
            for count in zone.first_count_ids:
                data[str(count.id)] = count.get_lxo3_pivot_data() if self.file_loaded else None
            internal(zone).write({'history_sap_snapshot': data})
        return super()._restart_snapshot(zones)
