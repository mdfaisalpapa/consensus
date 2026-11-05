app_name = "consensus"
app_title = "Consensus"
app_publisher = "Consensus Contributors"
app_description = "Reusable voting and election management for Frappe"
app_email = ""
app_license = "mit"

# Trusted installed apps register providers and secret resolvers through hooks.
consensus_voter_providers = []
consensus_secret_resolvers = []

after_install = "consensus.install.after_install"
after_migrate = ["consensus.install.ensure_database_constraints"]

scheduler_events = {
    "cron": {"*/5 * * * *": ["consensus.services.voting.close_expired_polls"]},
}

doctype_js = {
    "Consensus Poll": "public/js/consensus_poll.js",
}

# Temporary browser stabilization fixture; removed after the test run.
consensus_voter_providers = ["consensus.tests.e2e_provider.TestProvider"]
consensus_secret_resolvers = ["consensus.tests.e2e_provider.TestResolver"]
