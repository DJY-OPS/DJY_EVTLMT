"""Read public AWS pages and exercise only rejected mutations; never send live fixes."""
import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.error
import urllib.request


def check(base):
    def request(path, payload=None):
        headers={'Content-Type':'application/json','Origin':base}
        req=urllib.request.Request(base+path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,r.read()
        except urllib.error.HTTPError as e:return e.code,e.read()

    pages={}
    for path in ('/driver','/pit','/comms','/api/driver-message','/battery','/map','/phone','/records','/onboard','/coach','/tv',
                 '/api/telemetry','/api/gnss/live?after=999999999999','/api/gnss/runs',
                 '/api/tuning/live?after=999999999999','/api/battery/live?after=999999999999','/api/battery/prediction','/api/records/status'):
        status,body=request(path);pages[path]=status
        assert status==200,(path,status)
    rejected={}
    assert request('/api/recording/status')[0]==403,'Raw recording metadata must stay local'
    for path,payload,expected in (('/api/gnss/control',{'action':'gate'},400),
                                  ('/api/gnss/control',{'action':'collect'},403),
                                  ('/api/control',{},403),('/api/test',{},403),('/api/driver-message',{},400),
                                  ('/api/vehicle/exchange',{},401),('/api/records/control',{},400)):
        status,_=request(path,payload);rejected[path+':'+str(payload)]=status
        assert status==expected,(path,status,expected)
    def viewer(index):
        values=[]
        for _ in range(10):
            start=time.monotonic()
            status,_=request(('/api/telemetry' if index%2 else '/api/battery/live?after=999999999999'))
            assert status==200,status
            values.append((time.monotonic()-start)*1000)
            time.sleep(.2)
        return values
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        latency=sorted(v for result in pool.map(viewer,range(5)) for v in result)
    return dict(base=base,pages=pages,rejected_mutations=rejected,concurrent_viewers=5,requests=len(latency),
                median_ms=round(statistics.median(latency),1),p95_ms=round(latency[int(len(latency)*.95)],1))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='https://13.209.166.150')
    args=parser.parse_args();print(json.dumps(check(args.base.rstrip('/')),ensure_ascii=False,indent=2))
