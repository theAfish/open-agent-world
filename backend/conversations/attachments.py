"""Session-scoped views of the existing immutable artifact store."""
import mimetypes

from backend.errors import PermissionDeniedError, ResourceValidationError
from .models import ConversationAttachment


def agent_session(services, conversation_id, agent_id):
    context = services._require_run_manager().current_context
    if context is None or not context.context_id or context.agent_id != agent_id:
        raise PermissionDeniedError('Conversation files require an active conversation session')
    session = services.conversations.get_session(conversation_id, context.context_id)
    services._require_session_participant(session, agent_id)
    services._require_conversation_connection(agent_id, conversation_id)
    return session.id


def visible(store, record, session_id, agent_id):
    if record.get('provenance', {}).get('session_id') != session_id:
        return False
    if record['provenance'].get('agent_id') == agent_id:
        return True
    with store.database.locked() as db:
        return db.execute('''SELECT 1 FROM conversation_messages m, json_each(m.attachments_json) a
            WHERE m.session_id=? AND json_extract(a.value, '$.version_id')=? LIMIT 1''',
            (session_id, record['version_id'])).fetchone() is not None


def resolve(services, conversation_id, session_id, references, agent_id=None):
    services.conversations.get_session(conversation_id, session_id)
    store = services.resources.artifacts
    result = []
    for ref in references:
        store.authorize(services, conversation_id, agent_id, version_id=ref.version_id)
        record = store.get(ref.version_id)
        if record['provenance'].get('session_id') != session_id:
            raise PermissionDeniedError('Attachment belongs to a different session')
        if record['state'] != 'ready':
            raise ResourceValidationError('Attachment is not available')
        entry = next((e for e in record['manifest'] if e['path'] == ref.path and not e['directory']), None)
        if entry is None:
            raise ResourceValidationError('Attachment path is not in the artifact manifest')
        result.append(ConversationAttachment(version_id=ref.version_id, path=ref.path,
            name=ref.path.rsplit('/', 1)[-1], size_bytes=entry['size'],
            media_type=mimetypes.guess_type(ref.path)[0] or 'application/octet-stream'))
    return result
