import sys
import re
cls,meths=sys.argv[1],sys.argv[2].split(',')
files=['/tmp/final_user_cold.out','/tmp/final_fhir.out','/tmp/final_236.out']
k=f'Graph.KG.{cls}.1'; data={}; order=[]
for f in files:
    for l in open(f,errors='replace'):
        q=l.rstrip('\n').split('|',4)
        if len(q)<5 or q[1]!=k: continue
        key=(int(q[2]),q[4])
        if key not in data: order.append(key)
        data[key]=max(data.get(key,-9),int(q[3]))
cur=None
for key in sorted(order):
    ln,t=key
    m=re.match(r'^(\w+)(\(|\s|$)',t)
    if m and not t.startswith((' ','\t',';','{','}')): cur=m.group(1)
    s=t.strip()
    if cur in meths and s and not s.startswith((';','//','{','}','/')) and data[key]<=0:
        print(f'{cur}:{ln}: {s[:150]}')
