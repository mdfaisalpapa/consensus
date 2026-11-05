(() => {
    const status = document.getElementById("consensus-register-status");
    const content = document.getElementById("consensus-register-content");
    const params = new URLSearchParams(window.location.search);
    const poll = params.get("poll");
    const call = (method, args) =>
        frappe.call({ method: "consensus.api.voting." + method, args }).then(r => r.message);
    if (!poll) { status.textContent = "A poll reference is required."; return; }
    Promise.all([
        call("public_ballot_register", { poll_name: poll }),
        params.has("receipt") ? call("verify_public_receipt", {
            poll_name: poll, receipt_code: params.get("receipt"), ballot_hash: params.get("hash"),
            option: params.get("option"), weight: params.get("weight"),
        }) : Promise.resolve(null),
    ]).then(([register, verification]) => {
        status.className = "alert " + (verification ? (verification.valid ? "alert-success" : "alert-warning") : "alert-info");
        status.textContent = verification
            ? (verification.valid ? "Receipt claims match the published anonymous ballot register." : "Receipt claims do not match the published register.")
            : "Register hash: " + register.register_hash;
        const table = document.createElement("table");
        table.className = "table table-bordered";
        const head = document.createElement("tr");
        ["Receipt Code", "Selected Option", "Weight", "Ballot Hash"].forEach(label => {
            const th = document.createElement("th"); th.textContent = label; head.append(th);
        });
        table.append(head);
        (register.ballots || []).forEach(row => {
            const tr = document.createElement("tr");
            [row.receipt_code, row.selected_option_label, Number(row.voting_weight).toFixed(3), row.ballot_hash]
                .forEach(value => { const td = document.createElement("td"); td.textContent = String(value ?? ""); tr.append(td); });
            table.append(tr);
        });
        content.replaceChildren(table);
    }).catch(() => {
        status.className = "alert alert-warning";
        status.textContent = "The register is unavailable or its integrity check failed.";
    });
})();
