import io
import json
import unittest
import urllib.error
from app import create_app, CATEGORIES
from test_app import request, SETTINGS

CONFIG={**SETTINGS,'SAFETY_CONTENT_ENDPOINT':'https://safety.example','SAFETY_CONTENT_KEY':'test'}
def scores(category='Violence',value=0,matches=None):
    return {'categoriesAnalysis':[{'category':n,'severity':value if n==category else 0} for n in CATEGORIES], 'blocklistsMatch':matches or []}

class Guards(unittest.TestCase):
    def run_case(self,prompt=None,shield=False,completion=None,model=None,history=None):
        calls=[]
        def transport(req,timeout):
            calls.append(req.full_url)
            if 'text:analyze' in req.full_url:
                result=completion if any('/chat/completions' in c for c in calls) else prompt
                if result is None: result=scores()
            elif 'shieldPrompt' in req.full_url:
                result=shield if isinstance(shield,(dict,Exception)) else {'userPromptAnalysis':{'attackDetected':shield}}
            else: result=model or {'choices':[{'message':{'content':'Respuesta segura'},'finish_reason':'stop'}]}
            if isinstance(result,Exception): raise result
            return io.BytesIO(json.dumps(result).encode())
        result=request(create_app(CONFIG,transport),{'messages':history or [{'role':'user','content':'Hola'}]})
        return result, sum('/chat/completions' in c for c in calls)

    def test_each_category_boundary_and_model_call(self):
        for category in CATEGORIES:
            for value in (0,1,3,4,7):
                with self.subTest(category=category,value=value):
                    r,c=self.run_case(prompt=scores(category,value))
                    self.assertEqual(r['status'],403 if value>=4 else 200)
                    self.assertEqual(c,0 if value>=4 else 1)

    def test_prompt_shield(self):
        r,c=self.run_case(shield=True)
        self.assertEqual((r['status'],c),(403,0))
        self.assertFalse(r['body']['evidence']['gatewayProcessed'])

    def test_blocklist_zero_scores(self):
        r,c=self.run_case(prompt=scores(matches=[{'blocklistItemId':'demo'}]))
        self.assertEqual((r['status'],c),(403,0))

    def test_missing_invalid_and_unavailable_checks_never_call_model(self):
        for result in ({}, {'categoriesAnalysis':[{'category':'Hate','severity':0}]}, scores(value=8), scores(value=True), urllib.error.URLError('timeout')):
            with self.subTest(result=str(result)):
                r,c=self.run_case(prompt=result)
                self.assertEqual((r['status'],c),(503,0))
                self.assertEqual(r['body']['evidence']['result'],'ERROR')
        for shield in ({},urllib.error.URLError('timeout')):
            r,c=self.run_case(shield=shield)
            self.assertEqual((r['status'],c),(503,0))

    def test_response_categories_and_blocklist_are_withheld(self):
        for result in [scores(n,4) for n in CATEGORIES]+[scores(matches=[{'blocklistItemId':'demo'}])]:
            r,c=self.run_case(completion=result)
            self.assertEqual((r['status'],c),(403,1))
            self.assertNotIn('content',r['body'])
            self.assertFalse(r['body']['evidence']['responseDelivered'])
            self.assertEqual(r['body']['evidence']['blockStage'],'response')

    def test_failed_response_analysis_never_delivers(self):
        for result in ({},urllib.error.URLError('timeout')):
            r,c=self.run_case(completion=result)
            self.assertEqual((r['status'],c),(503,1))
            self.assertNotIn('content',r['body'])

    def test_llm_refusal_is_not_content_safety_block(self):
        r,c=self.run_case(model={'choices':[{'message':{'content':None,'refusal':'No puedo ayudar.'}}]})
        self.assertEqual((r['status'],c),(200,1))
        self.assertEqual(r['body']['evidence']['result'],'MODEL_REFUSAL')
        r,c=self.run_case(model={'choices':[{'message':{'content':'No puedo ayudar.'}}]})
        self.assertEqual(r['body']['evidence']['result'],'ALLOWED')

    def test_backend_filter_attribution(self):
        r,c=self.run_case(model={'choices':[{'finish_reason':'content_filter','message':{'content':'oculto'}}]})
        self.assertEqual((r['status'],c),(403,1))
        self.assertEqual(r['body']['evidence']['decisionSource'],'Filtro del backend LLM')
        self.assertIsNone(r['body']['evidence']['policyApplied'])

    def test_no_truncation_of_context_and_full_history_moderated(self):
        seen=[]
        def transport(req,timeout):
            payload=json.loads(req.data)
            if 'text:analyze' in req.full_url:
                seen.append(payload['text']); data=scores(value=4)
            else: data={'userPromptAnalysis':{'attackDetected':False}}
            return io.BytesIO(json.dumps(data).encode())
        messages=[{'role':'user','content':'contexto previo'},{'role':'assistant','content':'respuesta anterior'},{'role':'user','content':'Hola'}]
        r=request(create_app(CONFIG,transport),{'messages':messages})
        self.assertEqual(r['status'],403)
        self.assertEqual(seen,['contexto previo\nrespuesta anterior\nHola'])
        r,c=self.run_case(history=[{'role':'user','content':'a'*4000},{'role':'assistant','content':'b'*4000},{'role':'user','content':'c'*4000}])
        self.assertEqual((r['status'],c),(413,0))

if __name__=='__main__': unittest.main()
