"""Local workflow commands for skillsync."""

# Generator version (SemVer, K12): `core_min` of layers is compared with it.
__version__ = "1.0.2"


def register_subcommands(subparsers):
    from . import quality, selection, runs, environment, e2e, delivery, claims, maintenance, catalog, repomap, knowledge, status, retro, dispatch_state, prototype, leakguard, layers, retro_scan, setup_wizard, dashboard
    quality.register(subparsers)
    selection.register(subparsers)
    runs.register(subparsers)
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
