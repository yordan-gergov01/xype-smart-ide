# Changelog

## 1.0.0 (2026-11-14)


### Features

* **gateway:** add client credentials, token families and rate limits ([#12](https://github.com/yordan-gergov01/xype-smart-ide/issues/12)) ([abfa41a](https://github.com/yordan-gergov01/xype-smart-ide/commit/abfa41aadf11b1212a445c2522b25e47de9182d8))
* **gateway:** add session keys with a separate key store ([#13](https://github.com/yordan-gergov01/xype-smart-ide/issues/13)) ([9072ec1](https://github.com/yordan-gergov01/xype-smart-ide/commit/9072ec13e104081f9c3ec97826f0a08a6bcc69fa))
* **gateway:** add the gRPC session stream with auth and heartbeats ([#14](https://github.com/yordan-gergov01/xype-smart-ide/issues/14)) ([5ccade4](https://github.com/yordan-gergov01/xype-smart-ide/commit/5ccade4c5cda8883b7c9f6c139b83f062e586fd1))
* **gateway:** close live session streams on revocation and shutdown ([#15](https://github.com/yordan-gergov01/xype-smart-ide/issues/15)) ([4b3f698](https://github.com/yordan-gergov01/xype-smart-ide/commit/4b3f698f43d00ac7e63599b612ff59287e9656e4))
* **gateway:** phase 1 slice 4, backend with metadata-only observability ([#2](https://github.com/yordan-gergov01/xype-smart-ide/issues/2)) ([d24698d](https://github.com/yordan-gergov01/xype-smart-ide/commit/d24698ded9ece9637d062d73c30249408464fff0))
* **indexing:** add the index message bus and queue topology ([#21](https://github.com/yordan-gergov01/xype-smart-ide/issues/21)) ([fc45b9a](https://github.com/yordan-gergov01/xype-smart-ide/commit/fc45b9a65fcfe906d9d97580da8698be655e7530))
* **indexing:** add the Merkle index with compare-and-set apply ([#19](https://github.com/yordan-gergov01/xype-smart-ide/issues/19)) ([81405fd](https://github.com/yordan-gergov01/xype-smart-ide/commit/81405fdb267a29128b9ef860fa1e1671cdeea33e))
* **indexing:** run the Merkle sync over the session stream ([#20](https://github.com/yordan-gergov01/xype-smart-ide/issues/20)) ([d14025a](https://github.com/yordan-gergov01/xype-smart-ide/commit/d14025a5f2e8182533195f3825095727dc7748bc))
* **llm:** phase 1 slice 6, llm and embedder seams with mock and ollama ([#4](https://github.com/yordan-gergov01/xype-smart-ide/issues/4)) ([4e01d13](https://github.com/yordan-gergov01/xype-smart-ide/commit/4e01d13afdddbdfa35e1caaac5bb94adbcf8c164))
* **retrieval:** add the pgvector VectorStore keyed by Merkle leaf ([#18](https://github.com/yordan-gergov01/xype-smart-ide/issues/18)) ([e609d7d](https://github.com/yordan-gergov01/xype-smart-ide/commit/e609d7d5fc08142498c9b668f8ec08aa8dc02dbc))
* **security:** add payload crypto, key store and secret scrubbing ([#10](https://github.com/yordan-gergov01/xype-smart-ide/issues/10)) ([e7aa9f9](https://github.com/yordan-gergov01/xype-smart-ide/commit/e7aa9f9671743f630558eb83992371ffef56cc2c))
* **security:** add repository keys and deliver them with session keys ([#17](https://github.com/yordan-gergov01/xype-smart-ide/issues/17)) ([2dd61db](https://github.com/yordan-gergov01/xype-smart-ide/commit/2dd61db88ac3473c6fb099eda700cf633699ca05))
* **security:** add TLS with a local dev CA on every payload hop ([#11](https://github.com/yordan-gergov01/xype-smart-ide/issues/11)) ([2794ca6](https://github.com/yordan-gergov01/xype-smart-ide/commit/2794ca6f5b0adfc48760fe8b5398d50798359e81))
* **storage:** phase 1 slice 5, models, migrations, roles and proto ([#3](https://github.com/yordan-gergov01/xype-smart-ide/issues/3)) ([2e7f329](https://github.com/yordan-gergov01/xype-smart-ide/commit/2e7f329d54ef224d4a6250a7a444d2282a7f90f1))
* **xype-ai:** scaffold the extension package and build its grammars ([#9](https://github.com/yordan-gergov01/xype-smart-ide/issues/9)) ([aa1d9df](https://github.com/yordan-gergov01/xype-smart-ide/commit/aa1d9dfacf2b5f2b3e5af1b4fa3fa5c20687e288))
