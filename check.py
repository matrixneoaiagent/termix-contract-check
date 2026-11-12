#!/usr/bin/env python3
"""Read-only contract source check for Base contracts.

Checks the deployed bytecode, the Sourcify source match (exact_match/match),
compiler/settings metadata, and standard EIP-1967 proxy slots (implementation,
admin, beacon). Writes Markdown/HTML/JSON reports. No keys, no signing.

Usage:
  python check.py --address 0x... [--out report]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

RPC = "https://mainnet.base.org"
SOURCIFY = "https://sourcify.dev/server/v2"
CHAIN_ID = 8453
SLOTS = {
    "implementation": "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",
    "admin": "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103",
    "beacon": "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50",
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rpc(method: str, params: list) -> object:
    last = None
    for _ in range(2):
        try:
            r = requests.post(
                RPC,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                headers={"Content-Type": "application/json", "User-Agent": "contract-check"},
                timeout=30,
            )
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(str(d["error"])[:160])
            return d.get("result")
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(f"{method}: {last}")


def sourcify_match(address: str) -> list[dict]:
    r = requests.get(f"{SOURCIFY}/contract/all-chains/{address}", timeout=30)
    r.raise_for_status()
    return [x for x in r.json().get("results", []) if str(x.get("chainId")) == str(CHAIN_ID)]


def sourcify_details(address: str) -> dict | None:
    r = requests.get(
        f"{SOURCIFY}/contract/{CHAIN_ID}/{address}",
        params={"fields": "sources,metadata"},
        timeout=60,
    )
    if r.status_code != 200:
        return None
    return r.json()


def address_from_slot(raw: str) -> str | None:
    if not raw or raw == "0x" or int(raw, 16) == 0:
        return None
    return "0x" + raw[-40:]


def check(address: str) -> dict:
    code = rpc("eth_getCode", [address, "latest"])
    if not code or code == "0x":
        return {"at": now(), "address": address, "error": "not a contract (no bytecode)"}
    code_bytes = bytes.fromhex(code[2:])
    code_hash = hashlib.sha256(code_bytes).hexdigest()

    matches = sourcify_match(address)
    best = None
    for m in matches:
        if m.get("match") in ("exact_match", "match"):
            best = m
            break
    details = sourcify_details(address) if best else None
    metadata = (details or {}).get("metadata") or {}
    sources = (details or {}).get("sources") or {}
    compiler = (metadata.get("compiler") or {}).get("version")
    optimizer = ((metadata.get("settings") or {}).get("optimizer") or {})

    proxy = {}
    for name, slot in SLOTS.items():
        raw = rpc("eth_getStorageAt", [address, slot, "latest"])
        addr = address_from_slot(raw)
        if addr:
            proxy[name] = addr
    impl = proxy.get("implementation")
    impl_info = None
    if impl:
        impl_code = rpc("eth_getCode", [impl, "latest"])
        impl_info = {
            "address": impl,
            "code_size": max(0, (len(impl_code) - 2) // 2),
            "sourcify": sourcify_match(impl)[:1],
        }

    return {
        "at": now(),
        "chain": "base",
        "chain_id": CHAIN_ID,
        "address": address,
        "code_size": len(code_bytes),
        "code_sha256": code_hash,
        "sourcify_match": best,
        "sourcify_details": {
            "compiler": compiler,
            "language": metadata.get("language"),
            "sources": len(sources),
            "optimizer": optimizer,
            "match": (details or {}).get("match"),
            "verified_at": (details or {}).get("verifiedAt"),
            "match_id": (details or {}).get("matchId"),
        } if details else None,
        "proxy": proxy,
        "implementation_info": impl_info,
        "links": {
            "basescan": f"https://basescan.org/address/{address}#code",
            "sourcify": f"https://sourcify.dev/#/lookup/{address}",
        },
        "error": None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--out", type=Path, default=Path("report"))
    args = ap.parse_args()
    address = args.address.strip()
    if not address.startswith("0x") or len(address) != 42:
        raise SystemExit("address must be a 0x-prefixed 20-byte contract address")
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    d = check(address)
    (out / "report.json").write_text(json.dumps(d, indent=1) + "\n")

    if d.get("error"):
        print(d["error"])
        return 1

    match = (d.get("sourcify_match") or {}).get("match") or "not_found"
    details = d.get("sourcify_details") or {}
    lines = [
        "# Contract source check",
        "",
        f"- Address: `{d['address']}` (Base, chain 8453)",
        f"- On-chain code: **{d['code_size']} bytes**, sha256 `{d['code_sha256']}`",
        f"- Sourcify match: **{match}**",
        f"- Compiler: `{details.get('compiler') or '—'}`",
        f"- Sources: {details.get('sources') or 0}; optimizer: `{json.dumps(details.get('optimizer'))}`",
        f"- Verified at: {details.get('verified_at') or '—'} (matchId {details.get('match_id') or '—'})",
        f"- Basescan: {d['links']['basescan']}",
        f"- Sourcify: {d['links']['sourcify']}",
        "",
        "## Proxy slots (EIP-1967)",
        "",
    ]
    if d["proxy"]:
        for k, v in d["proxy"].items():
            lines.append(f"- {k}: `{v}`")
        if d.get("implementation_info"):
            lines.append(
                f"- implementation code: {d['implementation_info']['code_size']} bytes, "
                f"Sourcify: {json.dumps(d['implementation_info']['sourcify'])[:300]}"
            )
    else:
        lines.append("- none found (not a standard EIP-1967 proxy)")
    lines += ["", "Read-only check; no transactions were signed."]
    (out / "report.md").write_text("\n".join(lines) + "\n")

    proxy_rows = "\n".join(
        f"<tr><td>{k}</td><td><code>{v}</code></td></tr>" for k, v in d["proxy"].items()
    ) or "<tr><td colspan=2>none found</td></tr>"
    status_class = "ok" if match in ("exact_match", "match") else "bad"
    (out / "report.html").write_text(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Contract check</title>
<style>body{{font:14px/1.55 ui-sans-serif,system-ui;background:#0e1512;color:#ecf3ee;padding:32px}}
h1{{font-size:28px}} table{{border-collapse:collapse;width:100%;max-width:1000px}}
th,td{{padding:8px 10px;border-bottom:1px solid #273a33;text-align:left;vertical-align:top}}
th{{color:#94a89f;font-size:11px;text-transform:uppercase;letter-spacing:.08em}}
.ok{{color:#5fd3a6;font-weight:700}} .bad{{color:#ee7b6b;font-weight:700}} code{{font-size:12px}}
.meta{{color:#94a89f}}</style></head><body>
<h1>Contract source check</h1>
<p class="meta">Base · <code>{d['address']}</code> · {d['code_size']} bytes</p>
<p>Sourcify: <span class="{status_class}">{match}</span> · compiler <code>{details.get('compiler') or '—'}</code> · sources {details.get('sources') or 0}</p>
<p class="meta">sha256 <code>{d['code_sha256']}</code> · verified {details.get('verified_at') or '—'}</p>
<h2>Proxy slots (EIP-1967)</h2>
<table><thead><tr><th>Slot</th><th>Address</th></tr></thead><tbody>{proxy_rows}</tbody></table>
<p class="meta">Read-only check; no transactions were signed.</p>
</body></html>"""
    )

    print(f"{address}: code {d['code_size']} bytes, Sourcify {match}, proxy {d['proxy'] or 'none'}")
    print(f"wrote {out}/report.md, report.html, report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
