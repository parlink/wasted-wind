# Wasted Wind

Reveal, predict, automatically dispatch batteries against, and simulate fixes for Scottish wind curtailment.
HackTheBurgh XIII, 7–8 November 2026.

- Plan: [docs/BRIEF.md](docs/BRIEF.md)
- Data formats and API: [docs/CONTRACT.md](docs/CONTRACT.md)
- Agent rules: [CLAUDE.md](CLAUDE.md)

```bash
pip install -r requirements.txt
gh release download data-v1 -D data/processed   # processed data from Person A
make serve                                       # http://localhost:8000
```
