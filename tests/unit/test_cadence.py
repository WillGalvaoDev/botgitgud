from __future__ import annotations

from pathlib import Path

from botgitgud.analysis.cadence import classify_cd_type, compute_cadence, is_eligible
from botgitgud.domain.blacklist import MAJOR_CD_BLACKLIST

# -- documented acceptance criteria ------------------------------------------


def test_single_usage_never_becomes_an_instant_cooldown() -> None:
    cadence = compute_cadence([200.0], n_usages_median=1.0)
    assert cadence.observed_interval_median is None
    assert cadence.observed_interval_median != 200.0


def test_three_usages_median_interval() -> None:
    cadence = compute_cadence([10.0, 130.0, 250.0], n_usages_median=1.0)
    assert cadence.observed_interval_median == 120.0


def test_single_usage_unknown_base_cooldown_classified_major_and_eligible() -> None:
    """legacy/bot.py descartava justamente essas — cooldowns longos usados uma vez."""
    cadence = compute_cadence([200.0], n_usages_median=1.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MAJOR"
    assert is_eligible(999, presence=0.85, cadence=cadence, blacklist=MAJOR_CD_BLACKLIST) is True


def test_ring_of_peace_is_not_filtered_by_name() -> None:
    """achado 3.12: 'ring' era um substring banido; agora a blacklist é só por ID."""
    ring_of_peace_spell_id = (
        207018  # a ID em si não importa — o ponto é que nome nunca entra na decisão
    )
    cadence = compute_cadence([10.0, 40.0, 70.0], n_usages_median=3.0)
    assert ring_of_peace_spell_id not in MAJOR_CD_BLACKLIST
    assert (
        is_eligible(
            ring_of_peace_spell_id, presence=0.9, cadence=cadence, blacklist=MAJOR_CD_BLACKLIST
        )
        is True
    )


def test_no_lexical_filters_anywhere_in_src() -> None:
    """achado 3.12: elegibilidade de spell nunca pode depender do NOME.

    `legacy/bot.py:523` decidia com
    `any(x in name_lower for x in ["potion", "healthstone", ..., "trinket"])`,
    e por isso este guard bane essas palavras em `src/`.

    EB.0 introduziu a única exceção auditada: `domain/models.py` usa
    "trinket" como nome de SLOT de equipamento (`TRINKET_SLOTS = (12, 13)`),
    selecionado por índice numérico do `combatantInfo.gear[]` — nunca
    comparando o nome de coisa alguma. O intento do guard (nada de decisão
    lexical sobre spells) continua valendo em todo o resto de `src/`,
    incluindo `analysis/` inteiro, que é onde a elegibilidade é decidida.
    """
    src_root = Path(__file__).resolve().parents[2] / "src"
    banned_words = ("potion", "healthstone", "trinket")
    reviewed_exceptions = {src_root / "botgitgud" / "domain" / "models.py"}

    offenders = []
    for path in src_root.rglob("*.py"):
        if path in reviewed_exceptions:
            continue
        text = path.read_text(encoding="utf-8").lower()
        if any(word in text for word in banned_words):
            offenders.append(path)
    assert offenders == []


def test_the_reviewed_trinket_exception_selects_by_slot_never_by_name() -> None:
    """Guarda a própria exceção acima: se `domain/models.py` algum dia
    passar a comparar nomes, este teste quebra em vez de deixar a exceção
    virar um buraco silencioso no achado 3.12.
    """
    models = Path(__file__).resolve().parents[2] / "src" / "botgitgud" / "domain" / "models.py"
    text = models.read_text(encoding="utf-8")

    assert "TRINKET_SLOTS: tuple[int, int] = (12, 13)" in text
    assert "g.slot in TRINKET_SLOTS" in text
    # nenhuma comparação de nome, em nenhuma forma
    assert ".lower()" not in text
    assert "name_lower" not in text


# -- classification branches --------------------------------------------------


def test_classify_uses_base_cooldown_first_when_known() -> None:
    cadence = compute_cadence([0.0, 10.0], n_usages_median=2.0, base_cooldown=120.0)
    assert classify_cd_type(cadence) == "MAJOR"  # base_cooldown wins even though interval is 10s


def test_classify_minor_base_cooldown() -> None:
    cadence = compute_cadence([], n_usages_median=1.0, base_cooldown=30.0)
    assert classify_cd_type(cadence) == "MINOR"


def test_classify_falls_back_to_observed_interval_when_base_unknown() -> None:
    cadence = compute_cadence([0.0, 100.0], n_usages_median=2.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MAJOR"


def test_classify_multi_use_short_interval_is_minor() -> None:
    cadence = compute_cadence([0.0, 20.0, 40.0], n_usages_median=3.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MINOR"


def test_classify_single_usage_high_n_median_is_minor() -> None:
    # n_usages_median > 1.5 with no other evidence: not treated as a rare major CD.
    cadence = compute_cadence([200.0], n_usages_median=4.0, base_cooldown=None)
    assert classify_cd_type(cadence) == "MINOR"


# -- eligibility branches ------------------------------------------------------


def test_low_presence_is_ineligible() -> None:
    cadence = compute_cadence([0.0, 100.0], n_usages_median=2.0)
    assert is_eligible(1, presence=0.5, cadence=cadence, blacklist=frozenset()) is False


def test_short_observed_interval_is_ineligible() -> None:
    cadence = compute_cadence([0.0, 10.0], n_usages_median=2.0)  # interval = 10s < 15s floor
    assert is_eligible(1, presence=0.9, cadence=cadence, blacklist=frozenset()) is False


def test_short_base_cooldown_is_ineligible() -> None:
    cadence = compute_cadence([], n_usages_median=1.0, base_cooldown=10.0)
    assert is_eligible(1, presence=0.9, cadence=cadence, blacklist=frozenset()) is False


def test_blacklisted_id_is_ineligible_regardless_of_stats() -> None:
    cadence = compute_cadence([0.0, 100.0], n_usages_median=2.0)
    assert is_eligible(22812, presence=0.99, cadence=cadence, blacklist=MAJOR_CD_BLACKLIST) is False


def test_unknown_interval_never_excludes_on_its_own() -> None:
    cadence = compute_cadence([200.0], n_usages_median=1.0)  # single usage: interval unknown
    assert is_eligible(1, presence=0.9, cadence=cadence, blacklist=frozenset()) is True


def test_iqr_of_single_interval_is_zero() -> None:
    cadence = compute_cadence([10.0, 40.0], n_usages_median=2.0)
    assert cadence.observed_interval_iqr == 0.0


def test_iqr_with_multiple_intervals() -> None:
    cadence = compute_cadence([0.0, 10.0, 20.0, 40.0], n_usages_median=4.0)
    assert cadence.observed_interval_iqr is not None
    assert cadence.observed_interval_iqr >= 0.0
