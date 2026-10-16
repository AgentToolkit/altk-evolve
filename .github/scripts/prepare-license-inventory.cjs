// Executed from the trusted base checkout, never from the PR's head revision.
// Only regular lockfile blobs are copied. No PR scripts, config or .env files run.
const fs = require('node:fs/promises');
const path = require('node:path');

function isLockfile(name) {
  return ['uv.lock', 'package-lock.json'].includes(path.posix.basename(name));
}

function safePath(name, root) {
  if (path.posix.isAbsolute(name) || name.split('/').some(part => !part || part === '.' || part === '..') || name.includes('\\')) {
    throw new Error(`Unsafe lockfile path: ${name}`);
  }
  return path.join(root, name);
}

async function lockfiles(github, repo, sha) {
  const {data} = await github.rest.git.getTree({...repo, tree_sha: sha, recursive: '1'});
  if (data.truncated) throw new Error('Truncated Git tree; cannot establish complete lockfile inventory');
  const files = data.tree.filter(entry => isLockfile(entry.path));
  if (!files.length) throw new Error('No tracked lockfiles found');
  for (const file of files) {
    if (file.type !== 'blob' || !['100644', '100755'].includes(file.mode)) {
      throw new Error(`Lockfile must be a regular file: ${file.path}`);
    }
  }
  return files;
}

module.exports = async function prepare({github, context, core}, side, root = process.cwd()) {
  if (!['base', 'head'].includes(side)) throw new Error('Expected base or head');
  const pr = context.payload.pull_request;
  // The base repository can resolve the PR head commit, including fork PRs.
  const base = await lockfiles(github, context.repo, pr.base.sha);
  const files = side === 'base' ? base : await lockfiles(github, context.repo, pr.head.sha);
  // Fetch everything before replacing the preceding inventory. API failures fail closed.
  const blobs = [];
  for (const file of files) {
    const {data} = await github.rest.git.getBlob({...context.repo, file_sha: file.sha});
    if (data.encoding !== 'base64') throw new Error(`Unexpected blob encoding: ${file.path}`);
    blobs.push([file.path, Buffer.from(data.content, 'base64')]);
  }
  for (const file of base) await fs.rm(safePath(file.path, root), {force: true});
  for (const [name, data] of blobs) {
    const destination = safePath(name, root);
    // Never follow a symlink inherited from the trusted checkout, either.
    let parent = path.dirname(destination);
    while (parent !== root) {
      const stat = await fs.lstat(parent).catch(error => {
        if (error.code !== 'ENOENT') throw error;
      });
      if (stat?.isSymbolicLink()) throw new Error(`Symlink in lockfile path: ${name}`);
      parent = path.dirname(parent);
    }
    const stat = await fs.lstat(destination).catch(error => {
      if (error.code !== 'ENOENT') throw error;
    });
    if (stat?.isSymbolicLink()) throw new Error(`Symlink lockfile: ${name}`);
    await fs.mkdir(path.dirname(destination), {recursive: true});
    await fs.writeFile(destination, data);
  }
  core.info(`Prepared ${files.length} ${side} lockfiles at ${pr[side].sha}: ${files.map(file => file.path).join(', ')}`);
};
