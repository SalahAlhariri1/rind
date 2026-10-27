p='agent/agents.py'
s=open(p,encoding='utf-8').read()
if 'isinstance(out, dict)' in s:
    print('already patched'); raise SystemExit
for key in ('claims','verdicts','why'):
    old=f'    out.setdefault("{key}", [])'
    assert old in s, key
    s=s.replace(old, f'    out = out if isinstance(out, dict) else {{"{key}": out if isinstance(out, list) else []}}\n' + old, 1)
old='    flagged = {r["account_id"]: r for r in snap'
assert old in s, 'planner'
s=s.replace(old, '    out = out if isinstance(out, dict) else {"deep_dives": out if isinstance(out, list) else []}\n' + old, 1)
open(p,'w',encoding='utf-8').write(s)
print('patched')
