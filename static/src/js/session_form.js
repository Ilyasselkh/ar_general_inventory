/** @odoo-module **/

import { registry } from "@web/core/registry";
import { FormCompiler } from "@web/views/form/form_compiler";
import { formView } from "@web/views/form/form_view";

class InventorySessionCompiler extends FormCompiler {
    compileNotebook(el, params) {
        const scanPageIndex = [...el.children].findIndex(
            (child) => child.getAttribute("name") === "scan_in_progress"
        );
        const notebook = super.compileNotebook(el, params);
        if (scanPageIndex === -1) {
            return notebook;
        }
        // Pages are ordered newest stage first in the XML. Select the first
        // visible stage on entry/transition, without overriding later clicks.
        const stageNames = ["scan_in_progress", "stage_inventory_day", "stage_go",
            "stage_preparation", "stage_scan_menu", "scan_chatter"];
        let defaultPage = notebook.getAttribute("defaultPage");
        for (const name of [...stageNames].reverse()) {
            const slot = [...notebook.children].find(
                (child) => child.getAttribute("name")?.includes(name)
            );
            if (slot) {
                defaultPage = `(${slot.getAttribute("isVisible")}) ? ${JSON.stringify(slot.getAttribute("t-set-slot"))} : (${defaultPage})`;
            }
        }
        notebook.setAttribute("defaultPage", defaultPage);
        return notebook;
    }
}

registry.category("views").add("ar_inventory_session_form", {
    ...formView,
    Compiler: InventorySessionCompiler,
});
