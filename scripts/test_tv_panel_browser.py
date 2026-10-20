"""Browser test with intercepted requests only: never connects to a vehicle/server."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'gateway'))
from control_catalog import catalog

def main():
    state = dict(control_client_allowed=True, online=True, stm_online=True,
                 live_control_allowed=True, fault_code=0, tv_applied_pct=35,
                 tv_limit_pct=60, rear_command_rx=12, rear_command_errors=0,
                 _tuning_values={'pid_kp':30,'pid_ki':2,'pid_kd':5})
    commands, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width':1100,'height':1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            path = request.request.url.replace('http://tv.test', '')
            if path in ('/tv', '/tv/app.js', '/tv/tv.css', '/tuning/dashboard.css'):
                asset = 'tv/index.html' if path == '/tv' else path.lstrip('/')
                request.fulfill(body=(ROOT/'web'/asset).read_text(encoding='utf8'),
                                content_type='text/javascript' if asset.endswith('.js') else 'text/css' if asset.endswith('.css') else 'text/html')
            elif path == '/api/control/catalog':
                request.fulfill(json=catalog(state))
            elif path == '/api/tv':
                request.fulfill(json=state)
            elif path == '/api/control':
                payload=request.request.post_data_json
                assert payload['type']=='parameter_update' and payload['schema_version']==1
                assert payload['persistence']=='volatile_lease'
                commands.append({'request_id':payload['request_id'],**payload['values']})
                request.fulfill(json={'ok':True})
            else:
                request.abort()
        page.route('**/*', route)
        page.goto('http://tv.test/tv')
        page.wait_for_function("!document.getElementById('hold').disabled")
        page.wait_for_timeout(300)
        assert not commands, 'Opening the page must not send commands'
        assert page.locator('#pidParameters .value').all_text_contents()==['30','2','5']
        assert page.locator('#pidParameters input').count()==0
        assert page.locator('#futureParameters button').count()==0
        assert page.locator('body').evaluate("el=>getComputedStyle(el).backgroundColor")=='rgb(0, 0, 0)'
        page.locator('#strength').fill('80');page.locator('#limit').fill('50')
        assert page.locator('#target').inner_text()=='50%'
        page.locator('#strength').fill('35');page.locator('#limit').fill('60')
        button = page.locator('#hold');button.hover();page.mouse.down()
        page.wait_for_timeout(650)
        assert len(commands) >= 3 and all(c['strength_pct']==35 and c['limit_pct']==60 for c in commands)
        assert len({c['request_id'] for c in commands}) == 1
        assert page.locator('#strength').is_disabled()
        page.mouse.up();page.wait_for_timeout(250)
        assert commands[-1]['strength_pct']==0 and not commands[-1]['tv_enable']
        count=len(commands);page.wait_for_timeout(450);assert len(commands)==count
        # Positive commands blocked on vehicle fault; neutral diagnostic remains available.
        state['fault_code']=1;page.wait_for_timeout(400);assert button.is_disabled()
        page.locator('#strength').fill('0');assert button.is_enabled()
        state['fault_code']=0;page.locator('#strength').fill('40');page.wait_for_timeout(400)
        button.hover();page.mouse.down();page.wait_for_timeout(220)
        state['online']=False;page.wait_for_timeout(500);page.mouse.up()
        assert commands[-1]['strength_pct']==0 and button.is_disabled()
        assert page.locator('#pidParameters .value').all_text_contents()==['—','—','—']
        count=len(commands);state['online']=True;page.wait_for_timeout(500)
        assert len(commands)==count, 'Reconnect must not restart control'
        # Focus loss also releases the active command.
        button.hover();page.mouse.down();page.wait_for_timeout(200)
        page.evaluate("window.dispatchEvent(new Event('blur'))")
        page.wait_for_timeout(250);page.mouse.up()
        assert commands[-1]['strength_pct']==0
        state['control_client_allowed']=False;page.wait_for_timeout(400)
        assert button.is_disabled()
        state['control_client_allowed']=True;state['fault_code']=1
        page.locator('#strength').fill('0');page.wait_for_timeout(400)
        output=ROOT/'.local/tv-panel-browser.png';output.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(output),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(100)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(ROOT/'.local/tv-panel-mobile.png'),full_page=True)
        assert not errors, errors
        browser.close()
    print(json.dumps({'result':'PASS','mock_commands':len(commands),
                      'checks':['no auto send','hold refresh','release neutral','fault gate',
                                'offline release','no restart on reconnect','blur release','public read-only'],
                      'screenshot':str(output)},ensure_ascii=False))

if __name__ == '__main__': main()
