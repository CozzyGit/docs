#!/usr/bin/env python3
"""In-memory Northstar REST mock. Standard library only; FOR LOCAL DEMOS ONLY.

Start: python3 mock_api.py
URL:   http://localhost:8787/v1
Token: ns_demo_key

Not designed for production use. No TLS, secret management, persistent storage,
rate limiting or outgoing webhook delivery. CORS is permissive for local demos.
"""
from datetime import datetime, date, timezone
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from threading import RLock
import json
import uuid
import re
import os

HOST = "127.0.0.1"
PORT = int(os.getenv('NORTHSTAR_PORT', '8787'))
TOKEN = 'ns_demo_key'
MAX_BODY_BYTES = 1024 * 1024
lock = RLock()

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

def make_id(prefix):
    return f'{prefix}_{uuid.uuid4().hex[:10]}'

db = {
    'customers': [
        {'id': 'cus_7a2b9c', 'name':'Acme Robotics', 'email':'admin@acme.example',
         'plan':'growth', 'status':'active','external_id':'crm_acme_001',
         'created_at':'2026-11-16T16:00:00Z'},
    ],
    'events': [
        {'id':'evt_89fa2d','customer_id':'cus_7a2b9c','name':'feature.used',
         'properties':{'feature':'dashboard','source':'web'},
         'occurred_at':'2026-11-16T16:20:00Z','received_at':'2026-11-16T16:20:01Z'},
    ],
    'webhooks': [
        {'id':'whk_24d9fa','url':'https://hooks.acme.example/northstar',
         'events':['customer.created','event.received'], 'enabled':True,
         'created_at':'2026-11-16T16:30:00Z'},
    ],
    'idempotency': {},
}
ALLOWED_PLANS = {'starter','growth','enterprise'}
ALLOWED_STATUS = {'active','trialing','paused'}
ALLOWED_HOOK_EVENTS = {'customer.created','customer.updated','event.received'}

class ApiError(Exception):
    def __init__(self,status,code,message):
        self.status,self.code,self.message=status,code,message


def expect(ok,msg,code='invalid_request',status=422):
    if not ok:
        raise ApiError(status,code,msg)


def parse_iso_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError,ValueError):
        raise ApiError(422,'invalid_date','Dates must be valid YYYY-MM-DD strings.')


