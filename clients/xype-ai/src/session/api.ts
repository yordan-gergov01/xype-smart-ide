// The REST calls the session needs (ADR-0014, ADR-0015): log in, refresh, who am I and
// a session key. JSON over `node:https` with the server's CA, so a dev CA works without
// turning verification off. Errors carry the status or a code, never a body, a token or
// a secret; responses are checked field by field.

import { type IncomingMessage, request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';

export interface ServerEndpoint {
  /** `https://host:port`; `http:` only for loopback tests. */
  readonly baseUrl: string;
  /** PEM of the CA that issued the server's certificate; the system CAs when absent. */
  readonly ca?: string;
}

export interface Tokens {
  readonly accessToken: string;
  readonly refreshToken: string;
  readonly expiresInS: number;
}

export interface Principal {
  readonly userId: string;
  readonly teamId: string;
}

export interface WrappedRepositoryKey {
  readonly repositoryId: string;
  readonly hmacKeyId: string;
  readonly wrappedKey: Uint8Array;
}

export interface MintedKey {
  readonly keyId: string;
  readonly serverPublicKey: Uint8Array;
  readonly wrappedKey: Uint8Array;
  readonly repositoryKeys: readonly WrappedRepositoryKey[];
}

/** `status` is the HTTP status, or 0 when no response arrived (`sent` says if the body left). */
export class ApiError extends Error {
  override name = 'ApiError';
  readonly status: number;
  readonly sent: boolean;

  constructor(status: number, sent: boolean) {
    super(
      status === 0 ? 'the server could not be reached' : `the server answered ${String(status)}`,
    );
    this.status = status;
    this.sent = sent;
  }
}

export class EndpointError extends Error {
  override name = 'EndpointError';
}

// Addresses, not `localhost`, which the hosts file could point elsewhere.
const LOOPBACK = new Set(['127.0.0.1', '[::1]']);

/**
 * The server's base URL: `https:`, or `http:` only to a loopback address (tests). Credentials,
 * tokens and the key exchange never travel in clear (ADR-0036).
 */
export function checkedBaseUrl(baseUrl: string): URL {
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new EndpointError('the server URL is malformed');
  }
  const plainLoopback = url.protocol === 'http:' && LOOPBACK.has(url.hostname);
  if (url.protocol !== 'https:' && !plainLoopback) {
    throw new EndpointError('the server URL must use https');
  }
  return url;
}

/** Larger answers are refused: every response here is a few hundred bytes. */
const MAX_RESPONSE_BYTES = 64 * 1024;
const TIMEOUT_MS = 15_000;

type Json = Record<string, unknown>;

function text(data: Json, key: string): string {
  const value = data[key];
  if (typeof value !== 'string' || value === '') throw new ApiError(502, true);
  return value;
}

function bytes(data: Json, key: string): Uint8Array {
  return new Uint8Array(Buffer.from(text(data, key), 'base64'));
}

/** The calls the session makes after login; `AuthApi` is the real one. */
export interface KeyApi {
  me(accessToken: string): Promise<Principal>;
  sessionKey(
    accessToken: string,
    publicKey: Uint8Array,
    repositoryIds: readonly string[],
  ): Promise<MintedKey>;
}

/** Login and refresh; `AuthApi` is the real one. */
export interface TokenApi {
  token(clientId: string, clientSecret: string): Promise<Tokens>;
  refresh(refreshToken: string): Promise<Tokens>;
}

export class AuthApi implements KeyApi, TokenApi {
  readonly #endpoint: ServerEndpoint;

  constructor(endpoint: ServerEndpoint) {
    checkedBaseUrl(endpoint.baseUrl);
    this.#endpoint = endpoint;
  }

