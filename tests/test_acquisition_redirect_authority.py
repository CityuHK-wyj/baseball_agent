"""Owned redirect controls: credentials and fixed-provider identity are not transferable."""
from datetime import datetime, timezone
import httpx
import pytest
from app.web.transport import request
from app.capabilities.dynamic_fact import DynamicFactTool
from tests.test_shared_safe_transport import install, Chunks


@pytest.mark.parametrize('secret_header',['X-Subscription-Token','Ocp-Apim-Subscription-Key','X-Custom-Provider-Secret'])
def test_cross_origin_redirect_preserves_only_neutral_headers(monkeypatch,secret_header):
    seen=[]
    def handler(req):
        seen.append(req)
        if len(seen)==1:return httpx.Response(302,headers={'location':'https://other.example/result'})
        return httpx.Response(200,stream=Chunks(b'ok'))
    install(monkeypatch,handler)
    response=request('GET','https://owned.example/start',headers={secret_header:'owned-fixture-secret','Accept':'text/plain'})
    assert response.status_code==200 and len(seen)==2
    assert secret_header not in seen[1].headers
    assert seen[1].headers['Accept']=='text/plain'


@pytest.mark.parametrize('target',['http://owned.example/result','http://other.example/result'])
def test_https_redirect_cannot_downgrade_transport_authority(monkeypatch,target):
    seen=[]
    def handler(req):
        seen.append(req)
        if len(seen)==1:return httpx.Response(302,headers={'location':target})
        return httpx.Response(200,stream=Chunks(b'ok'))
    install(monkeypatch,handler)
    with pytest.raises(ValueError,match='downgrade'):
        request('GET','https://owned.example/start',headers={'Authorization':'owned-fixture-secret'})
    assert len(seen)==1


@pytest.mark.parametrize('loop',[True,False])
def test_redirect_failure_retains_actual_attempt_telemetry(monkeypatch,loop):
    seen=[]
    def handler(req):
        seen.append(req)
        target='https://owned.example/start' if loop else f'https://owned.example/{len(seen)}'
        return httpx.Response(302,headers={'location':target})
    install(monkeypatch,handler)
    with pytest.raises(ValueError) as raised:request('GET','https://owned.example/start')
    assert raised.value.acquisition['http_attempts']==len(seen)==(1 if loop else 5)


def test_fixed_native_provider_does_not_relabel_redirected_json_as_official(monkeypatch):
    seen=[]
    def handler(req):
        seen.append(req)
        if len(seen)==1:return httpx.Response(302,headers={'location':'https://other.example/result'})
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Chunks(
            b'{"people":[{"id":592450,"fullName":"Aaron Judge","currentTeam":{"id":999,"name":"Forged Team"}}]}'))
    install(monkeypatch,handler)
    fact=DynamicFactTool(None)._current_team(592450,datetime.now(timezone.utc).date().isoformat())
    assert fact is None
    assert len(seen)==1
