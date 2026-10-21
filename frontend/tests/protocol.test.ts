import { describe, expect, it } from 'vitest';
import { initialState, receive } from '../src/state';
import { parseEvent } from '../src/protocol';
const base = { id: 'r:1', version: 1, run_id: 'r', message_id: 'm', conversation_id: 'c', timestamp: '2025-01-01T00:00:00Z' };
const req = { id: 'clar:g', conversation_id: 'c', run_id: 'r', goal_id: 'g', question: 'Which?', reason: 'Ambiguous', blocking_requirement_ids: ['req-b'], options: [{ option_id: 'choice:0', label: 'A', description: '' }], allow_free_text: true, status: 'pending' };
const table = { id: 'a', type: 'table', title: 'Analysis', description: '', status: 'accepted', evidence_refs: ['source'], payload: { columns: [{ id: 'score', label: 'Score', kind: 'number' }], rows: [{ score: 2 }], default_sort: [], total_rows: 1, pagination: 'client' } };
const answer = { goal_id: 'g', status: 'LIMITED', text: 'A supported', limitations: ['B pending'], issues: [{ category: 'unsupported', code: 'UNSUPPORTED_MEASURE', message: 'Not available' }], requirements: [{ id: 'req-b', statement: 'B', coverage: 'BLOCKED', required: true }], artifacts: [table] };
function e(id: string, type: string, payload: unknown) { return { ...base, id, type, payload }; }
describe('network ingress and stream reducer', () => {
  it('accumulates deltas exactly once; seals on completion; keeps prior partial answers', () => {
    let s = initialState();
    s = receive(s, e('r:1', 'message.accepted', { text: 'Query' }));
    s = receive(s, e('r:2', 'assistant.delta', { text: 'One' }));
    s = receive(s, e('r:2', 'assistant.delta', { text: 'One' }));
    s = receive(s, e('r:3', 'assistant.delta', { text: ' Two' }));
    expect(s.messages.map(m => m.text)).toEqual(['Query', 'One Two']);
    s = receive(s, e('r:4', 'answer.partial', { answer }));
    s = receive(s, e('r:5', 'clarification.required', { clarification: req }));
    expect(s.answers.g.artifacts).toHaveLength(1);
    expect(s.clarifications['clar:g'].status).toBe('pending');
    s = receive(s, { ...e('s:1', 'clarification.resolved', { clarification_id: 'clar:g' }), run_id: 's' });
    expect(s.clarifications['clar:g'].status).toBe('resolved');
    expect(s.answers.g.text).toBe('A supported');
    s = receive(s, { ...e('s:2', 'answer.completed', { answer: { ...answer, goal_id: 'g2', status: 'COMPLETE', text: 'Final' } }), run_id: 's', message_id: 'm2' });
    expect(s.messages.at(-1)?.sealed).toBe(true);
    expect(s.answers.g.artifacts).toHaveLength(1);
  });
  it('rejects invalid known payloads and allows unknown future events', () => {
    expect(() => parseEvent(e('r:1', 'assistant.delta', { value: 'bad' }))).toThrow();
    expect(receive(initialState(), e('r:1', 'assistant.delta', {})).error).toMatch(/Invalid server event/);
    expect(receive(initialState(), e('r:1', 'future.event', { mystery: 1 })).seen.size).toBe(1);
    expect(() => parseEvent(e('r:1', 'artifact.created', { artifact: { ...table, payload: { rows: 'invalid' } }, goal_id: 'g' }))).toThrow();
    expect(receive(initialState(), e('r:1', 'run.failed', { code: 'X', category: 'provider', message: 'Unavailable' })).error).toMatch(/provider: Unavailable/);
    const chart = { id: 'chart', type: 'chart', title: 'Trend', description: '', status: 'accepted', evidence_refs: [], scope: {}, calculation: [], payload: { kind: 'line', x: 'season', y: 'value', rows: [{ season: 2023, value: 2 }] } };
    expect(parseEvent(e('r:1', 'artifact.created', { artifact: chart, goal_id: 'g' })).type).toBe('artifact.created');
    expect(() => parseEvent(e('r:1', 'artifact.created', { artifact: { ...chart, payload: { ...chart.payload, kind: 'javascript' } }, goal_id: 'g' }))).toThrow();
  });
});
export { req, table, answer };
