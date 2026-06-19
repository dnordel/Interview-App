# Scored Question Web UI

Dependency-free prototype for the scored competency screen.

Run from the repository root so browser fetches can read existing rubric files:

```powershell
python -m http.server 8765
```

Open:

```text
http://localhost:8765/web/scored-question/
```

Notes:

- Reads `config/rubric.json` and trait signal JSON files when served from repo root.
- Keeps candidate notes in browser memory only; no `localStorage` or background persistence.
- `Back` and `Continue` move between competencies while preserving in-memory responses.
- `Save draft JSON` downloads all entered scored responses under `trait_inputs`; it does not write to repo files.
- Keyboard: number keys select score when not typing, `Ctrl+Enter` continues, `Ctrl+S` downloads draft JSON.
- This is a web UI slice, not yet wired to desktop session persistence/finalize flow.