  async token(clientId: string, clientSecret: string): Promise<Tokens> {
    const body = { client_id: clientId, client_secret: clientSecret };
    return tokens(await this.#call('POST', '/api/v1/auth/token', null, body));
  }

  async refresh(refreshToken: string): Promise<Tokens> {
    return tokens(
      await this.#call('POST', '/api/v1/auth/refresh', null, { refresh_token: refreshToken }),
    );
  }

  async me(accessToken: string): Promise<Principal> {
    const data = await this.#call('GET', '/api/v1/auth/me', accessToken, null);
    return { userId: text(data, 'user_id'), teamId: text(data, 'team_id') };
  }

  async sessionKey(
    accessToken: string,
    publicKey: Uint8Array,
    repositoryIds: readonly string[],
  ): Promise<MintedKey> {
    const body = {
      client_public_key: Buffer.from(publicKey).toString('base64'),
      repository_ids: repositoryIds,
    };
    const data = await this.#call('POST', '/api/v1/auth/session-key', accessToken, body);
    const keys = data['repository_keys'];
    if (!Array.isArray(keys)) throw new ApiError(502, true);
    return {
      keyId: text(data, 'key_id'),
      serverPublicKey: bytes(data, 'server_public_key'),
      wrappedKey: bytes(data, 'wrapped_key'),
      repositoryKeys: keys.map((entry: unknown) => {
        if (typeof entry !== 'object' || entry === null) throw new ApiError(502, true);
        const key = entry as Json;
        return {
          repositoryId: text(key, 'repository_id'),
          hmacKeyId: text(key, 'hmac_key_id'),
          wrappedKey: bytes(key, 'wrapped_key'),
        };
      }),
    };
  }

  #call(method: string, path: string, bearer: string | null, body: Json | null): Promise<Json> {
    const url = new URL(path, this.#endpoint.baseUrl);
    const payload = body === null ? null : Buffer.from(JSON.stringify(body));
    const headers: Record<string, string> = { accept: 'application/json' };
    if (bearer !== null) headers['authorization'] = `Bearer ${bearer}`;
    if (payload !== null) headers['content-type'] = 'application/json';
    const send = url.protocol === 'https:' ? httpsRequest : httpRequest;
    const ca = this.#endpoint.ca;
    const options = { method, headers, timeout: TIMEOUT_MS, ...(ca === undefined ? {} : { ca }) };
    return new Promise((resolve, reject) => {
      let sent = false;
      const call = send(url, options, (response) => {
        readBody(response, () => call.destroy()).then(
          (raw) => {
            try {
              resolve(parse(response.statusCode ?? 0, raw));
            } catch (error) {
              reject(error instanceof ApiError ? error : new ApiError(502, true));
            }
          },
          () => {
            reject(new ApiError(0, true));
          },
        );
      });
      // `timeout` is the socket's idle time; the deadline bounds a slow drip too.
      const deadline = setTimeout(() => call.destroy(), TIMEOUT_MS);
      call.on('close', () => {
        clearTimeout(deadline);
      });
      call.on('timeout', () => call.destroy());
      call.on('error', () => {
        reject(new ApiError(0, sent));
      });
      call.end(payload ?? undefined, () => (sent = true));
    });
  }
}

/** The response body, or a rejection when it is larger than any answer here. */
function readBody(response: IncomingMessage, abort: () => void): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    response.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size <= MAX_RESPONSE_BYTES) chunks.push(chunk);
      else abort();
    });
    response.on('end', () => {
      resolve(Buffer.concat(chunks));
    });
    response.on('error', reject);
    response.on('aborted', reject);
  });
}

function parse(status: number, raw: Buffer): Json {
  if (status !== 200) throw new ApiError(status, true);
  let data: unknown;
  try {
    data = JSON.parse(raw.toString('utf8'));
  } catch {
    throw new ApiError(502, true);
  }
  if (typeof data !== 'object' || data === null) throw new ApiError(502, true);
  return data as Json;
}

function tokens(data: Json): Tokens {
  const expires = data['expires_in'];
  if (typeof expires !== 'number' || !Number.isInteger(expires) || expires <= 0) {
    throw new ApiError(502, true);
  }
  return {
    accessToken: text(data, 'access_token'),
    refreshToken: text(data, 'refresh_token'),
    expiresInS: expires,
  };
}
