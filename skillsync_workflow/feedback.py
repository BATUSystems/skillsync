"""Controlled channel for change requests to the owner of a layer (CORE-1140-FEEDBACK).

F-2 (this part): the trust model of the target. The `feedback:` section of a `layer.yaml` only PROPOSES where requests go (the file may come from an
untrusted repository, and a logged-in `az` can reach every organization of the user); the machine config key `feedback_targets` holds the target a human
approved per layer. Nothing is sent to an unapproved or changed target. Names in tests and docs are neutral examples.
"""
from . import layers

TRUSTED, NOT_TRUSTED, CHANGED, NO_SECTION = 'trusted', 'not-trusted', 'changed', 'no-section'


def target_entry(layer_id, section):
    """The `feedback_targets` entry a section proposes (the part a human approves: kind, organization, project)."""
    return {'layer': layer_id, 'kind': section['kind'], 'organization': section['organization'], 'project': section['project']}


def target_state(config, layer_id, section):
    """`{state, entry}` of the layer's target against the machine config: `no-section` (the layer proposes nothing), `not-trusted` (no approved entry),
    `changed` (the approved entry differs from the section: a changed organization or project after `layers update` cancels the approval), `trusted`.
    `entry` is the approved entry or None. Area and type may change without a new approval (they change neither organization nor project)."""
    if not section:
        return {'state': NO_SECTION, 'entry': None}
    approved = next((item for item in config.feedback_targets if item['layer'] == layer_id), None)
    if approved is None:
        return {'state': NOT_TRUSTED, 'entry': None}
    same = all(approved[key] == section[key] for key in ('kind', 'organization', 'project'))
    return {'state': TRUSTED if same else CHANGED, 'entry': approved}


def trust_target(config, layer_id, section, *, apply=True):
    """Approve the proposed target of a layer in the machine config (adds the entry, or replaces the one for that layer; a backup is kept).

    The caller (the `skillsync feedback trust` command) shows the target and asks the human first; this function only writes."""
    if not section:
        raise layers.LayerError(f'Layer {layer_id} has no valid `feedback:` section')
    if layer_id not in config.layers:
        raise layers.LayerError(f'Unknown layer: {layer_id}')
    return layers.edit_config(config.path, [layers.feedback_target_op(target_entry(layer_id, section))], apply=apply)
