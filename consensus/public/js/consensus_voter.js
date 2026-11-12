(() => {
    const root = document.getElementById("consensus-voter");
    if (!root) return;

    const $ = (id) => document.getElementById(id);
    const state = {
        polls: [],
        activePoll: null,
        activeEligibility: null,
        selectedOption: null,
        verifiedOtp: false,
        key: null,
        receiptAvailable: false,
        currentTab: "active",
    };

    const call = (method, args = {}, quiet = false) =>
        frappe.call({
            method: "consensus.api.voting." + method,
            args,
            type: ["list_my_polls", "get_poll_options", "public_poll_results", "public_ballot_register"].includes(method) ? "GET" : "POST",
            quiet,
            error: quiet ? () => {} : undefined,
        }).then((r) => (r ? r.message : null));

    const message = (text, good = false) => {
        const node = $("consensus-message");
        if (!node) return;
        node.textContent = text;
        node.className = "alert " + (good ? "alert-success" : "alert-warning");
        node.hidden = false;
    };

    const clearMessage = () => {
        const node = $("consensus-message");
        if (node) node.hidden = true;
    };

    const computeSha256 = async (text) => {
        if (!window.crypto || !window.crypto.subtle) return null;
        try {
            const buffer = new TextEncoder().encode(text);
            const digest = await crypto.subtle.digest("SHA-256", buffer);
            return Array.from(new Uint8Array(digest))
                .map((b) => b.toString(16).padStart(2, "0"))
                .join("");
        } catch (e) {
            console.error("SHA-256 error:", e);
            return null;
        }
    };

    const getIdempotencyKey = (poll, eligName) => {
        const storageKey = "consensus-idem:" + poll + (eligName ? ":" + eligName : "");
        let value = sessionStorage.getItem(storageKey);
        if (!value) {
            const bytes = crypto.getRandomValues(new Uint8Array(32));
            value = btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
            sessionStorage.setItem(storageKey, value);
        }
        return value;
    };

    const getStoredKey = (poll, eligName) => {
        const storageKey = "consensus-idem:" + poll + (eligName ? ":" + eligName : "");
        return sessionStorage.getItem(storageKey);
    };

    const formatDate = (dateStr) => {
        if (!dateStr) return "N/A";
        try {
            const d = new Date(dateStr);
            return isNaN(d.getTime()) ? dateStr : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
        } catch (_) {
            return dateStr;
        }
    };

    function initTabs() {
        const tabActive = $("tab-btn-active");
        const tabClosed = $("tab-btn-closed");
        const paneActive = $("tab-pane-active");
        const paneClosed = $("tab-pane-closed");

        tabActive?.addEventListener("click", (e) => {
            e.preventDefault();
            state.currentTab = "active";
            tabActive.classList.add("active");
            tabClosed.classList.remove("active");
            paneActive.hidden = false;
            paneClosed.hidden = true;
        });

        tabClosed?.addEventListener("click", (e) => {
            e.preventDefault();
            state.currentTab = "closed";
            tabClosed.classList.add("active");
            tabActive.classList.remove("active");
            paneClosed.hidden = false;
            paneActive.hidden = true;
        });

        $("btn-back-to-polls")?.addEventListener("click", () => {
            $("consensus-poll-detail").hidden = true;
            $("polls-tabs-container").hidden = false;
            clearMessage();
            // Update browser URL without reload
            window.history.pushState({}, "", "/consensus-vote");
        });
    }

    function renderPollsList() {
        const activeContainer = $("active-polls-list");
        const closedContainer = $("closed-polls-list");
        const activeEmpty = $("active-empty-message");
        const closedEmpty = $("closed-empty-message");

        activeContainer.replaceChildren();
        closedContainer.replaceChildren();

        const activePolls = state.polls.filter((p) => p.status !== "Closed");
        const closedPolls = state.polls.filter((p) => p.status === "Closed");

        $("active-badge").textContent = activePolls.length;
        $("closed-badge").textContent = closedPolls.length;

        activeEmpty.hidden = activePolls.length > 0;
        closedEmpty.hidden = closedPolls.length > 0;

        activePolls.forEach((poll) => {
            activeContainer.appendChild(createPollCard(poll, true));
        });

        closedPolls.forEach((poll) => {
            closedContainer.appendChild(createPollCard(poll, false));
        });
    }

    function createPollCard(poll, isActive) {
        const col = document.createElement("div");
        col.className = "col-md-6 col-lg-6 mb-3";

        const isVoted = poll.eligibility_status === "Voted";
        const statusBadgeClass = poll.status === "Open" ? "badge-success bg-success text-white" : (poll.status === "Scheduled" ? "badge-info bg-info text-white" : "badge-secondary bg-secondary text-white");

        // Determine participation badge based on poll status
        let participationBadge = "";
        if (isVoted) {
            participationBadge = `<span class="badge badge-success bg-success text-white px-2 py-1 font-weight-bold">✅ Voted</span>`;
        } else if (poll.status === "Open") {
            participationBadge = `<span class="badge badge-primary bg-primary text-white px-2 py-1 font-weight-bold">🗳️ Ready to Vote</span>`;
        } else if (poll.status === "Closed") {
            participationBadge = `<span class="badge badge-warning bg-warning text-dark px-2 py-1 font-weight-bold">⏳ Not Voted</span>`;
        } else {
            participationBadge = `<span class="badge badge-secondary bg-secondary text-white px-2 py-1">${poll.status}</span>`;
        }

        let actionBtnText = "Cast Vote →";
        let actionBtnClass = "btn-primary";
        if (isVoted) {
            actionBtnText = "View Participation & Receipt →";
            actionBtnClass = "btn-outline-success";
        }
        if (!isActive) {
            actionBtnText = "View Certified Results & Audit Register →";
            actionBtnClass = "btn-outline-primary";
        }

        col.innerHTML = `
            <div class="card h-100 shadow-sm poll-card border ${isActive && !isVoted && poll.status === "Open" ? "border-primary" : ""}" style="border-radius: 10px;">
                <div class="card-body d-flex flex-column justify-content-between p-3">
                    <div>
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <span class="badge ${statusBadgeClass} text-uppercase px-2 py-1">${poll.status}</span>
                            ${participationBadge}
                        </div>
                        <h5 class="card-title text-dark font-weight-bold mb-2">
                            ${poll.frozen_title || poll.title}
                        </h5>
                        <div class="small text-muted mb-3">
                            <div class="mb-1">🏠 <strong>Unit / Flat:</strong> <span class="badge badge-light border text-dark">${poll.context_ref || "General Allocation"}</span></div>
                            <div class="mb-1">⚖️ <strong>Voting Weight:</strong> ${parseFloat(poll.voting_weight || 1.0).toFixed(3)} UDS</div>
                            <div>📅 <strong>Window:</strong> ${formatDate(poll.starts_at)} – ${formatDate(poll.ends_at)}</div>
                        </div>
                    </div>
                    <div class="pt-2 border-top">
                        <button type="button" class="btn btn-block btn-sm ${actionBtnClass} font-weight-bold open-poll-btn" data-poll="${poll.poll}" data-elig="${poll.eligibility_name}">
                            ${actionBtnText}
                        </button>
                    </div>
                </div>
            </div>
        `;

        col.querySelector(".poll-card").addEventListener("click", () => {
            openPoll(poll.poll, poll.eligibility_name);
        });

        return col;
    }

    async function openPoll(pollName, eligName) {
        clearMessage();
        $("polls-tabs-container").hidden = true;
        $("consensus-poll-detail").hidden = false;

        const content = $("consensus-poll-content");
        content.innerHTML = `<div class="text-center py-5 text-muted"><div class="spinner-border text-primary spinner-border-sm mr-2"></div>Loading poll details…</div>`;

        try {
            const pollData = await call("get_poll_options", { poll_name: pollName });
            state.activePoll = pollData;
            state.activeEligibility = state.polls.find((p) => p.poll === pollName && (!eligName || p.eligibility_name === eligName)) || pollData.my_eligibility || null;

            const existingKey = state.activeEligibility ? getStoredKey(pollName, state.activeEligibility.name) : null;
            state.key = existingKey || (state.activeEligibility ? getIdempotencyKey(pollName, state.activeEligibility.name) : null);
            state.selectedOption = null;
            state.verifiedOtp = false;

            const isVoted = state.activeEligibility && (state.activeEligibility.status === "Voted" || state.activeEligibility.eligibility_status === "Voted");

            // Check if receipt is actually available in local session
            state.receiptAvailable = false;
            if (isVoted && existingKey && state.activeEligibility) {
                try {
                    const rec = await call("recover", { poll_name: pollName, idempotency_key: existingKey, eligibility_name: state.activeEligibility.name }, true);
                    if (rec && rec.receipt_code) {
                        state.receiptAvailable = true;
                    }
                } catch (_) {
                    state.receiptAvailable = false;
                }
            }

            // Update browser URL query param cleanly
            window.history.pushState({}, "", `/consensus-vote?poll=${encodeURIComponent(pollName)}`);

            renderPollDetail();
        } catch (err) {
            content.innerHTML = `<div class="alert alert-warning">${err.message}</div><button type="button" class="btn btn-outline-secondary btn-sm" onclick="location.href='/consensus-vote'">← Back to Polls List</button>`;
        }
    }

    function renderPollDetail() {
        const poll = state.activePoll;
        const elig = state.activeEligibility;
        const isVoted = elig?.status === "Voted" || elig?.eligibility_status === "Voted";
        const content = $("consensus-poll-content");

        const statusBadgeClass = poll.status === "Open" ? "badge-success" : (poll.status === "Scheduled" ? "badge-info" : "badge-secondary");

        let detailParticipationBadge = "";
        if (!elig) {
            detailParticipationBadge = `<span class="badge badge-secondary px-3 py-2 font-weight-bold" style="font-size: 14px;">Auditor View</span>`;
        } else if (isVoted) {
            detailParticipationBadge = `<span class="badge badge-success px-3 py-2 font-weight-bold" style="font-size: 14px;">✅ Participation Recorded</span>`;
        } else if (poll.status === "Open") {
            detailParticipationBadge = `<span class="badge badge-primary px-3 py-2 font-weight-bold" style="font-size: 14px;">🗳️ Ready to Vote</span>`;
        } else if (poll.status === "Closed") {
            detailParticipationBadge = `<span class="badge badge-warning text-dark px-3 py-2 font-weight-bold" style="font-size: 14px;">⏳ Did Not Vote</span>`;
        } else {
            detailParticipationBadge = `<span class="badge badge-secondary px-3 py-2 font-weight-bold" style="font-size: 14px;">${poll.status}</span>`;
        }

        let html = `
            <!-- Poll Header Card -->
            <div class="card shadow-sm border mb-4">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start flex-wrap mb-2">
                        <div>
                            <span class="badge ${statusBadgeClass} text-uppercase px-2 py-1 mb-2">${poll.status}</span>
                            <h2 class="h4 font-weight-bold text-primary mb-1">${poll.title}</h2>
                        </div>
                        <div class="text-right">
                            ${detailParticipationBadge}
                        </div>
                    </div>

                    ${poll.description ? `<div class="text-muted small mb-3 border-left pl-3 py-1 bg-light">${poll.description}</div>` : ""}

                    <!-- Allocation Info Row -->
                    <div class="row bg-light rounded p-2 mx-0 mb-3 border small">
                        <div class="col-sm-4 mb-1 mb-sm-0">
                            <strong>🏠 Allocated Unit:</strong> ${elig ? (elig.context_ref || "General") : "N/A (Auditor View)"}
                        </div>
                        <div class="col-sm-4 mb-1 mb-sm-0">
                            <strong>⚖️ Voting Weight:</strong> ${elig ? (parseFloat(elig.voting_weight || 1.0).toFixed(3) + " UDS") : "N/A"}
                        </div>
                        <div class="col-sm-4">
                            <strong>📅 Closes On:</strong> ${formatDate(poll.ends_at)}
                        </div>
                    </div>

                    <!-- Turnout Banner -->
                    <div class="alert alert-light border small d-flex justify-content-between align-items-center mb-0">
                        <div>
                            <strong>🗳️ Turnout:</strong>
                            <span class="badge badge-info ml-1 font-weight-bold">${poll.turnout_percentage}%</span>
                        </div>
                        <div>
                            <strong>${poll.ballots_cast_count}</strong> Votes Cast of <strong>${poll.eligible_voter_count}</strong> Total Eligible Voters
                        </div>
                    </div>
                </div>
            </div>
        `;

        // If viewing without voter allocation
        if (!elig) {
            html += `
                <div class="alert alert-light border text-muted small mb-4">
                    ℹ️ <strong>Auditor / Public View:</strong> Your account is not allocated to cast a vote in this poll. You are viewing the certified poll details, outcome, and published audit register.
                </div>
            `;
        } else if (poll.status === "Closed" && !isVoted) {
            html += `
                <div class="alert alert-light border text-muted small mb-4">
                    ⏳ <strong>Poll Closed:</strong> This poll has concluded. No participation was recorded for your account in this election.
                </div>
            `;
        }

        // Voting Options Card (Only if poll is Open and voter has NOT voted yet)
        if (poll.status === "Open" && !isVoted) {
            html += `
                <div class="card shadow-sm border mb-4" id="voting-action-box">
                    <div class="card-header bg-white font-weight-bold">
                        <h5>Select an Option & Cast Ballot</h5>
                    </div>
                    <div class="card-body">
                        <div id="option-cards-container" class="mb-3">
                            ${poll.options.map((opt) => `
                                <div class="option-select-card" data-key="${opt.option_key}">
                                    <div class="d-flex align-items-center">
                                        <input type="radio" name="poll_option" id="opt_${opt.option_key}" value="${opt.option_key}" class="mr-2">
                                        <label for="opt_${opt.option_key}" class="mb-0 font-weight-bold flex-grow-1" style="cursor: pointer;">
                                            ${opt.frozen_label || opt.option_label}
                                        </label>
                                    </div>
                                    ${opt.description ? `<div class="small text-muted ml-4 mt-1">${opt.description}</div>` : ""}
                                </div>
                            `).join("")}
                        </div>

                        ${poll.require_otp ? `
                            <div class="bg-light p-3 border rounded mb-3" id="otp-section">
                                <h6 class="font-weight-bold mb-2">Two-Factor Authentication Required</h6>
                                <p class="small text-muted mb-2">A verification code must be delivered to your registered resident email/phone before casting.</p>
                                <div class="form-inline mb-2">
                                    <button type="button" id="btn-send-otp" class="btn btn-outline-primary btn-sm mr-2">Send Verification Code</button>
                                    <input type="text" id="otp-input" class="form-control form-control-sm mr-2" placeholder="Enter 6-digit code" inputmode="numeric" style="max-width: 160px;">
                                    <button type="button" id="btn-verify-otp" class="btn btn-secondary btn-sm">Verify Code</button>
                                </div>
                                <div id="otp-status" class="small"></div>
                            </div>
                        ` : ""}

                        <button type="button" id="btn-submit-vote" class="btn btn-primary btn-block font-weight-bold" ${poll.require_otp ? "disabled" : ""}>
                            Review and Cast Anonymous Vote
                        </button>
                    </div>
                </div>
            `;
        }

        // Already Voted & Download Receipt Section
        if (isVoted) {
            html += `
                <div class="card shadow-sm border mb-4 border-success">
                    <div class="card-body">
                        <div class="d-flex align-items-center mb-3">
                            <span class="mr-2" style="font-size: 24px;">✅</span>
                            <div>
                                <h5 class="mb-0 text-success font-weight-bold">Your Participation Has Been Recorded</h5>
                                <div class="small text-muted">Your vote is securely and anonymously logged into the cryptographic ballot register.</div>
                            </div>
                        </div>

                        ${state.receiptAvailable ? `
                            <!-- Receipt available in this device session -->
                            <div class="mb-3">
                                <button type="button" id="btn-download-receipt-live" class="btn btn-outline-success font-weight-bold">
                                    📥 Download Cryptographically Signed Receipt (PDF)
                                </button>
                            </div>
                            <div class="alert alert-info border small mb-0">
                                <strong>ℹ️ Signed Receipt Verified in Device Session:</strong><br>
                                To guarantee 100% voter privacy, the application database contains zero relational links or foreign keys connecting your voter identity to your cast ballot. Your signed receipt and verification keys are held in your local browser session.
                                <br><br>
                                <strong>Receipt download is available only from this voter device (while browser session storage is preserved) and cannot be retrieved or regenerated from the Application Server subsequently.</strong>
                                Please download and save your digitally signed receipt now for your records.
                            </div>
                        ` : `
                            <!-- Receipt NOT available in this device session/cache -->
                            <div class="alert alert-secondary border small mb-0">
                                <strong>⚠️ Receipt Not Found in Browser / Device Cache:</strong><br>
                                As per privacy design, no voter-to-ballot link is maintained on the application server. Your signed receipt was stored solely in the local browser session where the vote was originally cast.
                                <br><br>
                                <strong>Because this session is not present on this device (due to cache clearing, private window closure, or accessing from a different device), the receipt cannot be retrieved or regenerated from the Application Server.</strong>
                            </div>
                        `}
                    </div>
                </div>
            `;
        }

        // Section: Associated Electorate & Voter Roll (Single unified button taking to publication with back link to poll)
        if (poll.approved_publication_id || poll.publication_info) {
            const pub = poll.publication_info || {};
            const pubId = poll.approved_publication_id || pub.name;
            html += `
                <div class="card shadow-sm border mb-4">
                    <div class="card-header bg-light d-flex justify-content-between align-items-center">
                        <h5 class="mb-0 text-dark font-weight-bold">📋 Associated Electorate Roll & Certification</h5>
                        <span class="badge badge-info px-2 py-1">${pub.status || "Approved"}</span>
                    </div>
                    <div class="card-body">
                        <div class="d-flex justify-content-between align-items-start flex-wrap mb-2">
                            <div>
                                <h6 class="font-weight-bold mb-1">${pub.title || pubId}</h6>
                                <div class="small text-muted mb-2">
                                    Publication Identifier: <code>${pubId}</code>
                                    ${pub.published_on ? ` • Published On: ${formatDate(pub.published_on)}` : ""}
                                    ${pub.approval_certified_on ? ` • Certified: ${formatDate(pub.approval_certified_on)}` : ""}
                                </div>
                            </div>
                        </div>

                        ${pub.electorate_content_hash ? `
                            <div class="bg-light p-2 rounded border small mb-3">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <strong>Certified Electorate Hash (SHA-256):</strong>
                                    <button type="button" class="btn btn-link btn-xs p-0 text-primary font-weight-bold" id="btn-toggle-verify-electorate">
                                        🔍 How to verify in online engine
                                    </button>
                                </div>
                                <code class="voting-receipt-code text-dark">${pub.electorate_content_hash}</code>
                                <div id="electorate-verify-box" class="mt-2 p-2 bg-white border rounded" hidden>
                                    <p class="small text-muted mb-2">
                                        <strong>Verify Electorate Roll in Public Online Tool:</strong><br>
                                        The approved electorate roll is sealed using a canonical JSON manifest. To verify without any Python code:
                                    </p>
                                    <ol class="small text-muted pl-3 mb-2">
                                        <li>Click the button below to view the certified publication manifest and records.</li>
                                        <li>Open an ad-free, open-source online hash engine such as CyberChef (by GCHQ): <a href="https://gchq.github.io/CyberChef/#recipe=SHA2(256,64)" target="_blank" rel="noopener" class="font-weight-bold">CyberChef (GCHQ) ↗</a>.</li>
                                        <li>Paste the canonical roll data into the tool to observe that the generated SHA-256 matches the certification hash above.</li>
                                    </ol>
                                </div>
                            </div>
                        ` : ""}

                        <div>
                            <a href="/resident/voting/provisional-electorate?publication=${encodeURIComponent(pubId)}&poll=${encodeURIComponent(poll.poll)}" class="btn btn-outline-primary btn-sm font-weight-bold">
                                📋 View Electorate Roll, Objections & Certified Publication →
                            </a>
                        </div>
                    </div>
                </div>
            `;
        }

        // Section: Official Certified Poll Results (For Closed Polls)
        if (poll.status === "Closed") {
            html += `
                <div class="card shadow-sm border mb-4" id="poll-results-card">
                    <div class="card-header bg-light d-flex justify-content-between align-items-center">
                        <h5 class="mb-0 text-primary font-weight-bold">📊 Official Poll Results & Outcome</h5>
                        <span class="badge badge-success px-2 py-1">Certified Outcome</span>
                    </div>
                    <div class="card-body" id="poll-results-body">
                        <div class="text-center py-3 text-muted"><div class="spinner-border text-primary spinner-border-sm mr-2"></div>Loading certified results…</div>
                    </div>
                </div>
            `;
        }

        // Section: Official Published Anonymous Register (For Closed Polls with register_published)
        if (poll.status === "Closed" && poll.register_published) {
            html += `
                <div class="card shadow-sm border mb-4" id="published-register-card">
                    <div class="card-header bg-light d-flex justify-content-between align-items-center">
                        <h5 class="mb-0 text-dark font-weight-bold">🗳️ Published Anonymous Ballot Register</h5>
                        <span class="badge badge-success px-2 py-1">Cryptographically Sealed</span>
                    </div>
                    <div class="card-body" id="published-register-body">
                        <div class="text-center py-3 text-muted"><div class="spinner-border text-primary spinner-border-sm mr-2"></div>Loading published register…</div>
                    </div>
                </div>
            `;
        }

        content.innerHTML = html;

        // Attach event handlers
        attachDetailHandlers(poll, isVoted);

        // Fetch results if poll is Closed
        if (poll.status === "Closed") {
            loadResults(poll.poll);
        }

        // Fetch register if published
        if (poll.status === "Closed" && poll.register_published) {
            loadRegister(poll.poll);
        }
    }

    function attachDetailHandlers(poll, isVoted) {
        // Option selection
        const optionCards = document.querySelectorAll(".option-select-card");
        optionCards.forEach((card) => {
            card.addEventListener("click", () => {
                optionCards.forEach((c) => c.classList.remove("selected"));
                card.classList.add("selected");
                const radio = card.querySelector("input[type=radio]");
                if (radio) {
                    radio.checked = true;
                    state.selectedOption = radio.value;
                }
            });
        });

        // OTP handlers
        const sendOtpBtn = $("btn-send-otp");
        if (sendOtpBtn) {
            sendOtpBtn.addEventListener("click", async () => {
                const statusNode = $("otp-status");
                statusNode.textContent = "Sending verification code…";
                statusNode.className = "small text-muted mt-1";
                try {
                    await call("send_otp", { poll_name: poll.poll, eligibility_name: state.activeEligibility?.name });
                    statusNode.textContent = "✅ Verification code sent to your registered contact.";
                    statusNode.className = "small text-success mt-1";
                } catch (e) {
                    statusNode.textContent = "❌ " + e.message;
                    statusNode.className = "small text-danger mt-1";
                }
            });
        }

        const verifyOtpBtn = $("btn-verify-otp");
        if (verifyOtpBtn) {
            verifyOtpBtn.addEventListener("click", async () => {
                const code = $("otp-input")?.value?.trim();
                const statusNode = $("otp-status");
                if (!code) {
                    statusNode.textContent = "Please enter the verification code.";
                    statusNode.className = "small text-warning mt-1";
                    return;
                }
                statusNode.textContent = "Verifying…";
                statusNode.className = "small text-muted mt-1";
                try {
                    await call("verify_otp", { poll_name: poll.poll, code, eligibility_name: state.activeEligibility?.name });
                    state.verifiedOtp = true;
                    statusNode.textContent = "✅ Verification complete. You may now submit your vote.";
                    statusNode.className = "small text-success mt-1";
                    $("btn-submit-vote").disabled = false;
                } catch (e) {
                    statusNode.textContent = "❌ " + e.message;
                    statusNode.className = "small text-danger mt-1";
                }
            });
        }

        // Cast vote
        const submitBtn = $("btn-submit-vote");
        if (submitBtn) {
            submitBtn.addEventListener("click", async () => {
                if (!state.selectedOption) {
                    message("Please select an option before casting your ballot.");
                    return;
                }
                if (poll.require_otp && !state.verifiedOtp) {
                    message("Please verify your identity using the verification code first.");
                    return;
                }

                const selectedLabel = poll.options.find((o) => o.option_key === state.selectedOption)?.frozen_label || state.selectedOption;
                const confirmed = window.confirm(`Confirm your vote for:\n\n"${selectedLabel}"\n\nOnce cast, your vote is recorded anonymously in the sealed register and cannot be changed. Proceed?`);
                if (!confirmed) return;

                submitBtn.disabled = true;
                submitBtn.textContent = "Submitting Anonymous Ballot…";

                try {
                    const result = await call("cast", {
                        poll_name: poll.poll,
                        option_key: state.selectedOption,
                        idempotency_key: state.key,
                        eligibility_name: state.activeEligibility?.name,
                    });

                    // Update local state
                    if (state.activeEligibility) {
                        state.activeEligibility.status = "Voted";
                        state.activeEligibility.eligibility_status = "Voted";
                    }
                    const storedPoll = state.polls.find((p) => p.poll === poll.poll && p.eligibility_name === state.activeEligibility?.name);
                    if (storedPoll) {
                        storedPoll.status = "Voted";
                        storedPoll.eligibility_status = "Voted";
                    }

                    state.receiptAvailable = true;
                    message("Your anonymous vote has been recorded successfully. You can download your signed receipt below.", true);
                    renderPollDetail();
                } catch (e) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = "Review and Cast Anonymous Vote";
                    message(e.message);
                }
            });
        }

        // Electorate verify toggle
        $("btn-toggle-verify-electorate")?.addEventListener("click", () => {
            const box = $("electorate-verify-box");
            if (box) box.hidden = !box.hidden;
        });

        // Receipt download button
        const receiptBtn = $("btn-download-receipt-live");
        if (receiptBtn) {
            receiptBtn.addEventListener("click", () => downloadReceiptPdf(poll.poll, state.key, state.activeEligibility?.name));
        }
    }

    async function downloadReceiptPdf(pollName, key, eligName) {
        try {
            const response = await fetch("/api/method/consensus.api.voting.download_receipt", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": frappe.csrf_token },
                body: JSON.stringify({ poll_name: pollName, idempotency_key: key, eligibility_name: eligName }),
            });
            if (!response.ok) throw new Error("Receipt is not available or session expired.");
            const blob = await response.blob();
            if (blob.type !== "application/pdf") throw new Error("Server did not return a valid signed PDF receipt.");
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `Consensus-Receipt-${pollName}.pdf`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) {
            message(e.message + " Notice: Receipts cannot be retrieved if browser session was cleared.");
        }
    }

    function loadResults(pollName) {
        const body = $("poll-results-body");
        if (!body) return;

        frappe.call({
            method: "consensus.api.voting.public_poll_results",
            type: "GET",
            args: { poll_name: pollName },
            quiet: true,
            error: () => {
                body.innerHTML = `<div class="text-muted small">Official results are being certified and will be visible shortly.</div>`;
            },
            callback: function (r) {
                if (r && r.message && r.message.visible && r.message.results && r.message.results.length > 0) {
                    const results = r.message.results;
                    const totalWeight = results.reduce((acc, row) => acc + (parseFloat(row.total_weight) || 0), 0);
                    const totalVotes = results.reduce((acc, row) => acc + (parseInt(row.ballot_count) || 0), 0);

                    let html = `
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
                        const pct = totalWeight > 0 ? ((weight / totalWeight) * 100).toFixed(1) : 0;
                        html += `
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

                    html += `
                                </tbody>
                            </table>
                        </div>
                        <div class="small text-muted d-flex justify-content-between">
                            <span>Total Ballots Cast: <strong>${totalVotes}</strong></span>
                            <span>Total Weighted Share: <strong>${totalWeight.toFixed(3)}</strong></span>
                        </div>
                    `;

                    body.innerHTML = html;
                } else {
                    body.innerHTML = `<div class="text-muted small">Official results are being certified and will be visible shortly.</div>`;
                }
            },
        });
    }

    function loadRegister(pollName) {
        const body = $("published-register-body");
        if (!body) return;

        frappe.call({
            method: "consensus.api.voting.public_ballot_register",
            type: "GET",
            args: { poll_name: pollName },
            quiet: true,
            error: () => {
                body.innerHTML = `<div class="text-muted small">Published register is not currently available.</div>`;
            },
            callback: function (r) {
                if (r && r.message && r.message.ballots) {
                    const data = r.message;
                    const ballots = data.ballots || [];

                    let html = `
                        <div class="mb-3">
                            <div class="bg-light p-2 rounded border small mb-3">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <strong>Register Integrity SHA-256 Hash:</strong>
                                    <button type="button" class="btn btn-link btn-xs p-0 text-primary font-weight-bold" id="btn-toggle-verify-register">
                                        🔍 Verify Hash (Instant In-Browser & CyberChef)
                                    </button>
                                </div>
                                <code class="voting-receipt-code text-dark font-weight-bold">${data.register_hash}</code>
                                <div id="register-verify-box" class="mt-2 p-2 bg-white border rounded" hidden>
                                    <p class="small text-muted mb-2">
                                        <strong>Verify Register Integrity (Ad-Free & Native In-Browser Check):</strong><br>
                                        1. Click <strong>Copy Register Text</strong> below.<br>
                                        2. Open <a href="https://gchq.github.io/CyberChef/#recipe=SHA2(256,64)" target="_blank" rel="noopener" class="font-weight-bold">CyberChef (by GCHQ) ↗</a> (a reputed, 100% ad-free, open-source tool).<br>
                                        3. Paste into the input area. The computed SHA-256 will match the Register Hash exactly.
                                    </p>
                                    <div id="register-local-verify-result" class="alert alert-success py-1 px-2 small mb-2" hidden></div>
                                    <textarea id="register-canonical-textarea" class="form-control form-control-sm mb-2" rows="3" readonly style="font-family: monospace; font-size: 11px;"></textarea>
                                    <div class="d-flex align-items-center" style="gap: 10px;">
                                        <button type="button" id="btn-copy-register-text" class="btn btn-sm btn-primary">📋 Copy Register Text</button>
                                        <a href="https://gchq.github.io/CyberChef/#recipe=SHA2(256,64)" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary">
                                            🌐 Open CyberChef (Ad-Free) ↗
                                        </a>
                                        <span id="copy-register-status" class="small text-success"></span>
                                    </div>
                                </div>
                            </div>
                            <div class="d-flex flex-wrap mb-3" style="gap: 10px;">
                                <a href="/api/method/consensus.api.voting.download_public_register_pdf?poll_name=${encodeURIComponent(pollName)}" class="btn btn-outline-danger btn-sm font-weight-bold" target="_blank">
                                    📥 Download Register PDF (Digitally Signed)
                                </a>
                                <a href="/api/method/consensus.api.voting.download_public_register_csv?poll_name=${encodeURIComponent(pollName)}" class="btn btn-outline-success btn-sm font-weight-bold" target="_blank">
                                    📥 Download Register CSV
                                </a>
                            </div>
                        </div>

                        <!-- Interactive Ballot Hash Verification Box -->
                        <div id="ballot-verify-modal-box" class="alert alert-info border mb-3 p-3" hidden>
                            <div class="d-flex justify-content-between align-items-center mb-1">
                                <h6 class="font-weight-bold mb-0 text-primary">🔍 Verify Ballot Hash (Instant In-Browser & CyberChef)</h6>
                                <button type="button" class="close" id="btn-close-ballot-verify" style="font-size: 20px;">&times;</button>
                            </div>
                            <div class="small text-muted mb-2">
                                Receipt Code: <code id="verify-modal-receipt-code" class="text-primary font-weight-bold"></code> • Expected Ballot Hash: <code id="verify-modal-expected-hash" class="small text-dark font-weight-bold"></code>
                            </div>
                            <p class="small text-dark mb-2">
                                <strong>Instructions:</strong> Click <strong>Copy Text</strong> below, then open <a href="https://gchq.github.io/CyberChef/#recipe=SHA2(256,64)" target="_blank" rel="noopener" class="font-weight-bold">CyberChef (GCHQ) ↗</a> (a reputed, 100% ad-free tool) and paste into the input area. The resulting hash will match the Ballot Hash character-for-character.
                            </p>
                            <div id="ballot-local-verify-result" class="alert alert-success py-1 px-2 small mb-2" hidden></div>
                            <textarea id="ballot-canonical-textarea" class="form-control form-control-sm mb-2" rows="2" readonly style="font-family: monospace; font-size: 11px;"></textarea>
                            <div class="d-flex align-items-center" style="gap: 10px;">
                                <button type="button" id="btn-copy-ballot-text" class="btn btn-sm btn-primary">📋 Copy Text</button>
                                <a href="https://gchq.github.io/CyberChef/#recipe=SHA2(256,64)" target="_blank" rel="noopener" class="btn btn-sm btn-outline-secondary">
                                    🌐 Open CyberChef (Ad-Free) ↗
                                </a>
                                <span id="copy-ballot-status" class="small text-success"></span>
                            </div>
                        </div>

                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <h6 class="font-weight-bold mb-0">Anonymous Ballot Entries (${ballots.length})</h6>
                            <input type="text" id="filter-ballots-input" class="form-control form-control-sm" placeholder="Search Receipt Code..." style="max-width: 220px;">
                        </div>

                        <div class="table-responsive" style="max-height: 380px; overflow-y: auto;">
                            <table class="table table-sm table-bordered bg-white mb-0" id="ballots-table">
                                <thead class="thead-light" style="position: sticky; top: 0;">
                                    <tr>
                                        <th>Receipt Code</th>
                                        <th>Selected Option</th>
                                        <th class="text-center">Weight</th>
                                        <th>Ballot Hash (SHA-256)</th>
                                        <th class="text-center" style="width: 85px;">Verify</th>
                                    </tr>
                                </thead>
                                <tbody id="ballots-table-body">
                    `;

                    ballots.forEach((b) => {
                        html += `
                            <tr>
                                <td><code class="voting-receipt-code text-primary font-weight-bold">${b.receipt_code}</code></td>
                                <td>${b.selected_option_label}</td>
                                <td class="text-center font-weight-bold">${parseFloat(b.voting_weight || 0).toFixed(3)}</td>
                                <td><code class="voting-receipt-code small text-muted">${b.ballot_hash}</code></td>
                                <td class="text-center">
                                    <button type="button" class="btn btn-outline-info btn-xs py-0 px-2 btn-verify-ballot" data-code="${b.receipt_code}">
                                        🔍 Verify
                                    </button>
                                </td>
                            </tr>
                        `;
                    });

                    html += `
                                </tbody>
                            </table>
                        </div>
                    `;

                    body.innerHTML = html;

                    // Toggle register verification box
                    $("btn-toggle-verify-register")?.addEventListener("click", async () => {
                        const box = $("register-verify-box");
                        if (!box) return;
                        box.hidden = !box.hidden;
                        if (!box.hidden && !$("register-canonical-textarea").value) {
                            $("register-canonical-textarea").value = "Loading canonical register payload…";
                            try {
                                const res = await call("get_register_canonical_text", { poll_name: pollName });
                                if (res && res.canonical_text) {
                                    $("register-canonical-textarea").value = res.canonical_text;
                                    const localHash = await computeSha256(res.canonical_text);
                                    const resultNode = $("register-local-verify-result");
                                    if (resultNode && localHash) {
                                        resultNode.hidden = false;
                                        if (localHash.toLowerCase() === (data.register_hash || "").toLowerCase()) {
                                            resultNode.className = "alert alert-success py-1 px-2 small mb-2";
                                            resultNode.innerHTML = `✅ <strong>Native Browser SHA-256:</strong> Computed hash (<code>${localHash}</code>) matches the certified register hash character-for-character!`;
                                        } else {
                                            resultNode.className = "alert alert-danger py-1 px-2 small mb-2";
                                            resultNode.innerHTML = `❌ <strong>Hash Mismatch:</strong> Computed <code>${localHash}</code> vs certified <code>${data.register_hash}</code>`;
                                        }
                                    }
                                }
                            } catch (e) {
                                $("register-canonical-textarea").value = e.message;
                            }
                        }
                    });

                    // Copy register text button
                    $("btn-copy-register-text")?.addEventListener("click", () => {
                        const text = $("register-canonical-textarea")?.value || "";
                        navigator.clipboard.writeText(text).then(() => {
                            $("copy-register-status").textContent = "✅ Copied to clipboard!";
                            setTimeout(() => { $("copy-register-status").textContent = ""; }, 3000);
                        });
                    });

                    // Individual ballot verification buttons
                    document.querySelectorAll(".btn-verify-ballot").forEach((btn) => {
                        btn.addEventListener("click", async () => {
                            const code = btn.dataset.code;
                            const modal = $("ballot-verify-modal-box");
                            if (!modal) return;
                            modal.hidden = false;
                            $("verify-modal-receipt-code").textContent = code;
                            $("verify-modal-expected-hash").textContent = "Loading…";
                            $("ballot-canonical-textarea").value = "Fetching canonical ballot string…";
                            $("copy-ballot-status").textContent = "";
                            const resultNode = $("ballot-local-verify-result");
                            if (resultNode) resultNode.hidden = true;
                            modal.scrollIntoView({ behavior: "smooth" });

                            try {
                                const res = await call("get_ballot_canonical_text", { poll_name: pollName, receipt_code: code });
                                if (res && res.canonical_text) {
                                    $("verify-modal-expected-hash").textContent = res.ballot_hash;
                                    $("ballot-canonical-textarea").value = res.canonical_text;
                                    const localHash = await computeSha256(res.canonical_text);
                                    if (resultNode && localHash) {
                                        resultNode.hidden = false;
                                        if (localHash.toLowerCase() === (res.ballot_hash || "").toLowerCase()) {
                                            resultNode.className = "alert alert-success py-1 px-2 small mb-2";
                                            resultNode.innerHTML = `✅ <strong>Native Browser SHA-256:</strong> Computed hash (<code>${localHash}</code>) matches the ballot hash character-for-character!`;
                                        } else {
                                            resultNode.className = "alert alert-danger py-1 px-2 small mb-2";
                                            resultNode.innerHTML = `❌ <strong>Hash Mismatch:</strong> Computed <code>${localHash}</code> vs expected <code>${res.ballot_hash}</code>`;
                                        }
                                    }
                                }
                            } catch (e) {
                                $("ballot-canonical-textarea").value = e.message;
                            }
                        });
                    });

                    $("btn-close-ballot-verify")?.addEventListener("click", () => {
                        $("ballot-verify-modal-box").hidden = true;
                    });

                    $("btn-copy-ballot-text")?.addEventListener("click", () => {
                        const text = $("ballot-canonical-textarea")?.value || "";
                        navigator.clipboard.writeText(text).then(() => {
                            $("copy-ballot-status").textContent = "✅ Copied!";
                            setTimeout(() => { $("copy-ballot-status").textContent = ""; }, 3000);
                        });
                    });

                    // Attach search filter
                    const searchInput = $("filter-ballots-input");
                    const tableBody = $("ballots-table-body");
                    if (searchInput && tableBody) {
                        searchInput.addEventListener("input", (e) => {
                            const term = e.target.value.toLowerCase().trim();
                            const rows = tableBody.querySelectorAll("tr");
                            rows.forEach((row) => {
                                const text = row.textContent.toLowerCase();
                                row.style.display = text.includes(term) ? "" : "none";
                            });
                        });
                    }
                } else {
                    body.innerHTML = `<div class="text-muted small">No anonymous ballots are published for this register.</div>`;
                }
            },
        });
    }

    // Initialize on page load
    async function init() {
        if (!frappe.session || frappe.session.user === "Guest") {
            $("consensus-loading").hidden = true;
            $("consensus-auth-message").hidden = false;
            return;
        }

        initTabs();

        try {
            const polls = await call("list_my_polls");
            state.polls = polls || [];
            $("consensus-loading").hidden = true;
            $("polls-tabs-container").hidden = false;

            renderPollsList();

            // Check if URL has a ?poll= parameter to open directly
            const urlParams = new URLSearchParams(window.location.search);
            const targetPoll = urlParams.get("poll");
            if (targetPoll) {
                const found = state.polls.find((p) => p.poll === targetPoll);
                if (found) {
                    openPoll(found.poll, found.eligibility_name);
                } else {
                    openPoll(targetPoll, null);
                }
            }
        } catch (err) {
            $("consensus-loading").hidden = true;
            $("consensus-auth-message").textContent = err.message || "Failed to load voting allocations.";
            $("consensus-auth-message").hidden = false;
        }
    }

    init();
})();
