# Changelog

## 1.0.0 (2026-11-28)


### Features

* **gateway:** add client credentials, token families and rate limits ([#12](https://github.com/yordan-gergov01/xype-smart-ide/issues/12)) ([abfa41a](https://github.com/yordan-gergov01/xype-smart-ide/commit/abfa41aadf11b1212a445c2522b25e47de9182d8))
* **gateway:** add session keys with a separate key store ([#13](https://github.com/yordan-gergov01/xype-smart-ide/issues/13)) ([9072ec1](https://github.com/yordan-gergov01/xype-smart-ide/commit/9072ec13e104081f9c3ec97826f0a08a6bcc69fa))
* **gateway:** add the gRPC session stream with auth and heartbeats ([#14](https://github.com/yordan-gergov01/xype-smart-ide/issues/14)) ([5ccade4](https://github.com/yordan-gergov01/xype-smart-ide/commit/5ccade4c5cda8883b7c9f6c139b83f062e586fd1))
* **gateway:** close live session streams on revocation and shutdown ([#15](https://github.com/yordan-gergov01/xype-smart-ide/issues/15)) ([4b3f698](https://github.com/yordan-gergov01/xype-smart-ide/commit/4b3f698f43d00ac7e63599b612ff59287e9656e4))
* **gateway:** phase 1 slice 4, backend with metadata-only observability ([#2](https://github.com/yordan-gergov01/xype-smart-ide/issues/2)) ([d24698d](https://github.com/yordan-gergov01/xype-smart-ide/commit/d24698ded9ece9637d062d73c30249408464fff0))
* **indexing:** accept chunk uploads for pending leaves ([#26](https://github.com/yordan-gergov01/xype-smart-ide/issues/26)) ([f5ffbee](https://github.com/yordan-gergov01/xype-smart-ide/commit/f5ffbee91f4f350babca78abadeee988bacdb993))
* **indexing:** add the index message bus and queue topology ([#21](https://github.com/yordan-gergov01/xype-smart-ide/issues/21)) ([fc45b9a](https://github.com/yordan-gergov01/xype-smart-ide/commit/fc45b9a65fcfe906d9d97580da8698be655e7530))
* **indexing:** add the indexer worker ([#22](https://github.com/yordan-gergov01/xype-smart-ide/issues/22)) ([48516ec](https://github.com/yordan-gergov01/xype-smart-ide/commit/48516ec2a5f30acc7da9716a08717e9d97d9f473))
* **indexing:** add the Merkle index with compare-and-set apply ([#19](https://github.com/yordan-gergov01/xype-smart-ide/issues/19)) ([81405fd](https://github.com/yordan-gergov01/xype-smart-ide/commit/81405fdb267a29128b9ef860fa1e1671cdeea33e))
* **indexing:** apply synced trees before uploads and check the model ([#25](https://github.com/yordan-gergov01/xype-smart-ide/issues/25)) ([e5f5f0f](https://github.com/yordan-gergov01/xype-smart-ide/commit/e5f5f0ff57ddce7627f19cc225decc54d5412a96))
* **indexing:** kill stuck server scans and check rules for ReDoS ([#23](https://github.com/yordan-gergov01/xype-smart-ide/issues/23)) ([e0a3626](https://github.com/yordan-gergov01/xype-smart-ide/commit/e0a3626ebcbdc8dea0cbe904ca8eb3ad86568346))
* **indexing:** persist pending leaves and settle indexer results ([#24](https://github.com/yordan-gergov01/xype-smart-ide/issues/24)) ([4075b1f](https://github.com/yordan-gergov01/xype-smart-ide/commit/4075b1fbef773c569c2bcc35e981f8db44177113))
* **indexing:** re-request stale pending leaves ([#27](https://github.com/yordan-gergov01/xype-smart-ide/issues/27)) ([ebd21c8](https://github.com/yordan-gergov01/xype-smart-ide/commit/ebd21c8bf3f630bd5e3985a74139786a846adc68))
* **indexing:** run the Merkle sync over the session stream ([#20](https://github.com/yordan-gergov01/xype-smart-ide/issues/20)) ([d14025a](https://github.com/yordan-gergov01/xype-smart-ide/commit/d14025a5f2e8182533195f3825095727dc7748bc))
* **llm:** phase 1 slice 6, llm and embedder seams with mock and ollama ([#4](https://github.com/yordan-gergov01/xype-smart-ide/issues/4)) ([4e01d13](https://github.com/yordan-gergov01/xype-smart-ide/commit/4e01d13afdddbdfa35e1caaac5bb94adbcf8c164))
* **retrieval:** add the pgvector VectorStore keyed by Merkle leaf ([#18](https://github.com/yordan-gergov01/xype-smart-ide/issues/18)) ([e609d7d](https://github.com/yordan-gergov01/xype-smart-ide/commit/e609d7d5fc08142498c9b668f8ec08aa8dc02dbc))
* **security:** add payload crypto, key store and secret scrubbing ([#10](https://github.com/yordan-gergov01/xype-smart-ide/issues/10)) ([e7aa9f9](https://github.com/yordan-gergov01/xype-smart-ide/commit/e7aa9f9671743f630558eb83992371ffef56cc2c))
* **security:** add repository keys and deliver them with session keys ([#17](https://github.com/yordan-gergov01/xype-smart-ide/issues/17)) ([2dd61db](https://github.com/yordan-gergov01/xype-smart-ide/commit/2dd61db88ac3473c6fb099eda700cf633699ca05))
* **security:** add TLS with a local dev CA on every payload hop ([#11](https://github.com/yordan-gergov01/xype-smart-ide/issues/11)) ([2794ca6](https://github.com/yordan-gergov01/xype-smart-ide/commit/2794ca6f5b0adfc48760fe8b5398d50798359e81))
* **storage:** phase 1 slice 5, models, migrations, roles and proto ([#3](https://github.com/yordan-gergov01/xype-smart-ide/issues/3)) ([2e7f329](https://github.com/yordan-gergov01/xype-smart-ide/commit/2e7f329d54ef224d4a6250a7a444d2282a7f90f1))
* **xype-ai:** add the client privacy core ([#29](https://github.com/yordan-gergov01/xype-smart-ide/issues/29)) ([58159a2](https://github.com/yordan-gergov01/xype-smart-ide/commit/58159a2631052938bfabbcfc685321758a1b669a))
* **xype-ai:** add the client sync core ([#31](https://github.com/yordan-gergov01/xype-smart-ide/issues/31)) ([b9bdd3b](https://github.com/yordan-gergov01/xype-smart-ide/commit/b9bdd3b3e8c63868df4e04ead3fdb1bdb4cf6501))
* **xype-ai:** add the session stream client ([#32](https://github.com/yordan-gergov01/xype-smart-ide/issues/32)) ([a1bc4ec](https://github.com/yordan-gergov01/xype-smart-ide/commit/a1bc4ec8f1f0a92bb8d98d608d4c39b39a2fa601))
* **xype-ai:** chunk files by syntax tree and build the call graph ([#30](https://github.com/yordan-gergov01/xype-smart-ide/issues/30)) ([0a813f9](https://github.com/yordan-gergov01/xype-smart-ide/commit/0a813f93779fdcef1df8ff7656b57500ea147a45))
* **xype-ai:** run the sync engine in the extension ([#33](https://github.com/yordan-gergov01/xype-smart-ide/issues/33)) ([ce40b7d](https://github.com/yordan-gergov01/xype-smart-ide/commit/ce40b7dbf8655468a6d4911f761037313c0fca7f))
* **xype-ai:** scaffold the extension package and build its grammars ([#9](https://github.com/yordan-gergov01/xype-smart-ide/issues/9)) ([aa1d9df](https://github.com/yordan-gergov01/xype-smart-ide/commit/aa1d9dfacf2b5f2b3e5af1b4fa3fa5c20687e288))
