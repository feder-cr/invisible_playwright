# Contributing to invisible_playwright

Thanks for your interest in improving this project. Contributions are welcome via issues and pull requests.

## Quick links

- **Bug or idea?** Open an [issue](https://github.com/feder-cr/invisible_playwright/issues/new/choose).
- **Security issue?** Do **not** open a public issue - see [SECURITY.md](SECURITY.md).
- **The C++ patches** live in the companion repo [feder-cr/firefox_antidetect_patch](https://github.com/feder-cr/firefox_antidetect_patch) (branch `stealth/151`). Bugs in fingerprint spoofing usually belong there.

## Scope

This repository ships the **Python wrapper** (`invisible_playwright`) around a pre-built patched Firefox. In scope:

- The `InvisiblePlaywright` sync and async API, and its Playwright compatibility layer
- The CLI (`invisible-playwright fetch`, `invisible-playwright version`)
- Tests, docs, examples, packaging

The fingerprint, the engine download and the browser protocol client are shared by every wrapper and live in [invisible_core](https://github.com/feder-cr/invisible_core).

Out of scope (belongs in `firefox_antidetect_patch`):

- Changes to the Firefox C++ source
- New preferences exposed by the patched binary
- Canvas / WebGL / WebRTC / font spoofing logic

## Development setup

To *use* the package you do not need any of this: see [Install](README.md#install) in the README.
The steps below are for working on the code, with [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/feder-cr/invisible_playwright.git
cd invisible_playwright
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
invisible-playwright fetch   # download the patched Firefox binary
```

One of: Windows x86_64, Linux x86_64 / arm64.

## Running tests

```bash
pytest                # unit + integration (default - fast)
pytest -m e2e         # end-to-end, requires the patched binary
pytest -m slow        # wheel-build regression tests
```

Markers are defined in `pyproject.toml`. The default run excludes `slow` and `e2e`.

## Pull requests

1. Fork and create a topic branch (`fix/...`, `feat/...`, `docs/...`).
2. Keep PRs focused - one logical change per PR.
3. Add or update tests for any behavior change.
4. Make sure the default `pytest` run is green.
5. Follow [Conventional Commits](https://www.conventionalcommits.org/) for commit messages (e.g. `fix(launcher): handle missing profile dir`).
6. Update `README.md` or `docs/` when changing user-visible behavior.
7. Open the PR against `main`, fill in the PR template, and link any related issue.

CI must be green before merge.

## Reporting bugs

Before opening, please:

- Search [existing issues](https://github.com/feder-cr/invisible_playwright/issues) - the bug may already be tracked.
- Reproduce on the **latest release** if possible.
- Confirm the issue is in the Python wrapper, not the patched Firefox itself. If a fingerprint is leaking or a detector flags the browser, open the issue at `feder-cr/firefox_antidetect_patch` instead.

Include:

- OS and version, Python version, the output of `invisible-playwright version`
- A minimal reproduction
- Expected vs actual behavior
- Relevant logs / stack traces

## License

By contributing, you agree that your contributions will be licensed under the MIT License (see [LICENSE](LICENSE)).
