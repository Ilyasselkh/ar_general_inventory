from ..models.session_preparation import PreparedSession


def legacy_session(env, values):
    """Reproduce a row predating 3.0, without exposing a public bypass."""
    return super(PreparedSession, env['araymond.maininvsession']).create(
        dict(values, preparation_workflow=False, full_flow=False, state='Checklist'))
