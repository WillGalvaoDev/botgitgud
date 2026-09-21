"""QA.0 — conteúdo, não `mtime`: prova de que um diretório real não foi
mutado por uma execução da suíte.

`mtime` é frágil nos dois sentidos: reescrever o mesmo byte de volta ainda
muda o mtime (falso positivo), e um clock de resolução grossa pode nem
mudar o mtime numa escrita real (falso negativo). `(tamanho, sha256)` por
arquivo detecta qualquer mutação de conteúdo e é imune a ambos.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DirSnapshot:
    exists: bool
    # caminho relativo (posix) -> (tamanho em bytes, sha256 hex)
    files: dict[str, tuple[int, str]]


def snapshot_directory(path: Path) -> DirSnapshot:
    if not path.exists():
        return DirSnapshot(exists=False, files={})
    files = {
        f.relative_to(path).as_posix(): (
            f.stat().st_size,
            hashlib.sha256(f.read_bytes()).hexdigest(),
        )
        for f in sorted(path.rglob("*"))
        if f.is_file()
    }
    return DirSnapshot(exists=True, files=files)


def diff_snapshots(before: DirSnapshot, after: DirSnapshot) -> list[str]:
    """Lista de violações, vazia se `after` preserva `before` inteiramente.
    Nunca levanta — quem chama decide o que fazer com a lista (assert,
    log, etc.), o que também é o que torna esta função testável por si só.
    """
    if not before.exists:
        if after.exists:
            return ["diretório não existia antes e foi criado"]
        return []
    if not after.exists:
        return ["diretório existia antes e foi apagado inteiramente"]

    missing = before.files.keys() - after.files.keys()
    added = after.files.keys() - before.files.keys()
    changed = {
        name
        for name in before.files.keys() & after.files.keys()
        if before.files[name] != after.files[name]
    }
    violations = []
    if missing:
        violations.append(f"arquivos removidos: {sorted(missing)}")
    if added:
        violations.append(f"arquivos criados: {sorted(added)}")
    if changed:
        violations.append(f"conteúdo alterado: {sorted(changed)}")
    return violations
