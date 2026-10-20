// Executed from the trusted base checkout, never from the PR's head revision.
// Only regular lockfile blobs are copied. No PR scripts, config or .env files run.
const fs = require('node:fs/promises');
const path = require('node:path');

const MAX_FILES = 25;
const MAX_BYTES = 20 * 1024 * 1024;

function isLockfile(name) {
  return ['uv.lock', 'package-lock.json'].includes(path.posix.basename(name));
}

function safePath(name, root) {
  if (path.posix.isAbsolute(name) || name.split('/').some(part => !part || part === '.' || part === '..') || name.includes('\\')) {
    throw new Error(`Unsafe lockfile path: ${name}`);
  }
  return path.join(root, name);
}

async function lockfiles(github, repo, sha, root) {
  const {data} = await github.rest.git.getTree({...repo, tree_sha: sha, recursive: '1'});
  if (data.truncated) throw new Error('Truncated Git tree; cannot establish complete lockfile inventory');
  const files = data.tree.filter(entry => isLockfile(entry.path));
  if (!files.length) throw new Error('No tracked lockfiles found');
  if (files.length > MAX_FILES) throw new Error("Too many lockfiles");
  let bytes = 0;
  for (const file of files) {
    safePath(file.path, root);
    if (!Number.isSafeInteger(file.size) || file.size < 0) throw new Error("Invalid lockfile size");
    bytes += file.size;
    if (bytes > MAX_BYTES) throw new Error("Lockfile inventory exceeds byte limit");
    if (file.type !== 'blob' || !['100644', '100755'].includes(file.mode)) {
      throw new Error(`Lockfile must be a regular file: ${file.path}`);
    }
  }
  return files;
}

module.exports = async function prepare({github, context, core}, side, root = process.cwd()) {
  if (!['base', 'head'].includes(side)) throw new Error('Expected base or head');
  root = path.resolve(root);
  const pr = context.payload.pull_request;
  // The base repository can resolve the PR head commit, including fork PRs.
  const base = await lockfiles(github, context.repo, pr.base.sha, root);
  const head = await lockfiles(github, context.repo, pr.head.sha, root);
  const files = side === 'base' ? base : head;
  // Fetch everything before replacing the preceding inventory. API failures fail closed.
  const blobs = [];
  let bytes = 0;
  for (const file of files) {
    const {data} = await github.rest.git.getBlob({...context.repo, file_sha: file.sha});
    if (data.encoding !== 'base64') throw new Error(`Unexpected blob encoding: ${file.path}`);
    if (typeof data.content !== 'string' || data.content.length > Math.ceil(MAX_BYTES / 3) * 4 + MAX_BYTES / 50) {
      throw new Error('Encoded lockfile exceeds byte limit');
    }
    const content = Buffer.from(data.content, 'base64');
    bytes += content.length;
    if (content.length !== file.size || bytes > MAX_BYTES) throw new Error('Lockfile blob size mismatch or byte limit exceeded');
    blobs.push([file.path, content]);
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
