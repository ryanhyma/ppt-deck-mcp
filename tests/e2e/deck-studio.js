/**
 * E2E regression test for the Deck Studio MCP App.
 *
 * Pins the failures that only exist in a browser:
 *   1. the App iframe must load (sandbox origin, frame-ancestors, CSP)
 *   2. slide images must actually load (resourceDomains CSP + CORS on the asset origin)
 *   3. the template gallery must render 5 styles
 *   4. the style panel and the per-phrase controls must exist (old App snapshots must not break it)
 *   5. the Download .pptx button must open the deck (an IP-literal public base is silently blocked)
 *
 * Requires the dev stack up (LibreChat :3080, MCP :8931) and a conversation containing an App.
 *   CONVO=<conversation-id> node tests/e2e/deck-studio.js
 *   node tests/e2e/deck-studio.js <conversation-id>
 */
const { chromium } = require('playwright');

const BASE = process.env.LIBRECHAT_URL || 'http://localhost:3080';
const EMAIL = process.env.LIBRECHAT_EMAIL || 'hyma@local.test';
const PASSWORD = process.env.LIBRECHAT_PASSWORD || 'PiTest!2026';
const CONVO = process.argv[2] || process.env.CONVO;

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ' — ' + detail : ''}`);
}

(async () => {
  if (!CONVO) {
    console.error('usage: CONVO=<conversationId> node tests/e2e/deck-studio.js');
    process.exit(2);
  }
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);
    await page.locator('input[name="email"], #email').first().fill(EMAIL);
    await page.locator('input[name="password"], #password').first().fill(PASSWORD);
    await page.locator('button[type="submit"]').first().click();
    await page.waitForTimeout(6000);

    await page.goto(`${BASE}/c/${CONVO}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(6000);

    const close = page.getByRole('button', { name: /close app/i }).first();
    if (await close.count()) { await close.click(); await page.waitForTimeout(1500); }

    const open = page.getByRole('button', { name: /open app/i }).last();
    check('conversation has an App attachment', (await open.count()) > 0);
    if (!(await open.count())) throw new Error('nothing to open');

    await open.click();
    await page.waitForTimeout(6000);

    // The App frame is a srcdoc iframe; Playwright reports its url as '' or
    // 'about:srcdoc' depending on version, so identify it by its own DOM.
    let frame = null;
    for (const f of page.frames()) {
      try {
        if (await f.evaluate(() => Boolean(document.getElementById('viewer')))) frame = f;
      } catch { /* cross-origin or detached */ }
    }
    check('App iframe rendered (sandbox origin + frame-ancestors + CSP)', Boolean(frame));
    if (!frame) throw new Error('no app frame');

    const images = frame.locator('img[data-slide]');
    const count = await images.count();
    check('slide thumbnails present', count > 0, `${count} slides`);

    // Thumbnails are lazy-loaded, so scroll them into view before measuring.
    const loaded = await frame.evaluate(async () => {
      const imgs = Array.from(document.querySelectorAll('img[data-slide]'));
      for (const img of imgs) {
        img.scrollIntoView({ block: 'center' });
        await new Promise((r) => setTimeout(r, 150));
      }
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline && imgs.some((i) => i.naturalWidth === 0)) {
        await new Promise((r) => setTimeout(r, 200));
      }
      return {
        total: imgs.length,
        complete: imgs.filter((i) => i.complete && i.naturalWidth > 0).length,
      };
    });
    check('thumbnails load from the MCP origin (CSP resourceDomains + CORS)',
      loaded.total > 0 && loaded.complete === loaded.total,
      `${loaded.complete}/${loaded.total} loaded`);

    const styleCards = frame.locator('.style-card');
    check('template gallery renders 5 styles', (await styleCards.count()) === 5,
      `${await styleCards.count()} cards`);

    await frame.locator('figure').first().hover();
    await page.waitForTimeout(300);
    // Controls sit below each slide, never on top of it (WCAG 2.1.1 / 1.4.13).
    const styleBtn = frame.locator('figure .card-actions button').first();
    const below = await frame.evaluate(() => {
      const fig = document.querySelector('figure');
      if (!fig) return null;
      const thumb = fig.querySelector('.thumb').getBoundingClientRect();
      const bar = fig.querySelector('.card-actions').getBoundingClientRect();
      return { gap: Math.round(bar.top - thumb.bottom) };
    });
    check('slide controls sit below the thumbnail', Boolean(below) && below.gap >= 0,
      below ? `${below.gap}px below` : 'no figure');
    await styleBtn.click();
    await page.waitForTimeout(500);
    check('style panel opens', await frame.locator('#si-apply').isVisible());
    check('per-phrase controls present (style_text UI)',
      (await frame.locator('#st-phrase').count()) === 1 && (await frame.locator('#st-apply').count()) === 1);
    await frame.locator('#si-cancel').click();
    await page.waitForTimeout(300);

    // Slide viewer: modal, keyboard-navigable, focus returns to the trigger.
    const thumbs = frame.locator('button.thumb');
    await thumbs.nth(1).click();
    await page.waitForTimeout(600);
    const viewer = frame.locator('#viewer');
    check('slide viewer opens as a modal dialog',
      await viewer.evaluate((el) => el.open === true && el.tagName === 'DIALOG'));
    const firstTitle = await frame.locator('#viewer-title').innerText();
    check('viewer starts on the clicked slide', /2\./.test(firstTitle), firstTitle);
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(250);
    const nextTitle = await frame.locator('#viewer-title').innerText();
    check('ArrowRight advances the viewer', nextTitle !== firstTitle, nextTitle);
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(250);
    check('ArrowLeft goes back', (await frame.locator('#viewer-title').innerText()) === firstTitle);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    check('Escape closes the viewer', await viewer.evaluate((el) => el.open === false));
    check('focus returns to the thumbnail that opened it',
      await frame.evaluate(() => document.activeElement?.classList.contains('thumb') === true));

    // Download button: the host must actually open the .pptx. An IP-literal public base is
    // silently blocked by the MCP App bridge, so this pins the hostname requirement end to end.
    const context = page.context();
    const downloadBtn = frame.locator('button.download');
    const hasDownload = (await downloadBtn.count()) === 1;
    check('Download .pptx button present', hasDownload);
    let filename = null;
    const onDownload = (d) => { filename = d.suggestedFilename(); };
    page.on('download', onDownload);
    context.on('page', (p) => p.on('download', onDownload));
    try {
      if (hasDownload) {
        await downloadBtn.click();
        await page.waitForTimeout(5000);
      }
    } finally {
      page.off('download', onDownload);
    }
    check('Download button triggers a .pptx download',
      filename === 'deck.pptx', filename ? `got ${filename}` : 'no download event');

    await frame.locator('#edit-cancel').click().catch(() => {});
    await page.screenshot({ path: '/tmp/e2e-deck-studio.png' });
  } catch (err) {
    check('run completed', false, String(err).slice(0, 200));
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})();
