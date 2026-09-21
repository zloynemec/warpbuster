import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('counter loads asynchronously and initializes the supplied ID and options', () => {
  const inserted = [];
  const anchor = { parentNode: { insertBefore: (script) => inserted.push(script) } };
  const document = {
    currentScript: { dataset: { counterId: '12345678' } },
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
  assert.equal(inserted[0].src, 'https://mc.yandex.ru/metrika/tag.js?id=12345678');
  const [id, action, options] = context.ym.a[0];
  assert.equal(id, 12345678);
  assert.equal(action, 'init');
  assert.equal(options.webvisor, true);
  assert.equal(options.clickmap, true);
  assert.equal(options.trackLinks, true);
  assert.equal(options.url, context.location.href);
  assert.equal(options.referrer, document.referrer);
});

for (const value of [undefined, '', '0', '-1', 'abc', '1.5', '9007199254740992']) {
  test(`disabled or invalid counter (${value}) never loads Yandex`, () => {
    const context = { document: {
      currentScript: value === undefined ? null : { dataset: { counterId: value } },
      createElement: () => { throw new Error('must not load a script'); },
    } };
    context.window = context;
    vm.runInNewContext(readFileSync(new URL('../dist/assets/metrika.js', import.meta.url), 'utf8'), context);
    assert.equal(context.ym, undefined);
  });
}
