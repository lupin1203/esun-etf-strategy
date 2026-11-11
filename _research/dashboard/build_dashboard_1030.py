import json, csv, statistics as st, pandas as pd
U='/mnt/user-data/uploads/SMC-/'
def loadnav(p):
    n={}
    for l in open(p):
        c,rest=l.strip().split(' ',1); n[c]=dict(x.split('=') for x in rest.split(','))
    return n
N28=loadnav('/tmp/claude-0/nav2/n29.txt'); N=loadnav('/tmp/claude-0/nav2/n30.txt')
def r(nav,c,d):
    s=nav[c]; base=float(s[max(k for k in s if k<='10-26')]); return float(s[d])/base-1
sec=json.load(open(U+'esun_sector.json'))
snap=json.load(open(U+'esun_data/daily_2026-10-30.json'))['quotes']
nm=lambda c: snap[c]['name'] if c in snap else c
act=[c for c in N if c!='0050']
names={}
for row in csv.DictReader(open('oct_live.csv',encoding='utf-8')):
    if row['類別']=='主動ETF': names[row['名稱'].split()[0]]=row['名稱']
# ---------- live ----------
DP1102=json.load(open(U+'esun_official/DataPack_251_2026-11-02.json'))
navs={'2026-10-22':1e9,'2026-10-27':997399490.95,'2026-10-28':1015445252.7,'2026-10-29':987700542.92,'2026-10-30':DP1102['account']['nav_tradable']}
rows=[];prev=1e9;po=0
for d,v in navs.items():
    k=d[5:]
    if d=='2026-10-22': rows.append([d,1000000000,0,0,0,0,0,0,0]); continue
    o=r(N,'0050',k); med=st.median([r(N,c,k) for c in act]); cum=v/1e9-1
    rows.append([d,round(v),round(v/prev-1,5),round(cum,5),round((1+o)/(1+po)-1,5),round(o,5),round(med,5),round(cum-o,5),round(cum-med,5)]); prev=v; po=o
