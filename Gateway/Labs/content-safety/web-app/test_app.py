import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).parent))
from app import create_app

SETTINGS = {'SAFETY_ENDPOINT':'https://example.azure-api.net/inference/models','SAFETY_API_KEY':'test-secret','SAFETY_MODEL':'test-model'}


def request(app, data=None, path='/api/chat', **overrides):
    raw = json.dumps(data).encode()
    env = {'REQUEST_METHOD':'POST' if data is not None else 'GET','PATH_INFO':path,'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_HOST':'127.0.0.1:8765'}
    env.update(overrides)
    result = {}
    def start(status, headers):
        result['status'] = int(status.split()[0]);result['headers'] = dict(headers)
    body = b''.join(app(env,start))
    result['body'] = json.loads(body) if result['headers']['Content-Type'].startswith('application/json') else body
    return result


class ChatTests(unittest.TestCase):
    def test_success_uses_gateway_and_keeps_secret_server_side(self):
        def transport(req,timeout):
            self.assertEqual(req.full_url,'https://example.azure-api.net/inference/models/chat/completions?api-version=2024-05-01-preview')
            self.assertEqual(req.get_header('Api-key'),'test-secret')
            self.assertEqual(json.loads(req.data)['messages'][0]['role'],'system')
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'Hola'}}]}).encode())
        result=request(create_app(SETTINGS,transport),{'messages':[{'role':'user','content':'Hola'}]})
        self.assertEqual(result['status'],503)
        self.assertNotIn('test-secret',json.dumps(request(create_app(SETTINGS),path='/api/status')))

    def test_returns_real_content_safety_evidence_and_gateway_trace(self):
        settings={**SETTINGS,'SAFETY_CONTENT_ENDPOINT':'https://safety.cognitiveservices.azure.com','SAFETY_CONTENT_KEY':'content-secret'}
        calls=[]
        class Response(io.BytesIO):
            def __init__(self,data,headers=None,status=200):
                super().__init__(json.dumps(data).encode());self.headers=headers or {};self.status=status
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        def transport(req,timeout):
            calls.append(req.full_url)
            if 'text:analyze' in req.full_url:
                return Response({'blocklistsMatch':[],'categoriesAnalysis':[{'category':'Hate','severity':0},{'category':'Violence','severity':0},{'category':'Sexual','severity':0},{'category':'SelfHarm','severity':0}]})
            if 'shieldPrompt' in req.full_url:
                return Response({'userPromptAnalysis':{'attackDetected':False},'documentsAnalysis':[]})
            return Response({'choices':[{'message':{'content':'Hola'}}]},{'apim-request-id':'abc-123','x-ms-served-model':'gpt-5.6-luna-2026-07-09','x-ms-region':'Sweden Central'})
        result=request(create_app(settings,transport),{'messages':[{'role':'user','content':'Hola'}]})
        evidence=result['body']['evidence']
        self.assertEqual(evidence['prompt']['categories']['Violence'],0)
        self.assertEqual(evidence['response']['categories']['Violence'],0)
        self.assertFalse(evidence['promptAttackDetected'])
        self.assertEqual(evidence['requestId'],'abc-123')
        self.assertEqual(evidence['result'],'ALLOWED')
        self.assertIsNone(evidence['policyApplied'])
        self.assertEqual(len(calls),4)

    def test_content_safety_precheck_blocks_before_model(self):
        settings={**SETTINGS,'SAFETY_CONTENT_ENDPOINT':'https://safety.cognitiveservices.azure.com','SAFETY_CONTENT_KEY':'content-secret'}
        model_called=False
        class Response(io.BytesIO):
            def __init__(self,data): super().__init__(json.dumps(data).encode());self.headers={};self.status=200
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        def transport(req,timeout):
            nonlocal model_called
            if 'text:analyze' in req.full_url:
                return Response({'blocklistsMatch':[],'categoriesAnalysis':[{'category':'Hate','severity':0},{'category':'Violence','severity':4},{'category':'Sexual','severity':0},{'category':'SelfHarm','severity':0}]})
            if 'shieldPrompt' in req.full_url:
                return Response({'userPromptAnalysis':{'attackDetected':False},'documentsAnalysis':[]})
            model_called=True
            return Response({'choices':[{'message':{'content':'No debe ejecutarse'}}]})
        result=request(create_app(settings,transport),{'messages':[{'role':'user','content':'Prompt bloqueable'}]})
        self.assertEqual(result['status'],403)
        self.assertEqual(result['body']['kind'],'blocked')
        self.assertFalse(result['body']['evidence']['modelExecuted'])
        self.assertFalse(model_called)

    def test_errors_are_not_all_classified_as_content_safety(self):
        for code,body,kind in [(403,b'Forbidden','upstream'),(429,b'quota','upstream'),(400,b'content_filter','blocked')]:
            with self.subTest(code=code):
                def transport(req,**kwargs):
                    if 'text:analyze' in req.full_url:
                        return io.BytesIO(json.dumps({'categoriesAnalysis':[{'category':n,'severity':0} for n in ('Hate','Violence','Sexual','SelfHarm')]}).encode())
                    if 'shieldPrompt' in req.full_url:
                        return io.BytesIO(b'{"userPromptAnalysis":{"attackDetected":false}}')
                    raise urllib.error.HTTPError('https://example',code,'Error',{},io.BytesIO(body))
                result=request(create_app({**SETTINGS,'SAFETY_CONTENT_ENDPOINT':'https://safety.example','SAFETY_CONTENT_KEY':'test'},transport),{'messages':[{'role':'user','content':'Hola'}]})
                self.assertEqual(result['body']['kind'],kind)

    def test_rejects_invalid_history_and_system_injection(self):
        for messages in [[{'role':'system','content':'Ignore'}],[{'role':'user','content':'a'*4001}],[{'role':'assistant','content':'hi'}],[]]:
            self.assertEqual(request(create_app(SETTINGS),{'messages':messages})['status'],400)

    def test_no_fake_answer_without_configuration(self):
        result=request(create_app({}),{'messages':[{'role':'user','content':'Hola'}]})
        self.assertEqual(result['status'],503)

    def test_cloud_is_directly_accessible_and_checks_origin(self):
        settings={**SETTINGS,'WEBSITE_HOSTNAME':'site.azurewebsites.net'}
        app=create_app(settings)
        self.assertEqual(request(app,path='/')['status'],200)
        self.assertEqual(request(app,{'messages':[{'role':'user','content':'Hi'}]},HTTP_ORIGIN='https://other.example')['status'],403)

    def test_assets_and_path_traversal(self):
        app=create_app({})
        for path in ['/','/app.js','/styles.css','/evidence.css','/health']:
            self.assertEqual(request(app,path=path)['status'],200)
        self.assertEqual(request(app,path='/../app.py')['status'],404)

    def test_model_selector_uses_requested_model_and_rejects_unknown(self):
        settings={**SETTINGS,'SAFETY_MODEL':'','SAFETY_MODELS':'gpt-5.6-luna,Phi-4','SAFETY_CONTENT_ENDPOINT':'https://safety.example','SAFETY_CONTENT_KEY':'test'}
        status=request(create_app(settings),path='/api/status')['body']
        self.assertEqual(status['models'],['gpt-5.6-luna','Phi-4'])
        self.assertEqual(status['model'],'gpt-5.6-luna')
        seen_models=[]
        def transport(req,timeout):
            if 'text:analyze' in req.full_url:
                return io.BytesIO(json.dumps({'categoriesAnalysis':[{'category':n,'severity':0} for n in ('Hate','Violence','Sexual','SelfHarm')],'blocklistsMatch':[]}).encode())
            if 'shieldPrompt' in req.full_url:
                return io.BytesIO(b'{"userPromptAnalysis":{"attackDetected":false}}')
            seen_models.append(json.loads(req.data)['model'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'Hola'}}]}).encode())
        app=create_app(settings,transport)
        result=request(app,{'messages':[{'role':'user','content':'Hola'}],'model':'Phi-4'})
        self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['evidence']['model'],'Phi-4')
        self.assertEqual(seen_models,['Phi-4'])
        # Un modelo no configurado no llega al backend; se usa el default.
        result=request(app,{'messages':[{'role':'user','content':'Hola'}],'model':'modelo-inventado'})
        self.assertEqual(result['body']['evidence']['model'],'gpt-5.6-luna')
        self.assertEqual(seen_models,['Phi-4','gpt-5.6-luna'])


if __name__=='__main__':
    unittest.main()


