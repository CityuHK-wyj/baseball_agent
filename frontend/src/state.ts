import { isKnownEvent, parseEvent, type AgentEvent, type Answer, type Artifact, type Clarification } from './protocol';

export type ThreadMessage = { id: string; role: 'user' | 'assistant'; text: string; sealed?: boolean };
export type State = { seen: Set<string>; messages: ThreadMessage[]; artifacts: Record<string, Artifact>; answers: Record<string, Answer>; clarifications: Record<string, Clarification>; status: string; error: string; terminalRuns: Set<string>; lastIds: Record<string, string> };
export const initialState = (): State => ({ seen: new Set(), messages: [], artifacts: {}, answers: {}, clarifications: {}, status: '', error: '', terminalRuns: new Set(), lastIds: {} });
export function reduce(state: State, event: AgentEvent): State {
  if (state.seen.has(event.id)) return state;
  const next: State = { ...state, seen: new Set(state.seen).add(event.id), messages: [...state.messages], artifacts: { ...state.artifacts }, answers: { ...state.answers }, clarifications: { ...state.clarifications }, terminalRuns: new Set(state.terminalRuns), lastIds: { ...state.lastIds, [event.run_id]: event.id } };
  if (!isKnownEvent(event)) return next;
  switch (event.type) {
    case 'message.accepted': next.messages.push({ id: `${event.message_id}:user`, role: 'user', text: event.payload.text }); break;
    case 'assistant.delta': {
      const id = event.message_id;
      const index = next.messages.findIndex(m => m.id === id);
      if (index === -1) next.messages.push({ id, role: 'assistant', text: event.payload.text });
      else next.messages[index] = { ...next.messages[index], text: next.messages[index].text + event.payload.text };
      break;
    }
    case 'agent.status': next.status = event.payload.label; break;
    case 'artifact.created': next.artifacts[event.payload.artifact.id] = event.payload.artifact; break;
    case 'answer.partial':
    case 'answer.completed': {
      const a = event.payload.answer;
      next.answers[a.goal_id] = a;
      a.artifacts.forEach(artifact => next.artifacts[artifact.id] = artifact);
      if (event.type === 'answer.completed') {
        const index = next.messages.findIndex(m => m.id === event.message_id);
        if (index < 0) next.messages.push({ id: event.message_id, role: 'assistant', text: a.text, sealed: true });
        else next.messages[index] = { ...next.messages[index], text: a.text, sealed: true };
        next.terminalRuns.add(event.run_id); next.status = '';
      }
      break;
    }
    case 'clarification.required': next.clarifications[event.payload.clarification.id] = event.payload.clarification; next.terminalRuns.add(event.run_id); next.status = 'waiting for clarification'; break;
    case 'clarification.resolved': {
      const current = next.clarifications[event.payload.clarification_id];
      if (current) next.clarifications[current.id] = { ...current, status: 'resolved' };
      break;
    }
    case 'run.failed': next.error = `${event.payload.category}: ${event.payload.message} (${event.payload.code})`; next.terminalRuns.add(event.run_id); next.status = ''; break;
    default: break; // unknown future event: retain activity, never assert a fact
  }
  return next;
}
export function receive(state: State, raw: unknown): State {
  try { return reduce(state, parseEvent(raw)); }
  catch (e) { return { ...state, error: `Invalid server event (diagnostic: ${e instanceof Error ? e.message : String(e)})` }; }
}
