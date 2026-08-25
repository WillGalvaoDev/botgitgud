# Separação entre seed versionado e cache de runtime do catálogo de spells

Correção de reprodutibilidade — D-35. Relacionada a T0.4 (remoção de `category`) e a D-4
(isolamento do `spells.json` durante testes/gravação).

## INCIDENT

Depois das execuções reais do bot durante o smoke de R1-01, a working tree ficou suja:

```
M spells.json
```

O arquivo **rastreado** havia passado de **100 para 362 entradas** (262 nomes aprendidos em
runtime) e perdido o campo `category` de todas elas. Como `legacy/bot.py:532` ainda exige
`category`, dois golden tests legados passaram a falhar com `KeyError: 'category'`.

Atribuição provada por isolamento:

| Estado do `spells.json` | Resultado da suíte |
|---|---|
| Mutado pelas execuções reais | 980 passed / **2 failed** |
| Revertido para o Git (nada mais alterado) | **982 passed** / 0 failed |

## CAUSA

`cli.py:_build_deps` construía o catálogo com um caminho relativo apontando direto para o arquivo
versionado:

```python
catalog = SpellCatalog(Path("spells.json"), blizzard=blizzard)
```

`_build_deps` alimenta **todos** os caminhos de produção — `analyze`, `build-cohort`, `serve`,
`discover`, `triage`. Qualquer execução real chamava `learn()` + `flush()` e reescrevia o arquivo
do repositório. A T0.4 já havia decidido, corretamente, que o catálogo moderno não persiste
`category`; o efeito colateral era destruir o formato de que o legado depende.

A D-4 tinha resolvido exatamente este risco **para testes e gravação de fixtures**, via CWD
isolado. O caminho de produção nunca recebeu a mesma proteção.

## DECISÃO

As 262 entradas aprendidas **não foram preservadas** no repositório. O catálogo é cache
regenerável; reprodutibilidade, determinismo da suíte e uma working tree limpa valem mais do que
nomes que o runtime reaprende sozinho. O arquivo foi revertido para o estado do Git.

## Os dois arquivos

| | Seed versionado | Cache de runtime |
|---|---|---|
| **Caminho** | `spells.json` (raiz do repo) | `settings.data_dir / "spells.json"` (padrão `data/spells.json`) |
| **Git** | rastreado | ignorado (`data/` está no `.gitignore`) |
| **Formato** | antigo, **com** `category` | moderno, **sem** `category` |
| **Quem lê** | `legacy/bot.py`, golden tests legados, primeiro boot de produção | todo o runtime moderno |
| **Quem escreve** | **ninguém** — nunca | `learn()` / `flush()` |
| **Entradas** | 100, fixas | cresce com o uso |

## Política de migração

`open_runtime_catalog(data_dir, *, blizzard, seed_path)`:

- **Cache existe** → usa o cache. O seed nem é aberto; o cache tem precedência.
- **Cache não existe e o seed existe** → copia o seed uma única vez para o cache e segue a partir
  dele. Os nomes já conhecidos não se perdem no primeiro boot.
- **Cache não existe e o seed também não** (ex.: instalação empacotada, já que `spells.json` não
  entra no wheel) → inicia vazio. Não é erro.
- **Qualquer escrita posterior** → sempre e apenas no cache.

Cache corrompido continua seguindo a política de quarentena que já existia
(`spells.corrupt.<timestamp>.json` + catálogo vazio), agora dentro de `data/`.

## Por que produção não pode mutar o repositório

1. **Reprodutibilidade** — a suíte precisa de entrada determinística. Um arquivo rastreado que
   muda conforme o bot roda torna o resultado dos testes função de quantas análises foram feitas.
2. **Compatibilidade com o legado** — o golden legado é a rede de segurança que prova que o
   pipeline moderno não regrediu em relação ao comportamento original. Ele depende do formato
   antigo, com `category`.
3. **Higiene de release** — `git status` sujo depois de operar o bot esconde alterações reais e
   torna impossível distinguir trabalho de efeito colateral.

## O que continua valendo

A decisão da T0.4 **não** foi revertida: o catálogo moderno segue descartando `category` na leitura
e nunca o persiste. `category` é propriedade de `(spell, spec, encounter)`, não da spell isolada —
persisti-lo globalmente foi um dos achados originais da auditoria. O seed versionado mantém o campo
apenas porque o `legacy/bot.py` congelado o exige.
