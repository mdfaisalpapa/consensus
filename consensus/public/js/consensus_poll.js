frappe.ui.form.on("Consensus Poll", {
    onload(frm) {
        setup_scope_picker(frm);
    },

    refresh(frm) {
        if (frm.is_new()) return;
        const roles = frappe.user_roles || [];
        if (!roles.includes("System Manager") && !roles.includes("Consensus Administrator")) return;

        setup_scope_picker(frm);
        render_participation_stats(frm);
        render_poll_results_dashboard(frm);
        setup_electorate_and_register_buttons(frm);

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

function setup_scope_picker(frm) {
    if (!frm.fields_dict.scope_selections) return;

    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Electorate Publication",
            filters: { status: "Approved" },
            fields: ["name", "title"],
            limit_page_length: 50,
        },
        callback: function (r) {
            const pubs = (r.message || []).map((p) => p.name);
            frm.fields_dict.scope_selections.grid.update_docfield_property("entity_ref", "options", pubs);
        },
    });

    if (frm.doc.status === "Draft" && frm.doc.snapshot_state !== "Complete") {
        frm.fields_dict.scope_selections.grid.add_custom_button?.(__("Select Scope Entity (Dropdown)"), () => {
            open_scope_picker_dialog(frm);
        });
    }
}

function open_scope_picker_dialog(frm) {
    const d = new frappe.ui.Dialog({
        title: __("Select Voting Scope Entity"),
        fields: [
            {
                fieldname: "scope_type",
                label: __("Provider Scope Type"),
                fieldtype: "Select",
                options: "publication\ngroup",
                default: "publication",
                change() {
                    const st = d.get_value("scope_type");
                    load_entities_for_dialog(d, st);
                },
            },
            {
                fieldname: "entity_select",
                label: __("Scope Entity"),
                fieldtype: "Select",
                reqd: 1,
                options: [],
            },
            {
                fieldname: "inclusion",
                label: __("Selection"),
                fieldtype: "Select",
                options: "Include\nExclude",
                default: "Include",
            },
        ],
        primary_action_label: __("Add to Scope"),
        primary_action(values) {
            const entity_id = values.entity_select.split(" | ")[0].trim();
            const child = frm.add_child("scope_selections");
            child.scope_type = values.scope_type;
            child.entity_ref = entity_id;
            child.inclusion = values.inclusion || "Include";
            frm.refresh_field("scope_selections");
            d.hide();
        },
    });

    load_entities_for_dialog(d, "publication");
    d.show();
}

function load_entities_for_dialog(dialog, scope_type) {
    if (scope_type === "publication") {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Electorate Publication",
                filters: { status: "Approved" },
                fields: ["name", "title"],
                limit_page_length: 50,
            },
            callback: function (r) {
                const pubs = r.message || [];
                const options = pubs.map((p) => `${p.name} | ${p.title}`);
                dialog.set_df_property("entity_select", "options", options);
                if (options.length) dialog.set_value("entity_select", options[0]);
            },
        });
    } else {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Block",
                fields: ["name", "block_name"],
                limit_page_length: 50,
            },
            callback: function (r) {
                const blocks = r.message || [];
                const options = blocks.map((b) => `${b.name} | ${b.block_name || b.name}`);
                dialog.set_df_property("entity_select", "options", options);
                if (options.length) dialog.set_value("entity_select", options[0]);
            },
        });
    }
}

frappe.ui.form.on("Consensus Poll Scope", {
    scope_type(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.scope_type === "publication") {
            frappe.call({
                method: "frappe.client.get_list",
                args: { doctype: "Electorate Publication", filters: { status: "Approved" }, fields: ["name"], limit_page_length: 50 },
                callback: function (r) {
                    frm.fields_dict.scope_selections.grid.update_docfield_property("entity_ref", "options", (r.message || []).map((p) => p.name));
                },
            });
        } else {
            frappe.call({
                method: "frappe.client.get_list",
                args: { doctype: "Block", fields: ["name"], limit_page_length: 50 },
                callback: function (r) {
                    frm.fields_dict.scope_selections.grid.update_docfield_property("entity_ref", "options", (r.message || []).map((b) => b.name));
                },
            });
        }
    },
});

function render_participation_stats(frm) {
    if (frm.doc.snapshot_state !== "Complete") return;

    const cast_count = parseInt(frm.doc.ballots_cast_count || (frm.doc.__onload && frm.doc.__onload.ballots_cast_count) || 0);
    const eligible_count = parseInt(frm.doc.eligible_voter_count || 0);
    const turnout_pct = eligible_count > 0 ? ((cast_count / eligible_count) * 100).toFixed(1) : "0.0";

    const badge_class = turnout_pct >= 50 ? "badge-success" : (turnout_pct > 0 ? "badge-primary" : "badge-secondary");
    const indicator_color = turnout_pct >= 50 ? "green" : (turnout_pct > 0 ? "blue" : "orange");

    frm.dashboard.clear_headline();
    frm.dashboard.set_headline_alert(
        `<div class="d-flex align-items-center justify-content-between flex-wrap" style="gap: 15px;">
            <div>
                <strong>🗳️ Polling Participation:</strong>
                <span class="badge ${badge_class} ml-1" style="font-size: 13px; padding: 4px 8px;">
                    ${turnout_pct}% Turnout
                </span>
            </div>
            <div>
                <strong>${cast_count}</strong> Votes Cast of <strong>${eligible_count}</strong> Eligible Voters
            </div>
            <div class="text-muted small">
                Poll Status: <strong class="text-uppercase">${frm.doc.status}</strong>
            </div>
        </div>`,
        indicator_color
    );
}

