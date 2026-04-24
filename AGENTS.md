# Agent Notes

- Use `uv` for dependency management.
- Use `tox -e format`, `tox -e lint`, and `tox -e unit` for local verification.
- Keep `src/charm.py` orchestration-focused.
- Move Juju-independent logic into small modules under `src/`.
