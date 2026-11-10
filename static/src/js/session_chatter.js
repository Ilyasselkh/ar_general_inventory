/** @odoo-module **/
import { Component, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { Chatter } from "@mail/chatter/web_portal/chatter";

class InventorySessionChatter extends Component {
    static components = { Chatter };
    static props = { ...standardWidgetProps };
    static template = xml`<div class="o_inventory_session_chatter"><Chatter threadModel="props.record.resModel" threadId="props.record.resId || false"/></div>`;
}
registry.category("view_widgets").add("inventory_session_chatter", { component: InventorySessionChatter });
