# `legacy/` — implementação original, congelada como oracle de regressão

`bot.py` é o script monolítico original do projeto, de antes da reescrita em
`src/botgitgud/`. Ele **não roda em produção** e não é importado por nenhum
código de `src/`.

Ele continua aqui por um motivo específico: é uma **implementação executável de
referência**, usada pela suíte golden como oracle. Não é código morto, e não é
mantido — é congelado.

## Nunca edite este arquivo

Editá-lo invalida os testes que dependem dele como referência histórica. Se
algum comportamento aqui parece errado, provavelmente *está* — vários bugs do
original estão documentados em `docs/relario.md` e deliberadamente preservados
(o novo pipeline os corrige, e o golden prova a diferença).

## Quem depende dele

| Consumidor | Uso |
|---|---|
| `tests/fixtures/legacy_runner.py` | carrega `bot.py` como módulo isolado, com CWD próprio |
| `tests/golden/test_legacy_output.py` | executa o pipeline legado e compara ao snapshot congelado |
| `tests/golden/test_legacy_determinism.py` | prova que o relatório legado é byte-idêntico sob 24 permutações da ordem de chegada das referências e sob o escalonamento real de um `ThreadPoolExecutor` (ver `docs/v1-readiness-determinism.md`) |
| `tests/golden/test_new_pipeline_output.py` | compara o pipeline ATUAL contra o snapshot legado — prova que a diferença é intencional e revisada |
| `tests/fixtures/record.py` | regrava as cassettes HTTP executando o pipeline legado contra a API real |

## Por que não foi removido

A remoção foi avaliada explicitamente (GH.0B). Ela exigiria apagar 8 dos 13
testes golden, incluindo a prova de determinismo — e determinismo sob permutação
de entrada é uma propriedade que **só existe executando código**: não há artefato
estático capaz de congelá-la. Trocar isso por uma comparação de snapshot consigo
mesmo seria uma asserção tautológica, não cobertura.

O snapshot `tests/golden/__snapshots__/test_legacy_output.ambr` só tem
autoridade como "o que o legado realmente produz" porque um teste executa o
legado e confirma isso a cada execução da suíte. Sem o executável, ele vira um
blob que ninguém consegue verificar nem regenerar.
