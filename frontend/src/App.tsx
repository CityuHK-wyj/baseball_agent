import { useEffect, useReducer, useRef, useState } from 'react';
import { useNavigate, useParams, Routes, Route } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createConversation, getConversation, sendMessage, submitClarification } from './api';
import { AnswerPanel, ClarificationCard, ErrorNotice } from './components';
import { initialState, receive } from './state';
import { parseEvent } from './protocol';

function ConversationPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(receive, undefined, initialState);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [debug, setDebug] = useState(false);
  const [runId, setRunId] = useState('');
  const stream = useRef<EventSource | null>(null);
  const lastId = useRef<Record<string, string>>({});
  const snapshot = useQuery({ queryKey: ['conversation', id], queryFn: () => getConversation(id!), enabled: !!id });
  useEffect(() => {
    if (!snapshot.data) return;
    for (const item of snapshot.data.events) dispatch(item);
    const runs = snapshot.data.events as { id?: string; type?: string; run_id?: string }[];
    const last = runs.at(-1);
    if (last?.run_id && !['answer.completed', 'clarification.required', 'run.failed'].includes(last.type || '')) setRunId(last.run_id);
  }, [snapshot.data]);
  useEffect(() => {
    if (!runId) return;
    let retries = 0;
    let closed = false;
    const connect = () => {
      if (closed) return;
      const after = lastId.current[runId];
      const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events${after ? `?after=${encodeURIComponent(after)}` : ''}`);
      stream.current = source;
      source.onmessage = e => {
        try {
          const event = parseEvent(JSON.parse(e.data));
          dispatch(event);
          lastId.current[runId] = event.id;
          if (['answer.completed', 'clarification.required', 'run.failed'].includes(event.type)) {
            closed = true; source.close(); setPending(false);
            void queryClient.invalidateQueries({ queryKey: ['conversation', id] });
          }
        } catch (exc) { dispatch({}); setError(`Invalid server event: ${String(exc)}`); closed = true; source.close(); setPending(false); }
      };
      source.onerror = () => {
        source.close();
        if (closed) return;
        if (++retries > 3) { setError('Transport: stream interrupted; existing results are preserved. Refresh to recover.'); setPending(false); return; }
        setError('Transport: reconnecting to event stream…');
        setTimeout(connect, 500 * retries);
      };
      source.onopen = () => { setError(''); retries = 0; };
    };
    connect();
    return () => { closed = true; stream.current?.close(); };
  }, [runId, id, queryClient]);
  async function start(text: string) {
    if (!id || pending) return;
    setPending(true); setError('');
    try { const ack = await sendMessage(id, text); setInput(''); setRunId(ack.run_id); }
    catch (e) { setError(String(e)); setPending(false); }
  }
  async function clarify(clarid: string, selected: string[], text: string) {
    if (!id || pending) return;
    setPending(true); setError('');
    try { const ack = await submitClarification(id, clarid, selected, text); setRunId(ack.run_id); }
    catch (e) { setError(String(e)); setPending(false); }
  }
  const derivedGoals = new Set(Object.values(state.answers).map(a => a.goal_id));
  const history = snapshot.data?.messages.filter(m => !m.goal_id || !derivedGoals.has(m.goal_id)) || [];
  const clarifications = Object.values(state.clarifications);
  const pendingClarification = snapshot.data?.pending_clarification;
  if (pendingClarification && !clarifications.some(c => c.id === pendingClarification.id)) clarifications.push(pendingClarification);
  return <main className="layout"><header><h1>Baseball Agent</h1><button onClick={() => navigate('/conversation/new')}>New conversation</button><label><input type="checkbox" checked={debug} onChange={e => setDebug(e.target.checked)} /> Developer mode</label></header>
    <section aria-label="Conversation thread" className="thread">
      {snapshot.isError && <p role="alert">Transport: {String(snapshot.error)}</p>}
      {history.map((m, i) => <div key={`history-${i}`} className={`message ${m.role}`}><strong>{m.role === 'user' ? 'You' : 'Agent'}</strong><p>{m.text}</p></div>)}
      {state.messages.map(m => <div key={m.id} className={`message ${m.role}`}><strong>{m.role === 'user' ? 'You' : 'Agent'}</strong><p>{m.text}</p></div>)}
      {Object.values(state.answers).map(a => <AnswerPanel key={a.goal_id} answer={a} debug={debug} />)}
      {clarifications.map(c => <ClarificationCard key={c.id} request={c} pending={pending} onSubmit={(ids, text) => void clarify(c.id, ids, text)} />)}
      {state.status && <p role="status">Agent: {state.status}</p>}
      {(error || state.error) && <ErrorNotice message={error || state.error} />}
    </section>
    <form className="composer" onSubmit={e => { e.preventDefault(); if (input.trim()) void start(input.trim()); }}><label htmlFor="question">Ask a baseball question</label><div><input id="question" value={input} onChange={e => setInput(e.target.value)} disabled={pending || clarifications.some(c => c.status === 'pending')} placeholder="Ask about a player or statistic…" /><button type="submit" disabled={!input.trim() || pending || clarifications.some(c => c.status === 'pending')}>Send</button></div></form>
  </main>;
}
function NewConversation() {
  const navigate = useNavigate();
  const [error, setError] = useState('');
  useEffect(() => { let active = true; createConversation().then(id => { if (active) navigate(`/conversation/${id}`, { replace: true }); }).catch(e => setError(String(e))); return () => { active = false; }; }, [navigate]);
  return <main role="status">{error || 'Creating conversation…'}</main>;
}
export default function App() { return <Routes><Route path="/conversation/new" element={<NewConversation />} /><Route path="/conversation/:id" element={<ConversationPage />} /><Route path="*" element={<NewConversation />} /></Routes>; }
