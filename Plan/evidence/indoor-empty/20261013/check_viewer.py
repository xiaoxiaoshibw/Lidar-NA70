"""Browser check: local viewer renders and view/measurement controls work."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge')
    page = browser.new_page(viewport={'width': 1200, 'height': 900})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto((root / 'viewer.html').as_uri())
    page.locator('#top').click()
    page.locator('#stable').check()
    page.locator('#stable').uncheck()
    page.locator('#orbit').click()
    point = page.evaluate('projected.find(o=>o.q[0]>100&&o.q[0]<900&&o.q[1]>80&&o.q[1]<550).q.slice(0,2)')
    page.locator('canvas').click(position={'x': point[0], 'y': point[1]})
    assert '原始回波平均斜距' in page.locator('#read').inner_text()
    page.screenshot(path=str(root / 'preview.png'), full_page=True)
    assert not errors, errors
    (root / 'browser_check.json').write_text(json.dumps({'passed': True, 'errors': errors,
        'measurement': page.locator('#read').inner_text()}, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
print('Viewer and measurement checks passed')
