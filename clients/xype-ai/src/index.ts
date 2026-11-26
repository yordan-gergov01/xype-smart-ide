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
export { chunkFile, languageOf, loadParsers } from './context/chunker';
export type { Parsers } from './context/chunker';
export type { Chunk } from './context/chunkPlan';
export { buildTree, innerHash, MerkleError } from './context/merkle';
export type { ChunkInput, FileInput, MerkleNode, MerkleTree } from './context/merkle';
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
export type { ScrubResult, ScrubRules } from './security/scrubber';
