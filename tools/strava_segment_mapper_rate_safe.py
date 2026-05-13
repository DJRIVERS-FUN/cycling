#!/usr/bin/env python3
"""
Strava Segment Mapper - rate-limit friendly version
"""
import argparse, json, os, sys, time
from pathlib import Path
import folium, polyline, requests
from dotenv import load_dotenv
TOKEN_URL='https://www.strava.com/oauth/token'
EXPLORE_URL='https://www.strava.com/api/v3/segments/explore'
AREAS={'hakodate':{'bounds':(41.65,140.55,42.05,141.05)},'kijihiki':{'bounds':(41.86,140.57,42.04,140.82)},'shirotai':{'bounds':(41.73,140.58,41.90,140.82)}}
def load_credentials():
 load_dotenv();cid=os.getenv('STRAVA_CLIENT_ID');sec=os.getenv('STRAVA_CLIENT_SECRET');ref=os.getenv('STRAVA_REFRESH_TOKEN')
 if not all([cid,sec,ref]): raise RuntimeError('Missing Strava credentials in .env')
 return cid,sec,ref
def get_access_token():
 cid,sec,ref=load_credentials();r=requests.post(TOKEN_URL,data={'client_id':cid,'client_secret':sec,'refresh_token':ref,'grant_type':'refresh_token'},timeout=30);r.raise_for_status();return r.json()['access_token']
def grid_bounds(sw_lat,sw_lng,ne_lat,ne_lng,rows,cols):
 lat_step=(ne_lat-sw_lat)/rows;lng_step=(ne_lng-sw_lng)/cols;count=0;total=rows*cols
 for r in range(rows):
  for c in range(cols):
   count+=1
   yield count,total,(sw_lat+r*lat_step,sw_lng+c*lng_step,sw_lat+(r+1)*lat_step,sw_lng+(c+1)*lng_step)
def fetch_segments(token,bounds,activity_type,min_cat,max_cat):
 headers={'Authorization':f'Bearer {token}'}
 params={'bounds':','.join(str(x) for x in bounds),'activity_type':activity_type,'min_cat':min_cat,'max_cat':max_cat}
 r=requests.get(EXPLORE_URL,headers=headers,params=params,timeout=30)
 if r.status_code==429:return 'RATE_LIMIT',[]
 r.raise_for_status();return 'OK',r.json().get('segments',[])
def collect_segments(bounds,rows=1,cols=1,activity_type='riding',min_cat=0,max_cat=5,delay=10.0,rate_limit_wait=90.0,max_rate_limit_retries=2):
 token=get_access_token();unique={}
 for i,total,b in grid_bounds(*bounds,rows,cols):
  retries=0
  while True:
   status,segments=fetch_segments(token,b,activity_type,min_cat,max_cat)
   if status=='RATE_LIMIT':
    if retries>=max_rate_limit_retries:break
    retries+=1;time.sleep(rate_limit_wait);continue
   for seg in segments:
    if 'id' in seg:unique[seg['id']]=seg
   break
  time.sleep(delay)
 return list(unique.values())
def make_map(segments,output):
 if not segments:raise RuntimeError('No segments found')
 pts=[]
 for seg in segments:
  if seg.get('start_latlng'):pts.append(seg['start_latlng'])
  if seg.get('end_latlng'):pts.append(seg['end_latlng'])
 center=[sum(p[0] for p in pts)/len(pts),sum(p[1] for p in pts)/len(pts)] if pts else [41.82,140.75]
 m=folium.Map(location=center,zoom_start=11,tiles=None)
 folium.TileLayer(tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',attr='Esri',name='Esri').add_to(m)
 fg=folium.FeatureGroup(name='Strava segments',show=True)
 for seg in segments:
  name=seg.get('name','Unnamed segment');seg_id=seg.get('id','')
  popup=f"<b>{name}</b><br><a href='https://www.strava.com/segments/{seg_id}' target='_blank'>Open in Strava</a>"
  if seg.get('points'):
   try:
    coords=polyline.decode(seg['points'])
    folium.PolyLine(coords,weight=4,opacity=.8,popup=folium.Popup(popup,max_width=320),tooltip=name).add_to(fg)
   except Exception:pass
 fg.add_to(m);folium.LayerControl().add_to(m);m.save(output)
def main():
 p=argparse.ArgumentParser();p.add_argument('--area',choices=sorted(AREAS.keys()),default='hakodate');p.add_argument('--output',default='docs/hakodate_segments.html');p.add_argument('--rows',type=int,default=1);p.add_argument('--cols',type=int,default=1);p.add_argument('--delay',type=float,default=10.0)
 a=p.parse_args();bounds=AREAS[a.area]['bounds'];segments=collect_segments(bounds=bounds,rows=a.rows,cols=a.cols,delay=a.delay);make_map(segments,a.output)
if __name__=='__main__':main()