with open('daily_live.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow('日期,淨值,當日報酬,累計報酬,0050當日,0050累計,主動ETF中位數累計,領先0050,領先主動中位數'.split(',')); w.writerows(rows)
lst=[('我們（正式流程）','我們',navs['2026-10-30']/1e9-1),('0050 元大台灣50（對照）','0050',r(N,'0050','10-30'))]+[(names.get(c,c),'主動ETF',r(N,c,'10-30')) for c in act]
lst.sort(key=lambda x:-x[2])
with open('oct_live.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow(['排名','名稱','類別','累計報酬'])
    for i,(a,b,c) in enumerate(lst,1): w.writerow([i,a,b,round(c,4)])
dp=DP1102; dp0=json.load(open('/mnt/user-data/outputs/dp1030/DataPack_251_2026-10-30.json'))
pre={h['code']:h['tradable_weight'] for h in dp0['holdings']}
H=[]
for h in dp['holdings']:
    c=h['code'];wt=h['tradable_weight']; role='台積電' if c=='2330' else ('核心' if wt>0.05 else '小部位')
    H.append([c,nm(c),role,sec[c]['name'],h['shares']//1000,h['close_price'],pre.get(c,0),wt])
order={'台積電':0,'核心':1,'小部位':2}; H.sort(key=lambda x:(order[x[2]],-x[7]))
with open('holdings.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow('代號,名稱,角色,產業,張數,收盤價,交易前權重,權重'.split(',')); w.writerows(H)
ind={}
for x in H:
    v=ind.setdefault(x[3],[0,0,0,[]]); v[0]+=x[7]; v[1]+=1; v[2]+=x[2]!='小部位'; v[3].append(x[1])
with open('industry.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow('產業,權重,檔數,核心檔數,持股'.split(','))
    for k,v in sorted(ind.items(),key=lambda x:-x[1][0]): w.writerow([k,round(v[0],5),v[1],v[2],'、'.join(v[3])])
def picks(d,dpk,plan):
    hs={h['code']:h for h in dpk['holdings']}
    core=[nm(c) for c,h in sorted(hs.items(),key=lambda x:-x[1]['tradable_weight']) if c!='2330' and h['tradable_weight']>0.05]
    small=[nm(c) for c,h in hs.items() if h['tradable_weight']<=0.05]
    dec={x['decision_id']:x for x in plan['decisions']}; lab={'BUY':'新買','ADD':'加碼','TRIM':'減碼','SELL_ALL':'出清'}
    b=[f"{nm(o['ticker'])}（{lab[dec[o['decision_ref']]['action']]}）" for o in plan['orders'] if o['side']=='BUY']
    s=[f"{nm(o['ticker'])}（{lab[dec[o['decision_ref']]['action']]}）" for o in plan['orders'] if o['side']=='SELL']
    return [d,len(hs),'台積電','、'.join(core),'、'.join(small),'、'.join(b) or '—','、'.join(s) or '—']
P=list(csv.reader(open('picks_live.csv',encoding='utf-8')))
P=[p for p in P if p[0]!='2026-10-30']
P.append(picks('2026-10-30',dp,json.load(open(U+'esun_reports/D-Plan_TEAM_11358_2026-10-30.json'))))
with open('picks_live.csv','w',encoding='utf-8',newline='') as f: csv.writer(f).writerows(P)
# ---------- rerun ----------
D=list(csv.DictReader(open('daily.csv',encoding='utf-8')))
keep={r_['日期']:r_ for r_ in D if r_['日期']<='2026-10-22'}
navr=pd.read_csv(U+'_research/bt_out_oct/每日淨值.csv'); 
navr.columns=['日期','淨值'] if len(navr.columns)==2 else navr.columns
cur=list(csv.DictReader(open('oct_vs_active.csv',encoding='utf-8')))
cum22={}
for row in cur:
    if row['類別']=='主動ETF':
        c=row['名稱'].split()[0]; cum22[c]=((1+float(row['累計報酬']))/(1+r(N28,c,'10-29'))-1, row['名稱'])
o22=float(keep['2026-10-22']['0050累計'])
out=[];prev=1e9;po_prev=None
for _,x in navr.iterrows():
    d=str(x['日期'])[:10]; v=float(x['淨值']); c_=v/1e9-1
    if d<='2026-10-22':
        k=keep[d]; o=float(k['0050累計']); med=float(k['主動ETF中位數累計'])
    else:
        kk=d[5:]; o=(1+o22)*(1+r(N,'0050',kk))-1; med=st.median([(1+a)*(1+r(N,c,kk))-1 for c,(a,_) in cum22.items()])
    od=(1+o)/(1+po_prev)-1 if po_prev is not None else o
    out.append([d,round(v),round(v/prev-1,5),round(c_,5),round(od,5),round(o,5),round(med,5),round(c_-o,5),round(c_-med,5)]); prev=v; po_prev=o
with open('daily.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow('日期,淨值,當日報酬,累計報酬,0050當日,0050累計,主動ETF中位數累計,領先0050,領先主動中位數'.split(',')); w.writerows(out)
lst=[('我們（重跑）','我們',out[-1][3]),('0050 元大台灣50（對照）','0050',out[-1][5])]+[(n,'主動ETF',(1+a)*(1+r(N,c,'10-30'))-1) for c,(a,n) in cum22.items()]
lst.sort(key=lambda x:-x[2])
with open('oct_vs_active.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow(['排名','名稱','類別','累計報酬'])
    for i,(a,b,c) in enumerate(lst,1): w.writerow([i,a,b,round(c,4)])
h=pd.read_excel(U+'_research/bt_out_oct/回測明細.xlsx',sheet_name='每月持股'); h=h[h['月底角色'].isin(['台積電','核心','小部位'])]
h=h.assign(o=h['月底角色'].map(order)).sort_values(['o','月底權重(%)'],ascending=[True,False])
with open('holdings_rerun.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow('代號,名稱,角色,產業,張數,權重'.split(','))
    for _,x in h.iterrows(): w.writerow([x['代號'],x['名稱'],x['月底角色'],x['產業'],int(x['月底張數']),round(x['月底權重(%)']/100,4)])
ind={}
for _,x in h.iterrows():
    v=ind.setdefault(x['產業'],[0,0,0,[]]); v[0]+=x['月底權重(%)']/100; v[1]+=1; v[2]+=x['月底角色']!='小部位'; v[3].append(x['名稱'])
with open('industry_rerun.csv','w',encoding='utf-8',newline='') as f:
    w=csv.writer(f); w.writerow('產業,權重,檔數,核心檔數,持股'.split(','))
    for k,v in sorted(ind.items(),key=lambda x:-x[1][0]): w.writerow([k,round(v[0],4),v[1],v[2],'、'.join(v[3])])
t=pd.read_excel(U+'_research/bt_out_oct/回測明細.xlsx',sheet_name='交易明細'); t['成交日']=t['成交日'].astype(str).str[:10]
lab={'BUY':'新買','ADD':'加碼','TRIM':'減碼','SELL_ALL':'出清'}
P=list(csv.reader(open('daily_picks.csv',encoding='utf-8'))); P=[p for p in P if p[0] not in ('2026-10-27','2026-10-28','2026-10-29','2026-10-30')]
last=P[-1]
for d in ['2026-10-27','2026-10-28','2026-10-29','2026-10-30']:
    td=t[t['成交日']==d]
    b='、'.join(f"{x['名稱']}（{lab.get(x['動作'],x['動作'])}）" for _,x in td.iterrows() if x['動作'] in ('BUY','ADD')) or '—'
    s='、'.join(f"{x['名稱']}（{lab.get(x['動作'],x['動作'])}）" for _,x in td.iterrows() if x['動作'] in ('TRIM','SELL_ALL')) or '—'
    if d=='2026-10-30':
        P.append([d,20,'台積電','、'.join(h[h['月底角色']=='核心']['名稱']),'、'.join(h[h['月底角色']=='小部位']['名稱']),b,s])
    else:
        P.append([d,last[1],'台積電',last[3],last[4],b,s])
with open('daily_picks.csv','w',encoding='utf-8',newline='') as f: csv.writer(f).writerows(P)
print(open('daily_live.csv').read()); print(open('oct_live.csv').read()[:400]); print(open('daily.csv').read()[-700:]); print(open('oct_vs_active.csv').read()[:300]); print(P[-3:])
