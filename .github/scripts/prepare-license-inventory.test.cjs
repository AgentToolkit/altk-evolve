const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const prepare = require('./prepare-license-inventory.cjs');

const file = (name, sha = name) => ({path: name, sha, type: 'blob', mode: '100644', size: Buffer.byteLength(`content:${sha}`)});
async function fixture(t, head, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'license-inventory-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  await fs.mkdir(path.join(root, 'old'), {recursive: true});
  await fs.writeFile(path.join(root, 'old/uv.lock'), 'old');
  await fs.writeFile(path.join(root, 'policy.yml'), 'trusted');
  const calls = [];
  const github = {rest: {git: {
    getTree: async ({tree_sha}) => ({data: {
      tree: tree_sha === 'base-sha' ? (options.base || [file('old/uv.lock')]) : head,
      truncated: tree_sha === 'head-sha' && options.truncated,
    }}),
    getBlob: async ({file_sha}) => {
      calls.push(file_sha);
      if (options.fetchError) throw new Error('API unavailable');
      return {data: {encoding: options.encoding || 'base64', content: Buffer.from(`content:${file_sha}`).toString('base64')}};
    },
  }}};
  const context = {repo: {owner: 'base-owner', repo: 'repo'}, payload: {pull_request: {
    base: {sha: 'base-sha'}, head: {sha: 'head-sha', repo: {owner: {login: 'fork-owner'}}},
  }}};
  return {root, calls, api: {github, context, core: {info() {}}}};
}

test('copies all nested lockfiles, removes deleted base files, and leaves policy trusted', async t => {
  const f = await fixture(t, [file('uv.lock'), file('plugins/example/uv.lock'), file('ui/package-lock.json'), file('.env'), file('policy.yml')]);
  await prepare(f.api, 'head', f.root);
  assert.deepEqual(f.calls, ['uv.lock', 'plugins/example/uv.lock', 'ui/package-lock.json']);
  assert.equal(await fs.readFile(path.join(f.root, 'plugins/example/uv.lock'), 'utf8'), 'content:plugins/example/uv.lock');
  assert.equal(await fs.readFile(path.join(f.root, 'policy.yml'), 'utf8'), 'trusted');
  await assert.rejects(fs.access(path.join(f.root, 'old/uv.lock')));
  await assert.rejects(fs.access(path.join(f.root, '.env')));
});

test('base inventory uses the exact base revision', async t => {
  const f = await fixture(t, [file('uv.lock')]);
  await prepare(f.api, 'base', f.root);
  assert.deepEqual(f.calls, ['old/uv.lock']);
});

for (const [name, head, options, message] of [
  ['symlink', [{...file('uv.lock'), mode: '120000'}], {}, /regular file/],
  ['truncated', [file('uv.lock')], {truncated: true}, /Truncated/],
  ['empty', [], {}, /No tracked/],
  ['API failure', [file('uv.lock')], {fetchError: true}, /API unavailable/],
  ['traversal', [file('../uv.lock')], {}, /Unsafe/],
]) {
  test(`rejects ${name}`, async t => {
    const f = await fixture(t, head, options);
    await assert.rejects(prepare(f.api, 'head', f.root), message);
  });
}

test('refuses to follow a parent symlink', async t => {
  const f = await fixture(t, [file('linked/uv.lock')]);
  await fs.symlink(os.tmpdir(), path.join(f.root, 'linked'));
  await assert.rejects(prepare(f.api, 'head', f.root), /Symlink/);
});

for (const [name, entries, options, message] of [
  ['late unsafe path', [file('uv.lock'), file('../uv.lock')], {}, /Unsafe/],
  ['too many files', Array.from({length: 26}, (_, i) => file(`${i}/uv.lock`)), {}, /Too many/],
  ['oversize inventory', [{...file('uv.lock'), size: 20 * 1024 * 1024 + 1}], {}, /byte limit/],
  ['missing size', [{...file('uv.lock'), size: undefined}], {}, /Invalid/],
  ['unsafe base path', [file('uv.lock')], {base: [file('../uv.lock')]}, /Unsafe/],
  ['absolute path', [file('/uv.lock')], {}, /Unsafe/],
  ['non-blob', [{...file('uv.lock'), type: 'tree'}], {}, /regular/],
  ['late symlink', [file('uv.lock'), {...file('other/uv.lock'), mode: '120000'}], {}, /regular/],
]) {
  test(`validates inventories before fetching: ${name}`, async t => {
    const f = await fixture(t, entries, options);
    await assert.rejects(prepare(f.api, 'base', f.root), message);
    assert.deepEqual(f.calls, []);
    assert.equal(await fs.readFile(path.join(f.root, 'old/uv.lock'), 'utf8'), 'old');
  });
}

test('rejects unexpected encoding and incorrect blob sizes before writing', async t => {
  for (const options of [{encoding: 'utf-8'}, {}]) {
    const f = await fixture(t, [{...file('uv.lock'), size: 1}], options);
    await assert.rejects(prepare(f.api, 'head', f.root), /encoding|size mismatch/);
    assert.equal(await fs.readFile(path.join(f.root, 'old/uv.lock'), 'utf8'), 'old');
  }
});

test('normalizes a root with trailing slash', async t => {
  const f = await fixture(t, [file('nested/uv.lock')]);
  await prepare(f.api, 'head', f.root + '/');
  assert.equal(await fs.readFile(path.join(f.root, 'nested/uv.lock'), 'utf8'), 'content:nested/uv.lock');
});

test('refuses to follow a destination symlink', async t => {
  const f = await fixture(t, [file('uv.lock')]);
  await fs.symlink(path.join(f.root, 'policy.yml'), path.join(f.root, 'uv.lock'));
  await assert.rejects(prepare(f.api, 'head', f.root), /Symlink/);
  assert.equal(await fs.readFile(path.join(f.root, 'policy.yml'), 'utf8'), 'trusted');
});

test('only the main-target workflow can emit the privileged license check', async () => {
  const privileged = await fs.readFile(path.join(__dirname, '../workflows/check-licenses.yaml'), 'utf8');
  const readonly = await fs.readFile(path.join(__dirname, '../workflows/test-license-inventory.yaml'), 'utf8');
  assert.match(privileged, /on:\n  pull_request_target:\n    branches: \[main\]/);
  assert.doesNotMatch(privileged, /^  pull_request:/m);
  assert.doesNotMatch(privileged, /^    if:/m);
  assert.match(privileged, /cancel-in-progress: true/);
  assert.match(readonly, /on:\n  pull_request:/);
  assert.doesNotMatch(readonly, /license-check:|contents: write|pull_request_target:/);
});
