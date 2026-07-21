#!/usr/bin/env python3
"""Serve the local gallery and persist batch-review feedback."""
import argparse,json
from datetime import datetime,timezone
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'.artifacts'/'batch_feedback.json'
REFERENCE_OUT=ROOT/'.artifacts'/'reference_feedback.json'
PREFERENCES=ROOT/'scripts'/'preferences.json'
CURATION_EVENTS=ROOT/'.artifacts'/'curation_events.jsonl'
DELETED_PIECES=ROOT/'scripts'/'deleted_pieces.json'

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def do_POST(self):
        if self.path=='/api/feedback-event':
            return self.save_feedback_event()
        if self.path=='/api/react':
            return self.save_reactions()
        if self.path not in {'/api/batch-feedback','/api/reference-feedback'}:self.send_error(404);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size>2_000_000:raise ValueError('payload too large')
            data=json.loads(self.rfile.read(size))
            reviews=data.get('reviews')
            if not isinstance(reviews,dict):raise ValueError('reviews must be an object')
            allowed={'keep','evolve','reject'}
            for value in reviews.values():
                decisions=value.get('decisions',[])
                if not isinstance(decisions,list) or any(x not in allowed for x in decisions):raise ValueError('invalid decisions')
                if 'reject' in decisions and len(decisions)>1:raise ValueError('reject is exclusive')
            target=REFERENCE_OUT if self.path=='/api/reference-feedback' else OUT
            data['saved_at']=datetime.now(timezone.utc).isoformat();target.parent.mkdir(parents=True,exist_ok=True)
            tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(target)
            body=json.dumps({'ok':True,'path':str(target.relative_to(ROOT))}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except Exception as e:
            body=json.dumps({'ok':False,'error':str(e)}).encode();self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

    def save_feedback_event(self):
        """Append raw feedback independently from deployable preference state."""
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size>100_000:raise ValueError('payload too large')
            event=json.loads(self.rfile.read(size))
            if not event.get('id') or not event.get('piece_id') or event.get('action') not in {'keep','evolve','remove','undo-remove'}:raise ValueError('invalid feedback event')
            if CURATION_EVENTS.exists():
                for line in CURATION_EVENTS.read_text().splitlines():
                    try:
                        if json.loads(line).get('id')==event['id']:
                            body=json.dumps({'ok':True,'id':event['id'],'duplicate':True}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
                    except json.JSONDecodeError:continue
            event['recorded_at']=datetime.now(timezone.utc).isoformat()
            CURATION_EVENTS.parent.mkdir(parents=True,exist_ok=True)
            with CURATION_EVENTS.open('a') as stream:stream.write(json.dumps(event,separators=(',',':'))+'\n')
            body=json.dumps({'ok':True,'id':event['id']}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except Exception as e:
            body=json.dumps({'ok':False,'error':str(e)}).encode();self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

    def save_reactions(self):
        """Atomically apply a verified curation batch to local preferences."""
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size>1_000_000:raise ValueError('payload too large')
            data=json.loads(self.rfile.read(size))
            actions=data.get('actions') or [{'piece_id':data.get('piece_id'),'action':data.get('action')}]
            allowed={'favorite','unfavorite','dismiss','undismiss','star','unstar'}
            if not actions or any(not x.get('piece_id') or x.get('action') not in allowed for x in actions):raise ValueError('invalid actions')
            prefs=json.loads(PREFERENCES.read_text()) if PREFERENCES.exists() else {'marks':{}}
            marks=prefs.setdefault('marks',{})
            for item in actions:
                piece_id,action=item['piece_id'],item['action'];mark=marks.get(piece_id,{})
                if action=='favorite':mark['favorite']=True;mark.pop('drop',None)
                elif action=='unfavorite':mark.pop('favorite',None)
                elif action=='dismiss':mark['drop']=True;mark.pop('favorite',None);mark.pop('star',None)
                elif action=='undismiss':mark.pop('drop',None)
                elif action=='star':mark['star']=True
                elif action=='unstar':mark.pop('star',None)
                if mark:marks[piece_id]=mark
                else:marks.pop(piece_id,None)
            prefs['updated_at']=datetime.now(timezone.utc).date().isoformat()
            tmp=PREFERENCES.with_suffix('.tmp');tmp.write_text(json.dumps(prefs,indent=2)+'\n');tmp.replace(PREFERENCES)
            new_tombstones={x['piece_id'] for x in actions if x['action']=='dismiss'}
            if new_tombstones:
                deleted=json.loads(DELETED_PIECES.read_text()) if DELETED_PIECES.exists() else {'version':1,'ids':[]}
                deleted['ids']=sorted(set(deleted.get('ids',[]))|new_tombstones)
                deleted['updated_at']=datetime.now(timezone.utc).date().isoformat()
                deleted_tmp=DELETED_PIECES.with_suffix('.tmp');deleted_tmp.write_text(json.dumps(deleted,indent=2)+'\n');deleted_tmp.replace(DELETED_PIECES)
            body=json.dumps({'ok':True,'applied':len(actions),'path':str(PREFERENCES.relative_to(ROOT))}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except Exception as e:
            body=json.dumps({'ok':False,'error':str(e)}).encode();self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=8787);args=ap.parse_args();server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);print(f'Review UI: http://localhost:{args.port}/batch-review.html');print(f'Feedback:  {OUT}');server.serve_forever()
if __name__=='__main__':main()
