// The real gRPC transport over HTTP/2 on loopback (cleartext here; TLS with the dev CA in
// the extension): the bearer token travels as metadata and the stream runs both ways.

import { createServer, type Http2Server } from 'node:http2';
import type { AddressInfo } from 'node:net';

import { create } from '@bufbuild/protobuf';
import { connectNodeAdapter } from '@connectrpc/connect-node';
import { afterEach, describe, expect, it } from 'vitest';

import { HelloAckSchema, SessionResponseSchema } from '@/gen/xype/v1/session_pb';

import { scriptedService } from './testing/fakes';
import { grpcTransport, sessionTransport } from './transport';
import { heartbeatRequest } from './wire';

let server: Http2Server | null = null;
afterEach(async () => {
  const open = server;
  server = null;
  if (open !== null) await new Promise((resolve) => open.close(resolve));
});

describe('grpcTransport', () => {
  it('opens a bidirectional Session call with the bearer token', async () => {
    const ack = create(SessionResponseSchema, {
      body: { case: 'helloAck', value: create(HelloAckSchema, { heartbeatIntervalS: 20 }) },
    });
    const service = scriptedService(() => [ack]);
    server = createServer(connectNodeAdapter({ routes: service.routes }));
    await new Promise<void>((resolve) => server?.listen(0, '127.0.0.1', resolve));
    const { port } = server.address() as AddressInfo;
    const transport = sessionTransport(
      grpcTransport({ baseUrl: `http://127.0.0.1:${String(port)}` }),
    );
    const stream = transport.open('tok');
    stream.send(heartbeatRequest(1n));
    for await (const response of stream.responses) {
      expect(response.body.case).toBe('helloAck');
      break;
    }
    stream.close();
    expect(service.tokens).toEqual(['Bearer tok']);
    expect(service.calls[0]?.[0]?.body.case).toBe('heartbeat');
  });
});
