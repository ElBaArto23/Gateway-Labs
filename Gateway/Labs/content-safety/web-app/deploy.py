"""Despliega solo el nuevo App Service en el grupo existente del laboratorio."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web-chat' if (ROOT / 'web-chat').is_dir() else ROOT


def azure(*args):
    executable = shutil.which('az')
    if not executable:
        raise RuntimeError('No se encontró Azure CLI. Instálala y ejecuta az login.')
    result = subprocess.run([executable, *args, '--only-show-errors', '-o', 'json'], capture_output=True)
    if result.returncode:
        # Los parámetros secretos se pasan por archivo y no se imprimen.
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())
    try:
        output = result.stdout.decode('utf-8-sig')
    except UnicodeDecodeError:
        output = result.stdout.decode('cp1252')
    return json.loads(output) if output.strip() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource-group', default='lab-content-safety-V2')
    parser.add_argument('--model', required=True, action='append', dest='models', help='Nombre de un modelo desplegado, igual que un elemento de models_config[i]["name"]. Repetir la opción para varios (p.ej. --model gpt-5.6-luna --model Phi-4); el primero queda como modelo por defecto del selector.')
    parser.add_argument('--apim-subscription', default='subscription1')
    parser.add_argument('--apim-name', default='')
    parser.add_argument('--webapp-name', default='')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    group = azure('group', 'show', '--name', args.resource_group)
    resources = azure('resource', 'list', '--resource-group', args.resource_group, '--resource-type', 'Microsoft.ApiManagement/service')
    apims = [r for r in resources if not args.apim_name or r['name'] == args.apim_name]
    if len(apims) != 1:
        raise RuntimeError('Indica --apim-name: no se encontró un único API Management en este grupo.')
    cognitive = azure('resource', 'list', '--resource-group', args.resource_group, '--resource-type', 'Microsoft.CognitiveServices/accounts')
    content_safety = [r for r in cognitive if r.get('kind') == 'ContentSafety']
    if len(content_safety) != 1:
        raise RuntimeError('No se encontró un único recurso Azure AI Content Safety en este grupo.')
    parameters = {'apimName': apims[0]['name'], 'apimSubscriptionName': args.apim_subscription, 'modelNames': args.models, 'contentSafetyName': content_safety[0]['name'], 'location': group['location']}
    policy_resource = azure('resource', 'show', '--ids', apims[0]['id']+'/apis/inference-api/policies/policy', '--api-version', '2024-05-01')
    policy = ET.fromstring(policy_resource['properties']['value']).find('./inbound/llm-content-safety')
    if policy is None:
        raise RuntimeError('No está desplegada la política llm-content-safety del lab.')
    categories = {c.attrib['name']: int(c.attrib['threshold']) for c in policy.findall('./categories/category')}
    if set(categories) != {'Hate','Violence','Sexual','SelfHarm'} or len(set(categories.values())) != 1:
        raise RuntimeError('La demo requiere las cuatro categorías con un umbral uniforme.')
    if policy.get('shield-prompt') != 'true' or policy.get('enforce-on-completions') != 'true' or policy.find('./categories').get('output-type') != 'EightSeverityLevels' or 'blocklist1' not in [x.text for x in policy.findall('./blocklists/id')]:
        raise RuntimeError('La política debe habilitar Prompt Shield, completions, escala 0–7 y blocklist1 para esta demo.')
    parameters['safetyThreshold'] = next(iter(categories.values()))
    if args.webapp_name:
        parameters['webAppName'] = args.webapp_name
    print(f'Grupo existente: {group["name"]}; API Management: {apims[0]["name"]}', flush=True)
    print('Se creará un App Service Linux y un plan B1 en este grupo. El plan tiene coste en Azure.', flush=True)
    with tempfile.TemporaryDirectory(prefix='safety-deploy-') as tmp:
        params_file = Path(tmp) / 'parameters.json'
        params_file.write_text(json.dumps({'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#','contentVersion':'1.0.0.0','parameters':{k:{'value':v} for k,v in parameters.items()}}), encoding='utf-8')
        azure('deployment','group','validate','--resource-group',args.resource_group,'--template-file',str(ROOT/'appservice.bicep'),'--parameters','@'+str(params_file))
        print('Plantilla validada.', flush=True)
        if args.validate_only:
            return
        deployment = azure('deployment','group','create','--name','content-safety-web','--resource-group',args.resource_group,'--mode','Incremental','--template-file',str(ROOT/'appservice.bicep'),'--parameters','@'+str(params_file))
        outputs = deployment['properties']['outputs']
        name = outputs['webAppName']['value']
        url = outputs['webAppUrl']['value']
        archive = Path(tmp) / 'web-chat.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for filename in ['app.py','app.js','styles.css','evidence.css','index.html','requirements.txt']:
                z.write(WEB/filename, filename)
        print(f'Publicando código en {name}…', flush=True)
        azure('webapp','deploy','--resource-group',args.resource_group,'--name',name,'--src-path',str(archive),'--type','zip','--timeout','600000')
        with urllib.request.urlopen(url + '/health', timeout=60) as response:
            if json.load(response).get('status') != 'ok':
                raise RuntimeError('El despliegue terminó, pero la comprobación de salud no fue satisfactoria.')
        print(f'Aplicación desplegada: {url}')
        print('La aplicación queda accesible directamente por HTTPS.')
        print('El control de salud no prueba la inferencia: entra en la aplicación y envía un mensaje para verificar Azure.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(f'No se completó el despliegue: {exc}')
