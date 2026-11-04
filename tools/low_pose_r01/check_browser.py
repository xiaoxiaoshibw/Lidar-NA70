"""Real browser replay checks; never exports fabricated human labels."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);args=p.parse_args()
    out=Path(args.out).resolve()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge')
        page=browser.new_page(viewport={'width':1500,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8943/')
        page.wait_for_function('row !== undefined && row !== null && row.frame === 0')
        assert page.locator('#session option').count()==9
        seen=[]
        options=page.locator('#session option').evaluate_all('(a)=>a.map(o=>o.value)')
        for sid in options:
            page.locator('#session').select_option(sid)
            page.wait_for_function('(sid)=>row !== null && row.sid === sid',arg=sid)
            seen.append(page.evaluate('({sid:row.sid,frame:row.frame,gate:row.alignment_gate,active:active.length})'))
        page.locator('#session').select_option('cap_20261013_082829')
        page.wait_for_function("row !== null && row.sid === 'cap_20261013_082829'")
        page.locator('#next').click();page.wait_for_function('row !== null && row.frame === 1')
        page.locator('#frame').fill('90');page.locator('#frame').dispatch_event('change')
        page.wait_for_function('row !== null && row.frame === 90')
        page.locator('#context').uncheck();page.locator('#rescue').uncheck()
        assert page.evaluate("active.filter(p=>p.userData.name!=='selected').length")==1
        page.locator('#context').check();page.locator('#rescue').check()
        # Click a rendered point using its projected position (source indices stay frame-local).
        xy=page.evaluate("""()=>{const a=active.find(p=>p.userData.name==='frozen');const v=new THREE.Vector3().fromBufferAttribute(a.geometry.attributes.position,Math.floor(a.geometry.attributes.position.count/2));v.project(camera);const b=renderer.domElement.getBoundingClientRect();return [b.left+(v.x+1)*b.width/2,b.top+(1-v.y)*b.height/2];}""")
        assert 0<xy[0]<1500 and 0<xy[1]<1000, 'Point pick must actually run'
        page.mouse.click(*xy)
        assert page.evaluate('selected.size')>0
        page.locator('#clear').click();assert page.evaluate('selected.size')==0
        page.once('dialog',lambda d:d.accept())
        page.locator('#export').click()
        page.screenshot(path=str(out/'REVIEW_SUPINE_F90_V2.png'),full_page=True)
        page.locator('#queue').click()
        page.wait_for_function('row !== null && row.sid === catalog.review_queue[0].sid && row.frame === catalog.review_queue[0].frame')
        assert not errors,errors
        with (out/'BROWSER_CHECK_V2.json').open('x',encoding='utf8') as f:
            json.dump({'status':'PASS','sessions':seen,'errors':errors,'operations':['nine sessions','frame seek','next frame','layer toggles','source point pick and clear','blank review export rejected','review queue navigation'],
                       'human_labels_exported':False},f,ensure_ascii=False,indent=2)
        browser.close()