def validate_customer(body,update=False):
    allowed={'name','email','plan','external_id'} if not update else {'name','plan','status','external_id'}
    expect(isinstance(body,dict),'JSON request body must be an object.')
    expect(not (set(body)-allowed), f'Unexpected fields: {", ".join(sorted(set(body)-allowed))}.')
    if update:
        expect(bool(body),'Provide at least one field to update.')
    else:
        expect('name' in body and 'email' in body,'name and email are required.')
    if 'name' in body:
        expect(isinstance(body['name'],str) and 1 <= len(body['name']) <= 120, 'name must be a string of 1-120 characters.')
    if 'email' in body:
        expect(isinstance(body['email'],str) and bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',body['email'])), 'email must be valid.')
    if 'plan' in body:
        expect(body['plan'] in ALLOWED_PLANS,'plan must be starter, growth, or enterprise.')
    if 'status' in body:
        expect(body['status'] in ALLOWED_STATUS,'status must be active, trialing, or paused.')
    if 'external_id' in body:
        expect(body['external_id'] is None or (isinstance(body['external_id'],str) and len(body['external_id'])<=128),'external_id must be a string (up to 128 characters) or null.')


def validate_event(body):
    expect(isinstance(body,dict),'JSON request body must be an object.')
    expect('customer_id' in body and 'name' in body, 'customer_id and name are required.')
    expect(isinstance(body['customer_id'],str),'customer_id must be a string.')
    expect(isinstance(body['name'],str) and bool(body['name'].strip()),'name must not be empty.')
    expect(isinstance(body.get('properties',{}),dict),'properties must be an object.')
    if 'occurred_at' in body:
        expect(isinstance(body['occurred_at'],str),'occurred_at must be an ISO 8601 timestamp.')
        try:
            datetime.fromisoformat(body['occurred_at'].replace('Z','+00:00'))
        except ValueError:
            raise ApiError(422,'invalid_timestamp','occurred_at must be an ISO 8601 timestamp.')


def validate_webhook(body):
    expect(isinstance(body,dict),'JSON request body must be an object.')
    expect(isinstance(body.get('url'),str),'url is required.')
    u=urlparse(body['url'])
    expect(u.scheme=='https' and bool(u.netloc),'webhook url must be a valid HTTPS URL.')
    e=body.get('events')
    expect(isinstance(e,list) and len(e)>0 and all(isinstance(v,str) and v in ALLOWED_HOOK_EVENTS for v in e) and len(e)==len(set(e)),
           'events must be a non-empty list of unique supported event names.')
    expect(isinstance(body.get('enabled',True),bool),'enabled must be a boolean.')


class Handler(BaseHTTPRequestHandler):
    server_version = 'NorthstarMock/1.0'

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(),fmt % args),flush=True)

    def common_headers(self, status, size=0):
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(size))
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type, Idempotency-Key')
        self.send_header('Access-Control-Allow-Methods','GET, POST, PATCH, DELETE, OPTIONS')
        self.send_header('Cache-Control','no-store')
        self.end_headers()

    def reply(self, status, payload=None):
        body = b'' if status==204 else json.dumps(payload,ensure_ascii=False).encode('utf8')
        self.common_headers(status,len(body))
        if body: self.wfile.write(body)

    def error_reply(self, status, code, message):
        self.reply(status, {'error':{'code':code,'message':message,'request_id':make_id('req')}})

    def do_OPTIONS(self):
        self.common_headers(204)

    def do_GET(self): self.dispatch('GET')
    def do_POST(self): self.dispatch('POST')
    def do_PATCH(self): self.dispatch('PATCH')
    def do_DELETE(self): self.dispatch('DELETE')

    def read_json(self):
        ct=self.headers.get('Content-Type','')
        if 'application/json' not in ct:
            raise ApiError(400,'invalid_content_type','Use Content-Type: application/json.')
        try:
            n=int(self.headers.get('Content-Length','0'))
        except ValueError:
            raise ApiError(400,'bad_request','Invalid Content-Length.')
        if n>MAX_BODY_BYTES:
            raise ApiError(413,'payload_too_large','Body exceeds 1MB.')
        try:
            return json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400,'invalid_json','Malformed JSON request body.')

    def paginate(self, values, query):
        try:
            limit=int(query.get('limit',['20'])[0])
        except ValueError:
            raise ApiError(422,'invalid_limit','limit must be an integer between 1 and 100.')
        expect(1<=limit<=100, 'limit must be between 1 and 100.')
        cursor=query.get('cursor',[None])[0]
        start=0
        if cursor:
            match=re.fullmatch('cursor_([0-9]+)',cursor)
            expect(bool(match),'Invalid pagination cursor.')
            start=int(match.group(1))
        portion=values[start:start+limit]
        has_more=start+limit<len(values)
        return {'data':portion, 'pagination':{'has_more':has_more,'next_cursor':f'cursor_{start+limit}' if has_more else None}}

    def dispatch(self,method):
        try:
            if self.headers.get('Authorization') != f'Bearer {TOKEN}':
                raise ApiError(401,'unauthorized','A valid bearer API token is required.')
            parsed=urlparse(self.path)
            path=parsed.path.rstrip('/')
            query=parse_qs(parsed.query)
            with lock:
                self.handle_route(method,path,query)
        except ApiError as e:
            self.error_reply(e.status,e.code,e.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print('Unexpected mock error:',repr(e),flush=True)
            try: self.error_reply(500,'internal_error','An unexpected error occurred in the mock.')
            except (BrokenPipeError, ConnectionResetError): pass

    def handle_route(self,method,path,query):
        if path == '/v1/customers':
            if method=='GET': return self.reply(200,self.paginate(db['customers'],query))
            if method=='POST':
                body=self.read_json()
                duplicate=self.cached_request(method,path,body)
                if duplicate: return self.reply(duplicate[0],duplicate[1])
                validate_customer(body)
                customer={**body,'id':make_id('cus'),'plan':body.get('plan','starter'),'status':'active','external_id':body.get('external_id'), 'created_at':now()}
                db['customers'].append(customer)
                self.save_request(method,path,body,201,customer)
                return self.reply(201,customer)
        if path.startswith('/v1/customers/'):
            ident=path.split('/')[-1]
            customer=next((r for r in db['customers'] if r['id']==ident),None)
            if not customer: raise ApiError(404,'not_found','Customer was not found.')
            if method=='GET': return self.reply(200,customer)
            if method=='PATCH':
                body=self.read_json();validate_customer(body,update=True)
                customer.update(body)
                return self.reply(200,customer)
        if path=='/v1/events':
            if method=='GET':
                events=list(reversed(db['events']))
                ident=query.get('customer_id',[None])[0]
                if ident: events=[e for e in events if e['customer_id']==ident]
                return self.reply(200,self.paginate(events,query))
            if method=='POST':
                body=self.read_json()
                duplicate=self.cached_request(method,path,body)
                if duplicate: return self.reply(duplicate[0],duplicate[1])
                validate_event(body)
                if not any(c['id']==body['customer_id'] for c in db['customers']):
                    raise ApiError(404,'not_found','Customer was not found.')
                event={'id':make_id('evt'),'customer_id':body['customer_id'],'name':body['name'],'properties':body.get('properties',{}),
                       'occurred_at':body.get('occurred_at',now()),'received_at':now()}
                db['events'].append(event)
                self.save_request(method,path,body,201,event)
                return self.reply(201,event)
        if path=='/v1/usage/summary' and method=='GET':
            ident=query.get('customer_id',[None])[0]
            start=query.get('from',[None])[0]
            end=query.get('to',[None])[0]
            if not ident or not start or not end:
                raise ApiError(400,'bad_request','customer_id, from, and to are required.')
            begin,finish=parse_iso_date(start),parse_iso_date(end)
            expect(begin<=finish,'from must be on or before to.')
            if not any(c['id']==ident for c in db['customers']):
                raise ApiError(404,'not_found','Customer was not found.')
            events=[]
            for e in db['events']:
                try: d=datetime.fromisoformat(e['occurred_at'].replace('Z','+00:00')).date()
                except ValueError: continue
                if e['customer_id']==ident and begin<=d<=finish: events.append((d,e))
            counts=Counter(e['name'] for d,e in events)
            return self.reply(200,{
                'customer_id':ident,'period_start':str(begin),'period_end':str(finish),
                'total_events':len(events),'active_days':len(set(d for d,e in events)),
                'top_events':[{'name':k,'count':v} for k,v in counts.most_common(5)]})
        if path=='/v1/webhooks':
            if method=='GET': return self.reply(200,{'data':db['webhooks']})
            if method=='POST':
                body=self.read_json()
                duplicate=self.cached_request(method,path,body)
                if duplicate: return self.reply(duplicate[0],duplicate[1])
                validate_webhook(body)
                hook={'id':make_id('whk'),'url':body['url'],'events':body['events'],'enabled':body.get('enabled',True),'created_at':now()}
                db['webhooks'].append(hook)
                self.save_request(method,path,body,201,hook)
                return self.reply(201,hook)
        if path.startswith('/v1/webhooks/'):
            ident=path.split('/')[-1]
            hook=next((r for r in db['webhooks'] if r['id']==ident),None)
            if not hook: raise ApiError(404,'not_found','Webhook destination was not found.')
            if method=='GET': return self.reply(200,hook)
            if method=='DELETE':
                db['webhooks'].remove(hook)
                return self.reply(204)
        raise ApiError(404,'not_found','No matching endpoint exists.')

    def cached_request(self,method,path,body):
        key=self.headers.get('Idempotency-Key')
        if not key: return None
        expect(len(key)<=128,'Idempotency-Key must be 128 characters or fewer.')
        stored=db['idempotency'].get((method,path,key))
        if not stored: return None
        if stored['body']!=body:
            raise ApiError(409,'idempotency_conflict','Idempotency-Key was used with a different request body.')
        return stored['status'],stored['response']

    def save_request(self,method,path,body,status,response):
        key=self.headers.get('Idempotency-Key')
        if key:
            db['idempotency'][(method,path,key)]={'body':body,'status':status,'response':response}


if __name__=='__main__':
    print(f'Northstar API mock running at http://{HOST}:{PORT}/v1')
    print('Token: ns_demo_key  (DEMO ONLY — do not use in production)')
    print('Press Ctrl+C to exit. Data resets when the server restarts.')
    try: ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
    except KeyboardInterrupt: print('\nMock stopped.')
