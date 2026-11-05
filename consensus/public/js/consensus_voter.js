(() => {
    const root = document.getElementById("consensus-voter");
    if (!root) return;
    const $ = (id) => document.getElementById(id);
    const state = { polls: [], poll: null, options: [], verifiedOtp: false, voted: false, key: null };
    const call = (method, args = {}) =>
        frappe.call({
            method: "consensus.api.voting." + method,
            args,
            type: ["list_my_polls", "get_poll_options"].includes(method) ? "GET" : "POST",
        }).then(r => r.message);
    const pollSelect = $("consensus-poll");
    const message = (text, good = false) => {
        const node = $("consensus-message");
        node.textContent = text;
        node.className = "alert mt-3 " + (good ? "alert-success" : "alert-warning");
        node.hidden = false;
    };
    const idempotencyKey = (poll) => {
        const storageKey = "consensus-idem:" + poll;
        let value = sessionStorage.getItem(storageKey);
        if (!value) {
            const bytes = crypto.getRandomValues(new Uint8Array(32));
            value = btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
            sessionStorage.setItem(storageKey, value);
        }
        return value;
    };
    async function loadPoll(pollName) {
        state.poll = await call("get_poll_options", { poll_name: pollName });
        state.options = state.poll.options || [];
        state.key = idempotencyKey(pollName);
        state.verifiedOtp = false;
        state.voted = false;
        const details = $("consensus-poll-details");
        details.replaceChildren();
        const title = document.createElement("h2");
        title.textContent = state.poll.title;
        const select = document.createElement("select");
        select.id = "consensus-option";
        select.className = "form-control";
        select.add(new Option("Select an option", ""));
        state.options.forEach(option => select.add(new Option(option.frozen_label, option.option_key)));
        details.append(title, select);
        $("consensus-otp").hidden = !state.poll.require_otp;
        $("consensus-cast").disabled = state.poll.status !== "Open" || state.poll.require_otp;
        $("consensus-receipt").hidden = true;
        try {
            const old = await call("recover", { poll_name: pollName, idempotency_key: state.key });
            if (old && old.receipt_code) {
                state.voted = true;
                $("consensus-cast").disabled = true;
                $("consensus-receipt").hidden = false;
                message("Your participation is recorded. You can recover the signed receipt.", true);
            }
        } catch (_) {}
    }
    pollSelect.addEventListener("change", () => loadPoll(pollSelect.value).catch(e => message(e.message)));
    $("consensus-send-otp").addEventListener("click", async () => {
        try {
            await call("send_otp", { poll_name: state.poll.poll });
            message("A verification code was sent to your registered contact.");
        } catch (e) { message(e.message); }
    });
    $("consensus-verify-otp").addEventListener("click", async () => {
        try {
            await call("verify_otp", { poll_name: state.poll.poll, code: $("consensus-otp-code").value.trim() });
            state.verifiedOtp = true;
            $("consensus-cast").disabled = false;
            message("Verification complete. You may cast your vote.", true);
        } catch (e) { message(e.message); }
    });
    $("consensus-cast").addEventListener("click", async () => {
        const option = document.getElementById("consensus-option")?.value;
        if (!option) return message("Choose an option first.");
        try {
            const result = await call("cast", {
                poll_name: state.poll.poll, option_key: option, idempotency_key: state.key,
            });
            state.voted = true;
            $("consensus-cast").disabled = true;
            $("consensus-receipt").hidden = false;
            message(result.recovered ? "Your recorded vote was recovered." : "Your vote was recorded.", true);
        } catch (e) { message(e.message); }
    });
    $("consensus-receipt").addEventListener("click", async () => {
        try {
            const response = await fetch("/api/method/consensus.api.voting.download_receipt", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token },
                body: JSON.stringify({ poll_name: state.poll.poll, idempotency_key: state.key }),
            });
            if (!response.ok) throw new Error("Signed receipt is not available yet.");
            const blob = await response.blob();
            if (blob.type !== "application/pdf") throw new Error("Server did not return a signed PDF.");
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = "Consensus-Voting-Receipt.pdf";
            anchor.click();
            URL.revokeObjectURL(url);
        } catch (e) { message(e.message + " You can retry without casting another vote."); }
    });
    if (!frappe.session || frappe.session.user === "Guest") return;
    call("list_my_polls").then(polls => {
        state.polls = polls || [];
        $("consensus-auth-message").hidden = true;
        $("consensus-controls").hidden = false;
        pollSelect.replaceChildren();
        state.polls.forEach(poll => pollSelect.add(new Option(poll.frozen_title || poll.poll, poll.poll)));
        if (state.polls.length) loadPoll(state.polls[0].poll);
        else $("consensus-poll-details").textContent = "No polls are allocated to your account.";
    }).catch(e => { $("consensus-auth-message").textContent = e.message; });
})();
