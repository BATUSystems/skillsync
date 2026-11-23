"""Local workflow commands for skillsync."""

# Generator version (SemVer, K12): `core_min` of layers is compared with it.
__version__ = "1.14.1"


def register_subcommands(subparsers):
    from . import test_run, quality, selection, runs, environment, e2e, delivery, claims, maintenance, catalog, repomap, knowledge, status, retro, dispatch_state, prototype, leakguard, layers, retro_scan, setup_wizard, dashboard, completion, feedback, agent_runs
    quality.register(subparsers)
    test_run.register(subparsers)
    selection.register(subparsers)
    runs.register(subparsers)
    agent_runs.register(subparsers)
    environment.register(subparsers)
    e2e.register(subparsers)
    delivery.register(subparsers)
    claims.register(subparsers)
    maintenance.register(subparsers)
    catalog.register(subparsers)
    repomap.register(subparsers)
    knowledge.register(subparsers)
    status.register(subparsers)
    retro.register(subparsers)
    retro_scan.register(subparsers)
    dispatch_state.register(subparsers)
    prototype.register(subparsers)
    leakguard.register(subparsers)
    layers.register(subparsers)
    setup_wizard.register(subparsers)
    dashboard.register(subparsers)
    completion.register(subparsers)
    feedback.register(subparsers)
