const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const prepare = require('./prepare-license-inventory.cjs');

const file = (name, sha = name) => ({path: name, sha, type: 'blob', mode: '100644'});
async function fixture(t, head, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'license-inventory-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  await fs.mkdir(path.join(root, 'old'), {recursive: true});
  await fs.writeFile(path.join(root, 'old/uv.lock'), 'old');
  await fs.writeFile(path.join(root, 'policy.yml'), 'trusted');
  const calls = [];
  const github = {rest: {git: {
    getTree: async ({tree_sha}) => ({data: {
      tree: tree_sha === 'base-sha' ? [file('old/uv.lock')] : head,
      truncated: tree_sha === 'head-sha' && options.truncated,
    }}),
    getBlob: async ({file_sha}) => {
      calls.push(file_sha);
      if (options.fetchError) throw new Error('API unavailable');
      return {data: {encoding: 'base64', content: Buffer.from(`content:${file_sha}`).toString('base64')}};
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
