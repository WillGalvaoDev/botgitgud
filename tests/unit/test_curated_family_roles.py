from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.curated_family_roles import CURATED_FAMILY_ROLES


def test_curated_entity_decisions_are_exact_and_carry_provenance() -> None:
    # Exatidao deliberada: a tabela nao pode crescer sem que este numero mude junto.
    # 19 (lote 1 pre-M18) + 12 (lote 1 pos-M18, Warlock) + 12 (lote 2, Warrior)
    # + 20 (lote 3, Druid: 5 abilities x 2 specs + 10 so em Balance)
    # + 52 (lote 4, cross-spec: 19 nomes expandidos pelos specs observados).
    assert len(CURATED_FAMILY_ROLES) == 115
    assert all(
        value.provenance == "GATE-M5-batch1-decisions.json"
        for value in CURATED_FAMILY_ROLES.values()
    )
    assert all(value.justification for value in CURATED_FAMILY_ROLES.values())
    dreadstalkers = CURATED_FAMILY_ROLES[("Warlock", "Demonology", "Call Dreadstalkers")]
    assert dreadstalkers.role is AbilityRole.PET_SUMMON
    for spec in ("Affliction", "Demonology", "Destruction"):
        assert CURATED_FAMILY_ROLES[("Warlock", spec, "Dark Pact")].role is AbilityRole.DEFENSIVE
        assert CURATED_FAMILY_ROLES[("Warlock", spec, "Burning Rush")].role is AbilityRole.UTILITY
        circle = CURATED_FAMILY_ROLES[("Warlock", spec, "Demonic Circle: Teleport")]
        assert circle.role is AbilityRole.UTILITY
    felguard = CURATED_FAMILY_ROLES[("Warlock", "Demonology", "Summon Felguard")]
    assert felguard.role is AbilityRole.PET_SUMMON
    # Expandida SO nos specs observados: Affliction nunca castou Fel Domination.
    assert ("Warlock", "Demonology", "Fel Domination") in CURATED_FAMILY_ROLES
    assert ("Warlock", "Destruction", "Fel Domination") in CURATED_FAMILY_ROLES
    assert ("Warlock", "Affliction", "Fel Domination") not in CURATED_FAMILY_ROLES


def test_unknown_entity_fails_closed() -> None:
    assert ("Warlock", "Demonology", "Unknown") not in CURATED_FAMILY_ROLES


def test_curated_may_restore_a_role_that_cadence_alone_could_not_justify() -> None:
    """Avatar e Sweeping Strikes estavam entre os 233 removidos por M17.

    Voltam por decisao humana curada, que e o nivel de maior precedencia — nunca por
    cadencia.  INV-CROLE-8 proibe o retorno "apenas por cadencia", nao o retorno.
    """
    avatar = CURATED_FAMILY_ROLES[("Warrior", "Arms", "Avatar")]
    sweeping = CURATED_FAMILY_ROLES[("Warrior", "Arms", "Sweeping Strikes")]
    assert avatar.role is AbilityRole.OFFENSIVE_COOLDOWN
    assert sweeping.role is AbilityRole.SELF_OFFENSIVE_BUFF
    assert avatar.provenance == sweeping.provenance == "GATE-M5-batch1-decisions.json"
    # Expandidas SO onde observadas: Fury nunca castou Avatar.
    assert ("Warrior", "Fury", "Avatar") not in CURATED_FAMILY_ROLES
    assert ("Warrior", "Fury", "Pummel") in CURATED_FAMILY_ROLES


def test_cross_spec_decision_expands_only_to_observed_specs() -> None:
    """Uma decisao por NOME vira uma entrada por (class, spec) observado."""
    for spec in ("BeastMastery", "Marksmanship", "Survival"):
        assert CURATED_FAMILY_ROLES[("Hunter", spec, "Exhilaration")].role is AbilityRole.HEALING
    # Astral Shift so foi observada em dois specs de Shaman.
    assert ("Shaman", "Elemental", "Astral Shift") in CURATED_FAMILY_ROLES
    assert ("Shaman", "Enhancement", "Astral Shift") in CURATED_FAMILY_ROLES
    assert ("Shaman", "Restoration", "Astral Shift") not in CURATED_FAMILY_ROLES
