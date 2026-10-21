import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AnswerPanel, ClarificationCard, ErrorNotice, TableView } from '../src/components';
import { req, table, answer } from './protocol.test';
import type { Clarification, Answer } from '../src/protocol';

describe('accessible presentation', () => {
  it('selects opaque option IDs; supports free text; disables pending/resolved', async () => {
    const user = userEvent.setup(); const submit = vi.fn();
    const c = req as Clarification;
    const view = render(<ClarificationCard request={c} pending={false} onSubmit={submit} />);
    await user.click(screen.getByLabelText(/A/)); await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(submit).toHaveBeenCalledWith(['choice:0'], '');
    await user.type(screen.getByLabelText('Your answer'), 'Specific player');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(submit).toHaveBeenCalledWith([], 'Specific player');
    view.rerender(<ClarificationCard request={c} pending onSubmit={submit} />);
    expect(screen.getByRole('button', { name: /Submitting/ })).toBeDisabled();
    view.rerender(<ClarificationCard request={{ ...c, status: 'resolved' }} pending={false} onSubmit={submit} />);
    expect(screen.getByText('Clarification submitted')).toBeVisible();
  });
  it('shows structured table; sorts and filters; preserves limited status', () => {
    render(<AnswerPanel answer={answer as Answer} debug />);
    expect(screen.getByText('LIMITED')).toBeVisible();
    expect(screen.getByText('B pending')).not.toBeVisible(); // progressive disclosure
    fireEvent.click(screen.getByText(/Limitations/));
    expect(screen.getByText('B pending')).toBeVisible();
    expect(screen.getByRole('table')).toBeVisible();
    expect(screen.getByText(/Requirement coverage/)).toBeVisible();
  });
  it('shows a same-Goal approved table and a pending clarification card at the same time', () => {
    // Partial approval: Requirement A's table and Requirement B's clarification coexist.
    render(<><AnswerPanel answer={answer as Answer} debug />
      <ClarificationCard request={req as Clarification} pending={false} onSubmit={vi.fn()} /></>);
    expect(screen.getByRole('table')).toBeVisible();
    expect(screen.getByText('LIMITED')).toBeVisible();
    expect(screen.getByLabelText('Clarification request')).toBeVisible();
    expect(screen.getByLabelText('Clarification request')).toHaveTextContent('Which?');
  });
  it('keeps the approved table after the clarification is resolved', () => {
    const resolved = { ...(req as Clarification), status: 'resolved' as const };
    render(<><AnswerPanel answer={answer as Answer} debug />
      <ClarificationCard request={resolved} pending={false} onSubmit={vi.fn()} /></>);
    expect(screen.getByRole('table')).toBeVisible();
    expect(screen.getByText('Clarification submitted')).toBeVisible();
  });
  it('renders structured failure category and code without hiding prior content', () => {
    render(<><AnswerPanel answer={answer as Answer} debug={false} /><ErrorNotice message="provider: Source unavailable (SOURCE_UNAVAILABLE)" /></>);
    expect(screen.getByRole('alert')).toHaveTextContent('provider: Source unavailable (SOURCE_UNAVAILABLE)');
    expect(screen.getByRole('table')).toBeVisible();
  });
  it('sorts numeric rows and filters them', async () => {
    const user = userEvent.setup();
    render(<TableView payload={{ ...table.payload, rows: [{ score: 2 }, { score: 1 }] }} />);
    const score = screen.getByRole('button', { name: /Sort by Score/ });
    await user.click(score);
    let rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('2')).toBeVisible();
    await user.click(score);
    rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('1')).toBeVisible();
    await user.type(screen.getByLabelText('Filter rows'), '1');
    expect(screen.getAllByRole('row')).toHaveLength(2);
  });
});
