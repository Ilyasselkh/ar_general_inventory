/** @odoo-module **/

import { Component, useExternalListener } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { patch } from "@web/core/utils/patch";
import { RelationalModel } from "@web/model/relational_model/relational_model";
import { registry } from "@web/core/registry";

registry.category("actions").add("ar_inventory_arbitration_notice", async (env, action) => {
    env.services.notification.add(action.params.message, {
        title: "Troisième comptage nécessaire",
        type: "warning",
        sticky: false,
        autocloseDelay: 5000,
    });
    await env.services.action.doAction({ type: "ir.actions.act_window_close" });
});

class ScanConfirmationDialog extends Component {
    static template = "ar_general_inventory.ScanConfirmationDialog";
    static components = { Dialog };
    static props = {
        barcode: String,
        title: String,
        message: String,
        close: Function,
    };

    setup() {
        this.buffer = "";
        this.lastKeyTime = 0;
        useExternalListener(document, "keydown", (event) => this.onKeydown(event), true);
    }

    onKeydown(event) {
        if (event.ctrlKey || event.altKey || event.metaKey) {
            return;
        }
        const now = Date.now();
        if (now - this.lastKeyTime > 500) {
            this.buffer = "";
        }
        this.lastKeyTime = now;

        if (event.key === "Enter" || event.key === "Tab") {
            if (this.buffer) {
                event.preventDefault();
                event.stopImmediatePropagation();
                if (this.buffer === this.props.barcode) {
                    this.props.close();
                }
            }
            this.buffer = "";
        } else if (event.key.length === 1) {
            // Keep scanner input inside the dialog; it must not reach the scan field.
            event.preventDefault();
            event.stopImmediatePropagation();
            this.buffer += event.key;
        }
    }
}

patch(RelationalModel.prototype, {
    _displayOnchangeWarning(warning) {
        if (warning.type === "ar_inventory_scan_success" && warning.barcode) {
            Promise.resolve(this.hooks.onWillDisplayOnchangeWarning(warning)).then(() => {
                this.dialog.add(ScanConfirmationDialog, {
                    barcode: warning.barcode,
                    title: warning.title,
                    message: warning.message,
                });
            });
            return;
        }
        return super._displayOnchangeWarning(warning);
    },
});
