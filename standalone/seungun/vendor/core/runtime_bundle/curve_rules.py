import statistics
M=1024**2
def slope(v):
 n=len(v);mean=statistics.mean(v);c=(n-1)/2
 return sum((i-c)*(x-mean) for i,x in enumerate(v))/sum((i-c)**2 for i in range(n))
def classify(rows):
 if len(rows)<8:return {'status':'MEMORY_CURVE_INCONCLUSIVE','reason':'FEWER_THAN_8'}
 last=rows[-5:];stats={}
 for k in ['pss_bytes','uss_bytes','rss_bytes']:
  v=[r.get(k) for r in last]
  if any(x is None for x in v):return {'status':'MEMORY_CURVE_INCONCLUSIVE','reason':'MEASUREMENT_MISSING'}
  stats[k]={'slope_bytes':slope(v),'range_bytes':max(v)-min(v),'mean_bytes':statistics.mean(v),'increases':sum(b>a for a,b in zip(v,v[1:]))}
 plateau=all(abs(stats[k]['slope_bytes'])<=M and stats[k]['range_bytes']<=max(16*M,.02*stats[k]['mean_bytes']) for k in ['pss_bytes','uss_bytes'])
 plateau &= stats['rss_bytes']['range_bytes']<=max(32*M,.03*stats['rss_bytes']['mean_bytes'])
 growth=all(stats[k]['slope_bytes']>=2*M and stats[k]['increases']>=3 for k in ['pss_bytes','uss_bytes'])
 return {'status':'OPERATIONAL_PLATEAU_OBSERVED' if plateau else 'CONTINUING_MEMORY_GROWTH' if growth else 'MEMORY_CURVE_INCONCLUSIVE','recent5':stats}
def sum_memory(snapshot):
 members=snapshot['members']
 return {k:(sum(p[k] for p in members) if members and all(p.get(k) is not None for p in members) else None) for k in ['rss_bytes','pss_bytes','uss_bytes']}
def enable_curve(supervisor):
 if supervisor.state!='READY' or supervisor.generation!=1 or supervisor.count!=2 or supervisor.total!=2 or supervisor.failed or not supervisor.worker:
  raise RuntimeError('CURVE_OVERRIDE_PRECONDITION')
 supervisor.cfg['max_requests_per_worker']=20
 supervisor.cfg['soft_recycle_worker_tree_rss_bytes']=supervisor.cfg['abort_worker_tree_rss_bytes']
 supervisor.event('READY',reason='EXPLICIT_MEASUREMENT_OVERRIDE_AFTER_TWO_PASSES')
