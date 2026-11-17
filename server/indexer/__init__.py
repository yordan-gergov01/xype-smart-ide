"""The indexer worker (ADR-0015): decrypts, re-checks, embeds and wipes chunks from
`index.chunks` and publishes vectors or rejections to `index.vectors`. No database."""
