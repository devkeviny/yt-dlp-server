import json, urllib.request, urllib.error
OR_KEY="sk-c8684e2a94ee8f3f-a86311-f3d87a65"
OR="https://omniroute.estudiopleiades.qzz.io"
combos=json.load(open('/tmp/combos.json'))['combos']
def test(full):
    body=json.dumps({"model":full,"messages":[{"role":"user","content":"ok"}],"max_tokens":5,"stream":False}).encode()
    req=urllib.request.Request(f"{OR}/v1/chat/completions", data=body, headers={"Authorization":f"Bearer {OR_KEY}","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d=json.loads(r.read().decode())
            if "choices" in d and d["choices"]: return r.status, "ok"
            return r.status, "empty"
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:80]
    except Exception as e:
        return 0, str(e)[:80]
res=[]
for c in combos:
    for m in c['models']:
        full=f"{m['providerId']}/{m['model']}"
        code, info = test(full)
        ok = code==200 and info=="ok"
        res.append((c['name'],full,code,ok,info))
        print(f"{'OK ' if ok else 'XX '} {c['name']:9} {full:42} {code} {info}")
t=len(res); o=sum(1 for r in res if r[3])
print(f"\n=== {o}/{t} 200+completion valido ===")
for n in ['sage-main','sage-fast','sage-moa']:
    s=[r for r in res if r[0]==n]; k=sum(1 for r in s if r[3])
    print(f"  {n}: {k}/{len(s)}")
