import re,collections,sys
def load(p):
    d=collections.defaultdict(dict)
    for l in open(p,errors='replace'):
        q=l.rstrip('\n').split('|',4)
        if len(q)<5 or q[0]!='L': continue
        d[q[1]][(int(q[2]),q[4])]=int(q[3])
    return d
def executable(t, adjusted):
    s=t.strip()
    if not s or s.startswith((';','//','/*','*','{','}')): return False
    if adjusted:
        if re.match(r'^\w+(\(.*\))?\s+methodimpl\b',t) or re.match(r'^%0dsql\w*',t): return False
        if '%xxsql' in t or 'BuildQuery^%SYS.SQLSRV' in t or t.strip() in ('q','return'): return False
        if re.match(r'^%sqllistUCQ',t) or re.match(r'^\s*s t\("%0dsql',t): return False
    return True
files=sys.argv[1:]
S=[load(f) for f in files]
skip=lambda k: k.startswith('User.') or 'LedgerStats' in k or 'LedgerRevision' in k
for adjusted in (False,True):
    H=T=0; rows=[]
    for k in set().union(*S):
        if skip(k): continue
        keys=set().union(*(s[k].keys() for s in S if k in s))
        keys={x for x in keys if executable(x[1],adjusted)}
        h=sum(1 for x in keys if max(s.get(k,{}).get(x,0) for s in S)>0)
        rows.append((len(keys)-h,k,h,len(keys))); H+=h;T+=len(keys)
    print('adjusted' if adjusted else 'raw', H,T,round(100*H/T,1))
    if adjusted:
        for r in sorted(rows,reverse=True)[:14]: print(' ',r[1],r[2],r[3],r[0])
