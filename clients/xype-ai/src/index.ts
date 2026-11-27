// The package entry point: only what other packages and the extension may use.

export {
  createParser,
  GrammarError,
  grammarFile,
  LANGUAGE_IDS,
  loadGrammar,
} from './context/grammars';
export type { LanguageId } from './context/grammars';
export { buildCallGraph } from './context/callGraph';
export type { CallGraph, ChunkKey, FileChunks } from './context/callGraph';
export { chunkFile, languageOf, loadParsers, PARSE_BUDGET_MS } from './context/chunker';
export type { LoadedParsers, Parsers } from './context/chunker';
export type { Chunk } from './context/chunkPlan';
export { buildTree, innerHash, MerkleError } from './context/merkle';
export type { ChunkInput, FileInput, MerkleLeaf, MerkleNode, MerkleTree } from './context/merkle';
export { DecryptionError } from './security/aead';
export { ClientCryptoBox, ContextError, NonceError } from './security/cryptoBox';
export type { EncryptedPayload, PayloadContext, SealContext } from './security/cryptoBox';
export {
  clientKeyPair,
  KeyExchangeError,
  unwrapRepositoryKey,
  unwrapSessionKey,
  wrappingKey,
} from './security/keys';
export type { ClientKeyPair } from './security/keys';
export { hmacId, normalisePath, PathError } from './security/obfuscation';
export { fileText, isDeniedPath, parsePathRules, PathRulesError } from './security/paths';
export type { Exclusion, FileText, PathRules } from './security/paths';
export { parseRules, RulesError, ScrubTimeoutError, SecretScrubber } from './security/scrubber';
export type { ScrubbedText, ScrubResult, ScrubRules } from './security/scrubber';
export { ScrubWorker, ScrubWorkerError } from './security/scrubWorker';
export type { WorkerScrub } from './security/scrubWorker';
export type * from './sync/messages';
export {
  CHUNKS_FIELD,
  MAX_CHUNKS_PER_UPLOAD,
  MAX_NODES_PER_REPORT,
  MAX_UPLOAD_BYTES,
  MerkleResponder,
} from './sync/responder';
export { walkRepository } from './sync/walker';
export type { WalkedFile } from './sync/walker';
export { ApiError, AuthApi } from './session/api';
export type { KeyApi, Principal, ServerEndpoint, TokenApi, Tokens } from './session/api';
export { systemClock } from './session/clock';
export type { Clock } from './session/clock';
export { mintSessionKey } from './session/mint';
export type { SessionKeys } from './session/mint';
export { KEY_UNUSABLE, SessionClient } from './session/session';
export type { SessionOptions, StopReason } from './session/session';
export { TokenSource } from './session/tokens';
export type { ClientCredentials, CredentialSource } from './session/tokens';
export { grpcTransport, sessionTransport } from './session/transport';
export type { SessionStream, SessionTransport } from './session/transport';
