frappe.ui.form.on("Consensus Poll", {
    refresh(frm) {
        if (frm.is_new()) return;
        const roles = frappe.user_roles || [];
        if (!roles.includes("System Manager") && !roles.includes("Consensus Administrator")) return;
        frm.add_custom_button(__("Manage Options"), () => {
            frappe.route_options = { poll: frm.doc.name };
            frappe.set_route("List", "Consensus Poll Option", "List");
        });
        if (frm.doc.status === "Draft" && ["Not Started", "Failed"].includes(frm.doc.snapshot_state)) {
            frm.add_custom_button(__("Build Eligibility Snapshot"), async () => {
                const response = await frappe.call({
                    method: "consensus.api.voting.build_snapshot",
                    args: { poll_name: frm.doc.name }, freeze: true,
                    freeze_message: __("Building frozen eligibility snapshot…"),
                });
                if (!response.exc) {
                    frappe.show_alert({ message: __("Snapshot completed"), indicator: "green" });
                    await frm.reload_doc();
                }
            }, __("Voting"));
        }
        if (frm.doc.status === "Draft" && frm.doc.snapshot_state === "Complete") {
            frm.add_custom_button(__("Open Poll"), async () => {
                await frappe.call({ method: "consensus.api.voting.open", args: { poll_name: frm.doc.name } });
                await frm.reload_doc();
            }, __("Voting"));
        }
        if (["Open", "Scheduled"].includes(frm.doc.status)) {
            frm.add_custom_button(__("Close Poll"), async () => {
                if (!window.confirm(__("Close this poll now?"))) return;
                await frappe.call({ method: "consensus.api.voting.close", args: { poll_name: frm.doc.name } });
                await frm.reload_doc();
            }, __("Voting"));
        }
        if (frm.doc.status === "Closed" && frm.doc.publish_anonymous_register && !frm.doc.register_published) {
            frm.add_custom_button(__("Publish Anonymous Register"), async () => {
                await frappe.call({
                    method: "consensus.api.voting.publish_ballot_register",
                    args: { poll_name: frm.doc.name },
                });
                await frm.reload_doc();
            }, __("Voting"));
        }
        if (frm.doc.status === "Closed" && frm.doc.result_visibility === "Manual" && !frm.doc.results_published) {
            frm.add_custom_button(__("Publish Results"), async () => {
                await frappe.call({
                    method: "consensus.api.voting.publish_poll_results",
                    args: { poll_name: frm.doc.name },
                });
                await frm.reload_doc();
            }, __("Voting"));
        }
    },
});
