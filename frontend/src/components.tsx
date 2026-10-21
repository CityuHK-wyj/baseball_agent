import { useState } from 'react';
import { flexRender, getCoreRowModel, getFilteredRowModel, getPaginationRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState, type VisibilityState } from '@tanstack/react-table';
import { ResponsiveContainer, BarChart, Bar, LineChart, Line, ScatterChart, Scatter, CartesianGrid, XAxis, YAxis, Tooltip } from 'recharts';
import { table, chart, type Artifact, type Answer, type Clarification } from './protocol';
import { Button } from './ui/button';

export function ClarificationCard({ request, pending, onSubmit }: { request: Clarification; pending: boolean; onSubmit: (selected: string[], text: string) => void }) {
  const [choice, setChoice] = useState('');
  const [text, setText] = useState('');
  const resolved = request.status === 'resolved';
  return <section className="card" aria-label="Clarification request">
    <h3>Clarification needed</h3><p>{request.question}</p><p className="muted">{request.reason}</p>
    <form onSubmit={e => { e.preventDefault(); if (!resolved && !pending && (choice || text.trim())) onSubmit(choice ? [choice] : [], choice ? '' : text.trim()); }}>
      <fieldset disabled={resolved || pending}>
        <legend>Choose an option or provide your own answer</legend>
        {request.options.map(option => <label className="choice" key={option.option_id}><input type="radio" name={`clar-${request.id}`} checked={choice === option.option_id} onChange={() => { setChoice(option.option_id); setText(''); }} /> {option.label} {option.description}</label>)}
        {request.allow_free_text && <label className="choice">Your answer<input aria-label="Your answer" value={text} onChange={e => { setText(e.target.value); setChoice(''); }} /></label>}
        <Button type="submit" disabled={!(choice || text.trim()) || pending}>{pending ? 'Submitting…' : 'Continue'}</Button>
      </fieldset>
    </form>{resolved && <p role="status">Clarification submitted</p>}
  </section>;
}

export function TableView({ payload }: { payload: unknown }) {
  const parsed = table.safeParse(payload);
  if (!parsed.success) return <p role="alert">Invalid table data</p>;
  return <ValidatedTable payload={parsed.data} />;
}
function ValidatedTable({ payload }: { payload: ReturnType<typeof table.parse> }) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState('');
  const [visibility, setVisibility] = useState<VisibilityState>({});
  const columns: ColumnDef<(typeof payload.rows)[number]>[] = payload.columns.map(col => ({
    id: col.id, accessorFn: row => row[col.id], header: col.label,
    cell: info => { const value = info.getValue(); return typeof value === 'number' ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value) + (col.unit ? ` ${col.unit}` : '') : String(value ?? '—'); },
    sortingFn: col.kind === 'number' ? 'basic' : 'alphanumeric',
  }));
  const instance = useReactTable({ data: payload.rows, columns, state: { sorting, globalFilter: filter, columnVisibility: visibility },
    onSortingChange: setSorting, onGlobalFilterChange: setFilter, onColumnVisibilityChange: setVisibility,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getFilteredRowModel: getFilteredRowModel(), getPaginationRowModel: getPaginationRowModel(), initialState: { pagination: { pageSize: 20 } } });
  return <div className="table-container"><div className="toolbar"><label>Filter rows <input value={filter} onChange={e => setFilter(e.target.value)} /></label>
    <details><summary>Columns</summary>{instance.getAllLeafColumns().map(c => <label className="choice" key={c.id}><input type="checkbox" checked={c.getIsVisible()} onChange={c.getToggleVisibilityHandler()} />{c.columnDef.header as string}</label>)}</details></div>
    <div className="scroll"><table><thead>{instance.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => <th scope="col" key={header.id}><button type="button" onClick={header.column.getToggleSortingHandler()} aria-label={`Sort by ${String(header.column.columnDef.header)}`}>{flexRender(header.column.columnDef.header, header.getContext())} {header.column.getIsSorted() === 'asc' ? '↑' : header.column.getIsSorted() === 'desc' ? '↓' : '↕'}</button></th>)}</tr>)}</thead>
      <tbody>{instance.getRowModel().rows.map(row => <tr key={row.id}>{row.getVisibleCells().map(cell => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div>
    <div className="toolbar"><button disabled={!instance.getCanPreviousPage()} onClick={() => instance.previousPage()}>Previous</button><span>Page {instance.getState().pagination.pageIndex + 1} / {instance.getPageCount()}</span><button disabled={!instance.getCanNextPage()} onClick={() => instance.nextPage()}>Next</button></div>
  </div>;
}
export function ArtifactRenderer({ artifact }: { artifact: Artifact }) {
  return <section className="card" aria-label={artifact.type === 'table' ? 'Analytical table' : `${artifact.type} artifact`}><h3>{artifact.title}</h3><p className="muted">{artifact.description}</p>
    {artifact.type === 'table' ? <TableView payload={artifact.payload} /> : artifact.type === 'chart' ? <ChartView payload={artifact.payload} /> : <pre>{JSON.stringify(artifact.payload, null, 2)}</pre>}
    <details><summary>Source and evidence</summary><ul>{artifact.evidence_refs.map((ref, i) => <li key={i}>{ref}</li>)}{Object.entries(artifact.scope || {}).map(([key, value]) => value != null && <li key={key}>{key}: {value}</li>)}{artifact.calculation?.map((item, i) => <li key={`calc-${i}`}>Measure: {item}</li>)}</ul></details>
  </section>;
}
function ChartView({ payload }: { payload: unknown }) {
  const result = chart.safeParse(payload);
  if (!result.success) return <p role="alert">Invalid chart data</p>;
  const { kind, x, y, rows } = result.data;
  return <div role="img" aria-label={`${kind} chart of ${y} by ${x}`} style={{ width: '100%', height: 300 }}><ResponsiveContainer>{kind === 'bar' ? <BarChart data={rows}><CartesianGrid /><XAxis dataKey={x} /><YAxis /><Tooltip /><Bar dataKey={y} fill="#2563eb" /></BarChart> : kind === 'line' ? <LineChart data={rows}><CartesianGrid /><XAxis dataKey={x} /><YAxis /><Tooltip /><Line dataKey={y} stroke="#2563eb" /></LineChart> : <ScatterChart><CartesianGrid /><XAxis dataKey={x} type="number" /><YAxis dataKey={y} type="number" /><Tooltip /><Scatter data={rows} fill="#2563eb" /></ScatterChart>}</ResponsiveContainer></div>;
}
export function ErrorNotice({ message }: { message: string }) {
  return <p role="alert" className="error">{message}</p>;
}

export function AnswerPanel({ answer, debug }: { answer: Answer; debug: boolean }) {
  return <section className="answer" aria-label="Answer status"><strong className="badge">{answer.status.replaceAll('_', ' ')}</strong>
    {answer.issues?.length > 0 && <details><summary>Execution notices ({answer.issues.length})</summary><ul>{answer.issues.map((issue, i) => <li key={i}>{issue.category}: {issue.message} ({issue.code})</li>)}</ul></details>}
    {answer.limitations.length > 0 && <details><summary>Limitations ({answer.limitations.length})</summary><ul>{answer.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></details>}
    {answer.artifacts.map(a => <ArtifactRenderer key={a.id} artifact={a} />)}
    {debug && <details><summary>Developer: Requirement coverage</summary><ul>{answer.requirements.map(r => <li key={r.id}>{r.id} — {r.statement}: {r.coverage}</li>)}</ul><p>Goal: {answer.goal_id}</p></details>}
  </section>;
}
