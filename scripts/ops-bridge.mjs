// Reuse the upstream client and query registry unchanged.
import { OnePanelClient } from '../dist/scripts/client.js';
import { getModule } from '../dist/scripts/index.js';

const reads = [
  /^\/api\/v2\/containers\/(?:search|info|inspect|compose\/search)$/,
  /^\/api\/v2\/files\/content$/,
  /^\/api\/v2\/websites\/(?:search|list|proxies|\d+(?:\/config\/openresty|\/https)?|domains\/\d+)$/,
  /^\/api\/v2\/openresty(?:\/status)?$/,
  /^\/api\/v2\/cronjobs\/(?:search|\d+|next)$/,
  /^\/api\/v2\/logs\/tasks\/search$/,
];
const writes = new Set([
  '/api/v2/containers/compose', '/api/v2/containers/compose/operate', '/api/v2/containers/compose/update', '/api/v2/containers/operate',
  '/api/v2/websites', '/api/v2/websites/nginx/update', '/api/v2/websites/proxies/update',
  '/api/v2/websites/proxies/file', '/api/v2/openresty/file', '/api/v2/cronjobs',
  '/api/v2/cronjobs/update', '/api/v2/cronjobs/status',
]);
function fail(code, message) { throw Object.assign(new Error(message), { code }); }
function validateRequest(input) {
  const { method, path, body = {} } = input;
  if (!['GET', 'POST'].includes(method) || typeof path !== 'string' || /[%\\?#]/.test(path) || path.includes('..') || path.startsWith('//'))
    fail('DENIED', 'Invalid method or API path');
  if (!reads.some(re => re.test(path)) && !writes.has(path) && !/^\/api\/v2\/websites\/\d+\/https$/.test(path))
    fail('DENIED', 'This endpoint is not included in the operations scripts');
  if (path === '/api/v2/containers/compose/operate' && !['up','restart','rebuild'].includes(body.operation))
    fail('DENIED', 'Only non-deleting Compose lifecycle actions are supported');
  if (path === '/api/v2/containers/operate' && (!['start','stop','restart'].includes(body.operation) || !Array.isArray(body.names) || body.names.length !== 1))
    fail('DENIED', 'Only a single exact container start/stop/restart is supported');
  if (path === '/api/v2/websites/proxies/update' && !['create','edit'].includes(body.operate))
    fail('DENIED', 'Proxy deletion and generic operations are not supported');
  if (body && ['remove','delete','down','kill','prune','clean'].includes(body.operation || body.operate))
    fail('DENIED', 'Destructive operations are not supported');
  return { method, path, ...(input.body === undefined || input.body === null ? {} : {body: input.body}) };
}
function unwrap(response) {
  const data = response.data;
  if (response.status < 200 || response.status >= 300 ||
      (data && typeof data === 'object' && 'code' in data && ![0,200].includes(Number(data.code))))
    fail([401,403].includes(response.status) || [401,403].includes(Number(data?.code)) ? 'API_REJECTED' : 'API_ERROR', `1Panel rejected request (HTTP ${response.status}, business ${data?.code ?? 'unknown'}): ${String(data?.message || 'request failed').slice(0,500)}`);
  return data && typeof data === 'object' && Object.hasOwn(data, 'data') ? data.data : data;
}
let input = '';
try {
  for await (const part of process.stdin) {
    input += part;
    if (Buffer.byteLength(input) > 1024*1024) fail('BAD_INPUT','Input is too large');
  }
  const request = JSON.parse(input);
  if (!request || Array.isArray(request) || typeof request !== 'object') fail('BAD_INPUT','Input must be an object');
  const origin = new URL(process.env.ONEPANEL_BASE_URL || 'invalid');
  if (!['127.0.0.1','localhost','[::1]'].includes(origin.hostname) || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash)
    fail('CONFIG', 'The operations adapter connects only to the configured local 1Panel origin');
  const client = OnePanelClient.fromEnv(process.env);
  const timer = setTimeout(() => { console.log(JSON.stringify({ok:false,code:'API_UNCERTAIN',message:'Request deadline exceeded; verify a write before retrying'})); process.exit(1); }, 60000);
  let response;
  if (request.mode === 'query') {
    const module = getModule(request.module);
    const action = module?.actions?.[request.action];
    if (!action) fail('UNKNOWN_ACTION', 'Unknown upstream query action');
    const values = { ...request.input };
    if (request.module === 'containers' && request.action === 'searchContainers') {
      values.state ||= 'all'; values.orderBy ||= 'createdAt'; values.order ||= 'descending';
    }
    if (request.module === 'cronjobs' && request.action === 'searchCronjobs') values.orderBy ||= 'createdAt';
    if ('follow' in values) values.follow = false;
    if ('pageSize' in values) values.pageSize = Math.min(200, Math.max(1, Number(values.pageSize) || 20));
    const queryClient = { request: options => {
      if (typeof options.path !== 'string' || !options.path.startsWith('/api/v2/') || /[%\\?#]/.test(options.path) || options.path.includes('..')) fail('DENIED','Invalid query path');
      if (options.operateNode) fail('DENIED','Remote-node selection is not enabled');
      if (options.query?.follow) fail('DENIED','Unbounded streaming is disabled');
      return client.request(options);
    }};
    response = await action.execute(queryClient, values);
  } else if (request.mode === 'request') {
    response = await client.request(validateRequest(request));
  } else fail('BAD_INPUT','Unsupported bridge mode');
  clearTimeout(timer);
  console.log(JSON.stringify({ok:true,data:unwrap(response)}));
} catch (error) {
  let message = String(error.message || 'API request failed');
  if (process.env.ONEPANEL_API_KEY) message = message.replaceAll(process.env.ONEPANEL_API_KEY, '[REDACTED]');
  console.log(JSON.stringify({ok:false,code:error.code || 'API_UNCERTAIN',message}));
  process.exit(1);
}
