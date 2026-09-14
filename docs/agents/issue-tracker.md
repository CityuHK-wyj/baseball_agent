# Issue tracker: Local Markdown

Issues and specifications for this repository live as Markdown files in `.scratch/`. This existing workflow remains in use after configuring the project's GitHub remote; implementation plans do not require external issue publication.

## Conventions

- Use one directory per feature: `.scratch/<feature-slug>/`.
- Store the specification at `.scratch/<feature-slug>/spec.md`.
- Store one implementation ticket per file at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` in dependency order.
- Record workflow state with a `Status:` line near the top of an issue file when the active skill requires it.
- Append discussion history under a `## Comments` heading.

## Publishing and reading work

When a skill says to publish to the issue tracker, create the appropriate file under `.scratch/<feature-slug>/`, creating the directory when needed.

When a skill says to fetch a ticket, read the referenced file. Callers should pass its path or ticket number.

## Ticket dependencies

- Record dependencies with a `Blocked by:` line near the top of each ticket.
- A ticket with no dependencies uses `Blocked by: None`.
- A ticket is ready when every referenced blocker is resolved.
- Work ready tickets in numeric order unless the specification says otherwise.

The repository does not currently install the `triage` skill, so no tracker-wide triage label mapping is configured.
