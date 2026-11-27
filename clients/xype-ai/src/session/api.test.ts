// The REST client against a loopback server: request shapes, bearer tokens, and errors
// that carry a status but never the response body.

import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

import { afterEach, describe, expect, it } from 'vitest';

import { ApiError, AuthApi, EndpointError } from './api';
import { grpcTransport } from './transport';

type Handler = (request: IncomingMessage, body: string, response: ServerResponse) => void;

let server: Server | null = null;
afterEach(async () => {
  const open = server;
  server = null;
  if (open === null) return;
  open.closeAllConnections();
  await new Promise((resolve) => open.close(resolve));
});

async function api(handler: Handler): Promise<AuthApi> {
  server = createServer((request, response) => {
    let body = '';
    request.on('data', (chunk: Buffer) => (body += chunk.toString()));
    request.on('end', () => {
      handler(request, body, response);
    });
  });
  await new Promise<void>((resolve) => server?.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return new AuthApi({ baseUrl: `http://127.0.0.1:${String(port)}` });
}

const json = (response: ServerResponse, status: number, data: unknown) => {
  response.writeHead(status, { 'content-type': 'application/json' });
  response.end(JSON.stringify(data));
};

describe('AuthApi', () => {
  it('logs in with the client credentials and reads the token pair', async () => {
    let seen: unknown = null;
    const client = await api((request, body, response) => {
      seen = [request.method, request.url, JSON.parse(body)];
      json(response, 200, { access_token: 'a', refresh_token: 'r', expires_in: 900 });
    });
    expect(await client.token('xc_1', 'secret')).toEqual({
      accessToken: 'a',
      refreshToken: 'r',
      expiresInS: 900,
    });
    expect(seen).toEqual([
      'POST',
      '/api/v1/auth/token',
      { client_id: 'xc_1', client_secret: 'secret' },
    ]);
  });

  it('sends the bearer token and the public key for a session key', async () => {
    let seen: unknown = null;
    const client = await api((request, body, response) => {
      seen = [request.headers.authorization, JSON.parse(body)];
      json(response, 200, {
        key_id: 'k',
        server_public_key: Buffer.alloc(32, 1).toString('base64'),
        wrapped_key: Buffer.alloc(60, 2).toString('base64'),
        expires_at: '2026-11-26T00:00:00Z',
        repository_keys: [{ repository_id: 'r', hmac_key_id: 'h', wrapped_key: 'AAAA' }],
      });
    });
    const minted = await client.sessionKey('tok', new Uint8Array(32).fill(3), ['r']);
    expect(seen).toEqual([
      'Bearer tok',
      { client_public_key: Buffer.alloc(32, 3).toString('base64'), repository_ids: ['r'] },
    ]);
    expect([minted.keyId, minted.wrappedKey.length, minted.repositoryKeys[0]?.hmacKeyId]).toEqual([
      'k',
      60,
      'h',
    ]);
  });

  it('fails with the status only, never the body, and refuses malformed answers', async () => {
    const client = await api((request, _body, response) => {
      if (request.url === '/api/v1/auth/me') json(response, 200, { user_id: 'u' });
      else json(response, 401, { detail: 'secret-detail' });
    });
    const failure = await client.refresh('spent').catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(401);
    expect((failure as ApiError).message).not.toContain('secret-detail');
    await expect(client.me('tok')).rejects.toMatchObject({ status: 502 });
  });

  it('refuses an oversized answer', async () => {
    const client = await api((_request, _body, response) => {
      json(response, 200, {
        access_token: 'a'.repeat(70 * 1024),
        refresh_token: 'r',
        expires_in: 1,
      });
    });
    await expect(client.token('xc_1', 's')).rejects.toBeInstanceOf(ApiError);
  });

  it('refuses plain http to any host but this machine', () => {
    expect(() => new AuthApi({ baseUrl: 'http://server.example:8443' })).toThrow(EndpointError);
    expect(() => grpcTransport({ baseUrl: 'http://10.0.0.5' })).toThrow(EndpointError);
    expect(() => new AuthApi({ baseUrl: 'https://server.example:8443' })).not.toThrow();
    expect(() => new AuthApi({ baseUrl: 'http://[::1]:8080' })).not.toThrow();
  });

  it('reports an unreachable server as status 0', async () => {
    const client = new AuthApi({ baseUrl: 'http://127.0.0.1:1' });
    await expect(client.token('xc_1', 's')).rejects.toMatchObject({ status: 0 });
  });
});
