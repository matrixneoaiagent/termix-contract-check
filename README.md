# Base contract source check

Read-only verification for a Base contract:

- deployed bytecode size and sha256
- Sourcify source match (`exact_match` / `match` / not found)
- compiler version, language, sources count and optimizer settings
- standard EIP-1967 proxy slots (implementation/admin/beacon), with the
  implementation's code size and Sourcify match
- Basescan and Sourcify links, Markdown/HTML/JSON reports

No keys, no signing, no transactions.

## Run

```bash
python -m venv .venv && . .venv/bin/activate
pip install requests
python check.py --address 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 --out report
```

Open `report/report.html` or `report/report.md`.

MIT licensed.
