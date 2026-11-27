// Access tokens for one IDE window (ADR-0014). A refresh token is used at most once: a
// refresh whose answer was lost is never retried with the same token, since presenting a
// spent one revokes the whole family. Any failed refresh logs in again with the client
// credentials, which starts a new family.

import type { TokenApi, Tokens } from './api';

export interface ClientCredentials {
  readonly clientId: string;
  readonly clientSecret: string;
}

/** Where the credentials live (`SecretStorage` in the extension, slice 11c). */
export type CredentialSource = () => Promise<ClientCredentials>;

/** The user has not signed in: no client credentials are stored. */
export class CredentialsMissingError extends Error {
  override name = 'CredentialsMissingError';
}

export class TokenSource {
  readonly #api: TokenApi;
  readonly #credentials: CredentialSource;
  #tokens: Tokens | null = null;
  /** One login at a time: two would start two token families. */
  #login: Promise<Tokens> | null = null;

  constructor(api: TokenApi, credentials: CredentialSource) {
    this.#api = api;
    this.#credentials = credentials;
  }

  /** The current access token, logging in first when there is none. */
  async access(): Promise<string> {
    this.#tokens ??= await this.#logIn();
    return this.#tokens.accessToken;
  }

  /** A new access token: by refresh, or by a new login when the refresh fails. */
  async renew(): Promise<string> {
    const spent = this.#tokens?.refreshToken;
    this.#tokens = null; // the refresh token is spent from here on, whatever happens
    if (spent !== undefined) {
      try {
        this.#tokens = await this.#api.refresh(spent);
      } catch {
        this.#tokens = null;
      }
    }
    this.#tokens ??= await this.#logIn();
    return this.#tokens.accessToken;
  }

  #logIn(): Promise<Tokens> {
    this.#login ??= this.#credentials()
      .then(({ clientId, clientSecret }) => this.#api.token(clientId, clientSecret))
      .finally(() => {
        this.#login = null;
      });
    return this.#login;
  }
}