function render_poll_results_dashboard(frm) {
    if (frm.doc.status !== "Closed" && !frm.doc.results_published) return;

    frappe.call({
        method: "consensus.api.voting.public_poll_results",
        args: { poll_name: frm.doc.name },
        type: "GET",
        quiet: true,
        error: () => {},
        callback: function (r) {
            if (!r || !r.message || !r.message.visible || !r.message.results || !r.message.results.length) return;

            const results = r.message.results;
            const total_weight = results.reduce((acc, row) => acc + (parseFloat(row.total_weight) || 0), 0);
            const total_votes = results.reduce((acc, row) => acc + (parseInt(row.ballot_count) || 0), 0);

            let resultsHtml = `
                <div class="border rounded p-3 mb-3 bg-light shadow-sm">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <h5 class="mb-0 text-primary">📊 Official Poll Results & Outcome</h5>
                        <span class="badge badge-success px-2 py-1">Certified Tally</span>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-sm table-bordered bg-white mb-2">
                            <thead class="thead-light">
                                <tr>
                                    <th>Option</th>
                                    <th class="text-center">Ballots Cast</th>
                                    <th class="text-center">Total Weight</th>
                                    <th style="width: 35%;">Vote Share (%)</th>
                                </tr>
                            </thead>
                            <tbody>
            `;

            results.forEach((row) => {
                const weight = parseFloat(row.total_weight) || 0;
                const pct = total_weight > 0 ? ((weight / total_weight) * 100).toFixed(1) : 0;
                resultsHtml += `
                    <tr>
                        <td><strong>${row.option_label}</strong></td>
                        <td class="text-center">${row.ballot_count}</td>
                        <td class="text-center font-weight-bold">${weight.toFixed(3)}</td>
                        <td>
                            <div class="d-flex align-items-center">
                                <div class="progress flex-grow-1 mr-2" style="height: 14px;">
                                    <div class="progress-bar bg-primary" role="progressbar" style="width: ${pct}%;"></div>
                                </div>
                                <small class="font-weight-bold">${pct}%</small>
                            </div>
                        </td>
                    </tr>
                `;
            });

            resultsHtml += `
                            </tbody>
                        </table>
                    </div>
                    <div class="small text-muted d-flex justify-content-between">
                        <span>Total Ballots: <strong>${total_votes}</strong></span>
                        <span>Total Weighted Share: <strong>${total_weight.toFixed(3)}</strong></span>
                    </div>
                </div>
            `;

            // Display in dashboard section
            frm.dashboard.set_headline_alert(
                frm.dashboard.headline_alert ? frm.dashboard.headline_alert + resultsHtml : resultsHtml,
                "blue"
            );
        },
    });
}

function setup_electorate_and_register_buttons(frm) {
    let pub_id = frm.doc.approved_publication_id;
    if (!pub_id && frm.doc.scope_selections) {
        const pub_row = frm.doc.scope_selections.find((s) => s.scope_type === "publication" && s.entity_ref);
        if (pub_row) pub_id = pub_row.entity_ref;
    }

    if (pub_id) {
        frm.add_custom_button(__("📋 Provisional Electorate Roll"), () => {
            frappe.route_options = { publication: pub_id };
            frappe.set_route("List", "Provisional Electorate Member", "List");
        }, __("Electorate & Audit"));

        frm.add_custom_button(__("⚖️ Electorate Objections"), () => {
            frappe.route_options = { publication: pub_id };
            frappe.set_route("List", "Electorate Objection", "List");
        }, __("Electorate & Audit"));

        frm.add_custom_button(__("📜 Final Certified Roll"), () => {
            frappe.set_route("Form", "Electorate Publication", pub_id);
        }, __("Electorate & Audit"));
    }

    if (frm.doc.status === "Closed" && frm.doc.register_published) {
        frm.add_custom_button(__("🗳️ Anonymous Published Register"), () => {
            window.open(`/consensus-register?poll=${encodeURIComponent(frm.doc.name)}`, "_blank");
        }, __("Electorate & Audit"));

        frm.add_custom_button(__("📥 Download Register CSV"), () => {
            window.open(`/api/method/consensus.api.voting.download_public_register_csv?poll_name=${encodeURIComponent(frm.doc.name)}`, "_blank");
        }, __("Electorate & Audit"));

        frm.add_custom_button(__("📥 Download Register PDF"), () => {
            window.open(`/api/method/consensus.api.voting.download_public_register_pdf?poll_name=${encodeURIComponent(frm.doc.name)}`, "_blank");
        }, __("Electorate & Audit"));
    }
}
