# Valida??o final da revis?o independente

Parecer: REQUIRES_CHANGES.

- Su?te offline ampla, na c?pia isolada: **2528 passed, 1 failed, 1 deselected, 337 warnings; 2 snapshots passed; 656,32s; exit 1**. Comando: `python -m pytest -o addopts='' -m 'not network' -q --tb=short --junitxml=docs/independent-suite-final.xml`. Sa?da integral em `independent-suite-final.log`.
- A ?nica falha conclu?da foi `test_get_code_version_returns_short_git_hash_in_this_repo`: a c?pia n?o tinha `.git`, e o resultado era `unknown`. O teste exato foi reexecutado **sem altera??o de c?digo no workspace original**, onde h? Git: **1 passed, 11,83s**, com os guards de preserva??o de dados aprovados. JUnit `docs/independent-git-check.xml`. Isso comprova a causa ambiental dessa falha; n?o ? uma alega??o de 2529 aprova??es em uma ?nica execu??o na c?pia.
- Os 78 testes dos sete m?dulos M0/M1 passaram em execu??o pr?pria, 12,29s; JUnit `docs/independent-focus.xml`.
- Ruff passou; Pyright retornou zero erros e zero warnings de an?lise.
- Os 12 grupos de contraexemplos foram reproduzidos com asser??es, em `independent_probes.py`; resultados em `independent-probes.json`.
- O gerador atual reproduziu todo o conte?do das evid?ncias entregues, exceto o timestamp de gera??o. Resultado: `docs/independent-evidence.json.gz`.
- A confer?ncia final confirmou os 976 arquivos e o mesmo fingerprint de c?digo/testes/scripts, al?m do mesmo hash dos 1361 Parquets no workspace. Os valores exatos e atributos dos JUnits est?o em `independent-validation.json`.

A tentativa inicial da su?te ? preservada em `independent-suite.log`, sem JUnit final: a c?pia ainda carecia de arquivos auxiliares versionados, e o log registrou access violation nativa em PyArrow. N?o foi usada como prova de aprova??o, nem essa ocorr?ncia foi atribu?da ? implementa??o sem evid?ncia. A execu??o completa posterior descrita acima n?o registrou essa ocorr?ncia.

Nenhum c?digo do workspace foi alterado. N?o houve teste network, migra??o do Store de produ??o, regrava??o do corpus ou in?cio de M2.
