import json, urllib.request, urllib.error
OR_KEY="sk-c8684e2a94ee8f3f-a86311-f3d87a65"
OR="https://omniroute.estudiopleiades.qzz.io"
combos=json.load(open('/tmp/combos.json'))['combos']
def test_model(full):
    body=json.dumps({"model":full,"messages":[{"role":"user","content":"ok"}],"max_tokens":3}).encode()
    req=urllib.request.Request(f"{OR}/v1/chat/completions", data=body, headers={"Authorization":f"Bearer {OR_KEY}","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d=json.loads(r.read().decode()); return r.getcode(), d.get("model","?")[:28], None
    except urllib.error.HTTPError as e:
        return e.code, None, e.read().decode()[:100]
    except Exception as e:
        return None, None, str(e)[:100]
results=[]
for c in combos:
    for m in c['models']:
        full=f"{m['providerId']}/{m['model']}"
        code, rm, err = test_model(full)
        ok = code==200
        results.append((c['name'], full, code, ok, rm or '', err or ''))
        print(f"{'OK ' if ok else 'FAIL'} {c['name']:10} {full:42} -> {code} {rm or err}")
total=len(results); okc=sum(1 for r in results if r[3])
print(f"\n=== RESUMO: {okc}/{total} modelos respondendo 200 ===")
for name in ['sage-main','sage-fast','sage-moa']:
    sub=[r for r in results if r[0]==name]; okk=sum(1 for r in sub if r[3])
    print(f"  {name}: {okk}/{len(sub)}")
