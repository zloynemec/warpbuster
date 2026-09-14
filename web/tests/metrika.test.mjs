import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('counter loads asynchronously and initializes the supplied ID and options', () => {
  const inserted = [];
  const anchor = { parentNode: { insertBefore: (script) => inserted.push(script) } };
  const document = {
    scripts: [],
    referrer: 'https://example.test/',
    createElement: () => ({}),
    getElementsByTagName: () => [anchor],
  };
  const context = { document, location: { href: 'https://warpbuster.ru/faq' } };
  context.window = context;
  vm.runInNewContext(readFileSync(new URL('../dist/assets/metrika.js', import.meta.url), 'utf8'), context);
  assert.equal(inserted.length, 1);
  assert.equal(inserted[0].async, 1);
  assert.equal(inserted[0].src, 'https://mc.yandex.ru/metrika/tag.js?id=112575529');
  const [id, action, options] = context.ym.a[0];
  assert.equal(id, 112575529);
  assert.equal(action, 'init');
  assert.equal(options.webvisor, true);
  assert.equal(options.clickmap, true);
  assert.equal(options.trackLinks, true);
  assert.equal(options.url, context.location.href);
  assert.equal(options.referrer, document.referrer);
});
