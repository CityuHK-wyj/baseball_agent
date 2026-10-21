import { z } from 'zod';
import type { components } from './generated';

export type WireArtifact = components['schemas']['PresentationArtifact'];
export type WireClarification = components['schemas']['ClarificationRequest'];
const scalar = z.union([z.string(), z.number(), z.boolean(), z.null()]);
const column = z.object({ id: z.string(), label: z.string(), kind: z.enum(['string', 'number', 'boolean', 'date']), unit: z.string().nullable().optional(), format: z.string().nullable().optional() });
export const table = z.object({ columns: z.array(column), rows: z.array(z.record(z.string(), scalar)), default_sort: z.array(z.string()).optional(), total_rows: z.number().nullable().optional(), pagination: z.enum(['client', 'server']) });
export const chart = z.object({ kind: z.enum(['bar', 'line', 'scatter']), x: z.string(), y: z.string(), rows: z.array(z.record(z.string(), scalar)) });
export const artifact = z.object({ id: z.string(), type: z.enum(['text', 'metric', 'table', 'chart', 'evidence', 'warning']), title: z.string(), description: z.string(), status: z.enum(['accepted', 'partial']), evidence_refs: z.array(z.string()), scope: z.record(z.string(), z.union([z.string(), z.number(), z.null()])).optional(), calculation: z.array(z.string()).optional(), payload: z.record(z.string(), z.unknown()) }).superRefine((a, ctx) => {
  const schema = a.type === 'table' ? table : a.type === 'chart' ? chart : z.record(z.string(), z.unknown());
  const result = schema.safeParse(a.payload);
  if (!result.success) ctx.addIssue({ code: 'custom', message: `Invalid ${a.type} payload: ${result.error.message}` });
});
export const clarification = z.object({ id: z.string(), conversation_id: z.string(), run_id: z.string(), goal_id: z.string(), question: z.string(), reason: z.string(), blocking_requirement_ids: z.array(z.string()), options: z.array(z.object({ option_id: z.string(), label: z.string(), description: z.string() })), allow_free_text: z.boolean(), status: z.enum(['pending', 'resolved']) });
export const answer = z.object({ goal_id: z.string(), status: z.enum(['COMPLETE', 'LIMITED', 'PARTIAL', 'WAITING_FOR_USER', 'FAILED']), text: z.string(), limitations: z.array(z.string()), issues: z.array(z.object({ code: z.string(), category: z.enum(['provider', 'validation', 'unsupported', 'semantic', 'run']), message: z.string() })), requirements: z.array(z.object({ id: z.string(), statement: z.string(), coverage: z.string(), required: z.boolean() })), artifacts: z.array(artifact) });
export type Answer = z.infer<typeof answer>;
export type Artifact = z.infer<typeof artifact>;
export type Clarification = z.infer<typeof clarification>;
const base = { version: z.literal(1), id: z.string(), conversation_id: z.string(), run_id: z.string(), message_id: z.string(), timestamp: z.iso.datetime() };
const event = <T extends string, S extends z.ZodType>(type: T, payload: S) => z.object({ ...base, type: z.literal(type), payload });
const known = z.discriminatedUnion('type', [
  event('run.started', z.object({ status: z.literal('running') })),
  event('message.accepted', z.object({ text: z.string() })),
  event('agent.status', z.object({ label: z.string() })),
  event('assistant.delta', z.object({ text: z.string() })),
  event('clarification.required', z.object({ clarification })),
  event('clarification.resolved', z.object({ clarification_id: z.string() })),
  event('artifact.created', z.object({ artifact, goal_id: z.string() })),
  event('answer.partial', z.object({ answer })),
  event('answer.completed', z.object({ answer })),
  event('run.failed', z.object({ code: z.string(), category: z.enum(['transport', 'provider', 'validation', 'unsupported', 'semantic', 'run']), message: z.string() })),
]);
export type KnownEvent = z.infer<typeof known>;
export type AgentEvent = KnownEvent | z.infer<typeof unknownEvent>;
export function isKnownEvent(event: AgentEvent): event is KnownEvent { return types.has(event.type); }
const unknownEvent = z.object({ ...base, type: z.string(), payload: z.unknown() });
const types: Set<string> = new Set(known.options.map(o => o.shape.type.value));
export function parseEvent(value: unknown): AgentEvent {
  const envelope = unknownEvent.parse(value);
  if (!types.has(envelope.type)) return envelope;
  return known.parse(value);
}
export const snapshot = z.object({ conversation_id: z.string(), messages: z.array(z.object({ role: z.enum(['user', 'assistant']), text: z.string(), goal_id: z.string().optional() })), pending_clarification: clarification.nullable(), events: z.array(z.unknown()) });
export type Snapshot = z.infer<typeof snapshot>;
// Generated types are compile-time witnesses to the backend schema, not a second model.
const _wireClarification: WireClarification = {} as Clarification;
const _wireTable: components['schemas']['TablePayload'] = {} as z.infer<typeof table>;
const _wireArtifact: WireArtifact = {} as Artifact;
const _wireEvent: NonNullable<components['schemas']['ConversationSnapshot']['events']>[number] = {} as KnownEvent;
void _wireClarification; void _wireTable; void _wireArtifact; void _wireEvent;
