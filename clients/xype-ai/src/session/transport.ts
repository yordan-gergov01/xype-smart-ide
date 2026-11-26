// The session stream seam (ADR-0013, ADR-0038): open one `Session` call with the bearer
// token in its metadata, send requests, read responses. The adapter runs on any Connect
// `Transport`: gRPC over HTTP/2 with the server's CA in the extension, an in-memory
// router in tests. HTTP/2 pings stay off (the server refuses frequent ones); the
// session's own heartbeat keeps the stream alive.

import { createClient, type Transport } from '@connectrpc/connect';
import { createGrpcTransport } from '@connectrpc/connect-node';

import {
  type SessionRequest,
  type SessionResponse,
  SessionService,
} from '@/gen/xype/v1/session_pb';
import { uuid7 } from '@/sync/ids';

import { checkedBaseUrl, type ServerEndpoint } from './api';

export interface SessionStream {
  send(request: SessionRequest): void;
  readonly responses: AsyncIterable<SessionResponse>;
  /** Ends the call; pending reads end too. */
  close(): void;
}

export interface SessionTransport {
  open(accessToken: string): SessionStream;
}

/** Requests as an async iterable the call reads from; `end` finishes it. */
class RequestQueue implements AsyncIterable<SessionRequest> {
  readonly #items: SessionRequest[] = [];
  #wake: (() => void) | null = null;
  #ended = false;

  push(request: SessionRequest): void {
    if (this.#ended) return;
    this.#items.push(request);
    this.#wake?.();
  }

  end(): void {
    this.#ended = true;
    this.#wake?.();
  }

  async *[Symbol.asyncIterator](): AsyncIterator<SessionRequest> {
    for (;;) {
      const next = this.#items.shift();
      if (next !== undefined) {
        yield next;
        continue;
      }
      if (this.#ended) return;
      await new Promise<void>((resolve) => (this.#wake = resolve));
      this.#wake = null;
    }
  }
}

export function sessionTransport(transport: Transport): SessionTransport {
  const client = createClient(SessionService, transport);
  return {
    open(accessToken) {
      const requests = new RequestQueue();
      const abort = new AbortController();
      const responses = client.session(requests, {
        headers: { authorization: `Bearer ${accessToken}`, 'x-correlation-id': uuid7() },
        signal: abort.signal,
      });
      return {
        send: (request) => {
          requests.push(request);
        },
        responses,
        close: () => {
          requests.end();
          abort.abort();
        },
      };
    },
  };
}

/** gRPC over HTTP/2 to the edge proxy, verified against the server's CA. */
export function grpcTransport(endpoint: ServerEndpoint): Transport {
  checkedBaseUrl(endpoint.baseUrl);
  return createGrpcTransport({
    baseUrl: endpoint.baseUrl,
    // Explicit, so a library default cannot turn pings on (the server refuses frequent ones).
    pingIntervalMs: Number.POSITIVE_INFINITY,
    ...(endpoint.ca === undefined ? {} : { nodeOptions: { ca: endpoint.ca } }),
  });
}
