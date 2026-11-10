/** @odoo-module **/
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListRenderer } from "@web/views/list/list_renderer";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";

class ScanLinesRenderer extends ListRenderer {
    setup() {
        super.setup();
        this.scanOrm = useService("orm");
        this.scanAction = useService("action");
    }
    async onCellClicked(record, column, ev, newWindow) {
        if (ev.target.special_click || ev.target.closest("button, a")) {
            return super.onCellClicked(record, column, ev, newWindow);
        }
        // Rack remains editable before scanning; all other cells open the scan.
        if (!record.resId || (column.name === "location_rack" && !record.data.ok_done && !record.data.location_scanned)) {
            return super.onCellClicked(record, column, ev, newWindow);
        }
        if (this.editedRecord && !(await this.editedRecord.save())) {
            return;
        }
        const action = await this.scanOrm.call(record.resModel, "action_open_scan_record", [[record.resId]]);
        await this.scanAction.doAction(action, { onClose: () => this.props.list.model.load() });
    }
}
class ScanLinesField extends X2ManyField {
    static components = { ...X2ManyField.components, ListRenderer: ScanLinesRenderer };
}
registry.category("fields").add("inventory_scan_lines", { ...x2ManyField, component: ScanLinesField });
