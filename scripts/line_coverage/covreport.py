import re
import collections
import sys
rows=collections.defaultdict(list)
for l in open(sys.argv[1],errors='replace'):
    if l.startswith('L|'):
        _,rn,ln,cnt,text=l.rstrip('\n').split('|',4); rows[rn].append((int(ln),int(cnt),text))
def is_code(t):
    s=t.strip()
    if not s or not t.startswith((' ','\t')): return False
    if s.startswith((';','//','#;','/*','*/')): return False
    if s in ('{','}','} Else {','}Else{'): return False
    return True
want={
 'Graph.KG.BM25Index.1':['Insert','Delete','Upgrade','RemoveDoc','RequireWritable','LockWrite','LockTimeout','Search','Info','Build','IndexDoc'],
 'Graph.KG.ArnoAccel.1':['ReaderRevisions','LibraryVersion','NativeResult','PageRankNativeJson','PPRNativeJson','WCCNativeJson','CDLPNativeJson','SubgraphNativeJson','Load','BumpVersion'],
 'Graph.KG.AlgoCache.1':['Token','Lookup','Get','Put','InvalidateGraph','InvalidateAll'],
 'Graph.KG.NKGAccelTraversal.1':['BFSJson','LastBfsPath','LastBfsError'],
 'Graph.KG.NKGAccelAdjacency.1':['ExportAdjacencyWithPreds'],
 'Graph.KG.EdgeScan.1':['WriteAdjacencyKeyed','DeleteAdjacencyKeyed'],
 'Graph.KG.BenchFormat.1':['Seed'],
 'Graph.KG.TraversalBuild.1':['BuildKG','Build2HopExactStats'],
 'Graph.KG.Eraser.1':['InvalidateDerived'],
}
print(f"{'method':46s} {'code':>5s} {'hit':>5s} {'%':>6s}")
T=H=0; miss=[]
for rn,methods in want.items():
    groups=collections.OrderedDict(); cur=None
    for ln,c,t in rows[rn]:
        if t and not t.startswith((' ','\t')):
            cur=re.split(r'[( ]',t.strip())[0]; groups.setdefault(cur,[])
        elif cur: groups[cur].append((ln,c,t))
    for m in methods:
        g=[x for x in groups.get(m,[]) if is_code(x[2])]
        if not g: continue
        h=sum(1 for x in g if x[1]>0); T+=len(g); H+=h
        print(f"{rn.replace('Graph.KG.','').replace('.1','')+'.'+m:46s} {len(g):5d} {h:5d} {100*h/len(g):6.1f}")
        for x in g:
            if x[1]<=0: miss.append((rn.replace('Graph.KG.','').replace('.1','')+'.'+m,x[0],x[2].strip()[:100]))
print(f"{'CHANGED METHODS TOTAL':46s} {T:5d} {H:5d} {100*H/T:6.1f}")
if len(sys.argv)>2:
    for m,ln,t in miss: print(f"  unhit {m}:{ln}: {t}")
