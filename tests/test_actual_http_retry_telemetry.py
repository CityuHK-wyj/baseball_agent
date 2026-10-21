"""Owned provider: actual HTTP sends are distinct from application/SDK calls."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
import pytest
from app.config import LLMConfig
from app.llm.client import LLMClient, LLMError


@pytest.mark.parametrize('mode,application,http_attempts,retries,success',[
    ('500',2,4,2,False),('400',2,2,0,False),('retry_success',1,2,1,True),
    ('malformed',2,2,0,False),('success',1,1,0,True),
])
def test_owned_provider_exact_attempt_accounting(mode,application,http_attempts,retries,success):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            received.append(self.headers.get('x-stainless-retry-count'))
            code=500 if mode=='500' or mode=='retry_success' and len(received)==1 else 400 if mode=='400' else 200
            payload={'error':{'message':'owned error'}} if code!=200 else {
                'id':'owned','object':'chat.completion','model':'fixture','choices':[
                    {'index':0,'message':{'role':'assistant','content':'invalid' if mode=='malformed' else '{"ok":true}'},'finish_reason':'stop'}]}
            raw=json.dumps(payload).encode()
            self.send_response(code);self.send_header('content-type','application/json')
            self.send_header('content-length',str(len(raw)));self.send_header('retry-after','0');self.end_headers()
            self.wfile.write(raw)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=LLMClient(LLMConfig(api_key='owned-fixture',base_url=f'http://127.0.0.1:{server.server_port}/v1',provider_max_retries=1))
    try:
        if success: assert client.complete_json(role='analytics',system='s',user='u').data=={'ok':True}
        else:
            with pytest.raises(LLMError): client.complete_json(role='analytics',system='s',user='u')
        assert len(received)==client.transport_attempts==http_attempts
        assert client.application_attempts==client.provider_attempts==application
        assert client.provider_retry_attempts==retries
        assert client.usage_records[-1]['http_attempts']==http_attempts
    finally:
        if client._client: client._client.close()
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_timeout_counts_each_actual_send_even_without_response():
    import openai
    actual=[]
    def timeout(req):
        actual.append(req)
        raise httpx.ReadTimeout('owned timeout',request=req)
    client=LLMClient(LLMConfig(api_key='fixture',provider_max_retries=1))
    client._client=openai.OpenAI(api_key='fixture',base_url='http://owned.example/v1',max_retries=1,
                                http_client=httpx.Client(transport=httpx.MockTransport(timeout)))
    client._client._sleep_for_retry=lambda **kwargs:None
    with pytest.raises(LLMError): client.complete_json(role='semantic',system='s',user='u')
    assert len(actual)==client.transport_attempts==4
    assert client.application_attempts==client.provider_attempts==2
    assert client.provider_retry_attempts==2
    client._client.close()
