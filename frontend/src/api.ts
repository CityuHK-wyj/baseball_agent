import { snapshot } from './protocol';

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  const value: unknown = await response.json();
  if (!response.ok) {
    const data = value as { error?: { category?: string; message?: string; code?: string } };
    throw new Error(`${data.error?.category || 'transport'}: ${data.error?.message || response.statusText} (${data.error?.code || response.status})`);
  }
  return value;
}
function accepted(value: unknown): { run_id: string; message_id: string } {
  if (typeof value !== 'object' || value === null || !('run_id' in value) || !('message_id' in value) || typeof value.run_id !== 'string' || typeof value.message_id !== 'string') throw Error('Invalid run acknowledgement');
  return { run_id: value.run_id, message_id: value.message_id as string };
}
export async function createConversation(): Promise<string> {
  const value = await request('/api/conversations', { method: 'POST' });
  if (typeof value !== 'object' || value === null || !('conversation_id' in value) || typeof value.conversation_id !== 'string') throw Error('Invalid conversation response');
  return value.conversation_id;
}
export async function getConversation(id: string) { return snapshot.parse(await request(`/api/conversations/${encodeURIComponent(id)}`)); }
export async function sendMessage(id: string, text: string) { return accepted(await request(`/api/conversations/${encodeURIComponent(id)}/messages`, { method: 'POST', body: JSON.stringify({ text }) })); }
export async function submitClarification(id: string, clarid: string, selected_option_ids: string[], free_text: string) {
  return accepted(await request(`/api/conversations/${encodeURIComponent(id)}/clarifications/${encodeURIComponent(clarid)}/responses`, { method: 'POST', body: JSON.stringify({ selected_option_ids, free_text }) }));
}
