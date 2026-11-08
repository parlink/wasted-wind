# Windfall

Reveal, predict, automatically dispatch batteries against, and simulate fixes for Scottish wind curtailment.
HackTheBurgh XIII, 7–8 November 2026.

- Plan: [docs/BRIEF.md](docs/BRIEF.md)
- Data formats and API: [docs/CONTRACT.md](docs/CONTRACT.md)
- Agent rules: [CLAUDE.md](CLAUDE.md)

```bash
pip install -r requirements.txt
make fetch-data                                  # latest processed data + saved models from Person A
make serve                                       # http://localhost:8000
```
