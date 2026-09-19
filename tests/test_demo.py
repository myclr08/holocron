"""Demo ortami: sahte Jira'nin JQL ayristirmasi ve tohumlama.

`tools/` bir paket degil; `tools/` dizini sys.path'e eklenerek `demo` paketi
ice aktarilir (test_packaging de tools/ dosyalarini boyle yukluyor).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from demo import sahte_jira, tohum, veri  # noqa: E402

SIMDI = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def kayitlar():
    return veri.kayitlar_uret(simdi=SIMDI)


@pytest.fixture
def sahte(kayitlar):
    return sahte_jira.SahteJira(kayitlar=kayitlar, saat=lambda: SIMDI)


# --- JQL ayristirma -----------------------------------------------------


def test_the_parser_splits_field_operator_and_value():
    sorgu = sahte_jira.ayristir("project = PRJ")
    (kosul,) = sorgu.kosullar
    assert (kosul.alan, kosul.islec, kosul.degerler) == ("project", "=", ["PRJ"])


def test_an_in_list_keeps_every_value():
    sorgu = sahte_jira.ayristir("status in (Açık, Geliştiriliyor, Test)")
    (kosul,) = sorgu.kosullar
    assert kosul.islec == "in"
    assert kosul.degerler == ["Açık", "Geliştiriliyor", "Test"]


def test_quoted_keys_survive_the_tokenizer():
    sorgu = sahte_jira.ayristir('key in ("PRJ-1", "OPS-2")')
    (kosul,) = sorgu.kosullar
    assert kosul.degerler == ["PRJ-1", "OPS-2"]


def test_not_in_and_is_not_empty_are_single_operators():
    kosullar = sahte_jira.ayristir(
        "status not in (Kapalı) AND duedate is not EMPTY"
    ).kosullar
    assert [kosul.islec for kosul in kosullar] == ["not in", "is not"]


def test_order_by_is_taken_out_of_the_conditions():
    sorgu = sahte_jira.ayristir("project = PRJ ORDER BY priority ASC, updated DESC")
    assert len(sorgu.kosullar) == 1
    assert sorgu.siralama == [("priority", "asc"), ("updated", "desc")]


def test_or_makes_a_second_group():
    sorgu = sahte_jira.ayristir("project = PRJ AND status = Açık OR project = OPS")
    assert [len(grup) for grup in sorgu.gruplar] == [2, 1]


def test_a_broken_query_is_a_jql_error():
    with pytest.raises(sahte_jira.JqlHatasi):
        sahte_jira.ayristir("project")


# --- JQL degerlendirme --------------------------------------------------


def test_current_user_resolves_to_the_demo_user(sahte):
    sonuc = sahte.ara("assignee = currentUser()", 0, 500)
    assert sonuc["total"] > 0
    for kayit in sonuc["issues"]:
        assert kayit["fields"]["assignee"]["name"] == veri.BEN["kullanici"]


def test_the_demo_user_is_spread_over_every_project(sahte):
    """Atama dagilimi proje sirasina yapismasin: filo tek projeye dusmesin."""
    sonuc = sahte.ara("assignee = currentUser()", 0, 500)
    projeler = {kayit["fields"]["project"]["key"] for kayit in sonuc["issues"]}
    assert projeler == {"PRJ", "OPS", "MOB"}


def test_a_relative_date_window_filters_by_updated(sahte, kayitlar):
    sonuc = sahte.ara("updated >= -10d", 0, 500)
    esik = SIMDI - timedelta(days=10)
    beklenen = [
        kayit
        for kayit in kayitlar
        if sahte_jira._an(kayit["fields"]["updated"]) >= esik
    ]
    assert sonuc["total"] == len(beklenen)


def test_status_in_and_project_combine_with_and(sahte):
    sonuc = sahte.ara("project = PRJ AND status in (Açık, Test)", 0, 500)
    for kayit in sonuc["issues"]:
        assert kayit["fields"]["project"]["key"] == "PRJ"
        assert kayit["fields"]["status"]["name"] in ("Açık", "Test")


def test_labels_match_any_member_of_the_list(sahte):
    sonuc = sahte.ara("labels = acil", 0, 500)
    assert sonuc["total"] > 0
    for kayit in sonuc["issues"]:
        assert "acil" in kayit["fields"]["labels"]


def test_order_by_updated_desc_really_sorts(sahte):
    sonuc = sahte.ara("project = PRJ ORDER BY updated DESC", 0, 500)
    damgalar = [kayit["fields"]["updated"] for kayit in sonuc["issues"]]
    assert damgalar == sorted(damgalar, reverse=True)


def test_paging_uses_start_at_and_max_results(sahte):
    ilk = sahte.ara("project = PRJ ORDER BY key ASC", 0, 5)
    ikinci = sahte.ara("project = PRJ ORDER BY key ASC", 5, 5)
    assert len(ilk["issues"]) == 5
    assert ilk["total"] == ikinci["total"]
    assert {kayit["key"] for kayit in ilk["issues"]}.isdisjoint(
        {kayit["key"] for kayit in ikinci["issues"]}
    )


def test_an_unknown_key_fails_the_whole_batch(sahte):
    """Gercek Jira da 400 veriyor; Holocron paketi bolerek kurtariyor."""
    with pytest.raises(sahte_jira.JqlHatasi):
        sahte.ara('key in ("PRJ-999999")', 0, 10)


def test_a_field_selector_trims_the_payload(sahte):
    sonuc = sahte.ara("project = PRJ", 0, 1, ["summary", "status"])
    alanlar = sonuc["issues"][0]["fields"]
    assert set(alanlar) == {"summary", "status"}


def test_navigable_returns_every_field(sahte):
    sonuc = sahte.ara("project = PRJ", 0, 1, ["*navigable"])
    assert "customfield_10001" in sonuc["issues"][0]["fields"]


# --- canlandirma --------------------------------------------------------


def test_refreshing_moves_a_few_records(sahte):
    once = {kayit["key"]: kayit["fields"]["status"]["name"] for kayit in sahte.kayitlar}
    degisen = sahte.canlandir(zorla=True)
    assert degisen
    sonra = {kayit["key"]: kayit["fields"]["status"]["name"] for kayit in sahte.kayitlar}
    assert any(once[anahtar] != sonra[anahtar] for anahtar in degisen)


def test_the_cooldown_stops_a_second_run(sahte):
    assert sahte.canlandir(zorla=True)
    assert sahte.canlandir() == []


# --- alan katalogu ------------------------------------------------------


def test_every_generated_field_is_in_the_catalog(kayitlar):
    katalog = {alan["id"] for alan in veri.ALAN_KATALOGU}
    for anahtar in kayitlar[0]["fields"]:
        assert anahtar in katalog


def test_no_address_leaves_example_com(kayitlar):
    adresler = {
        kayitlar[0]["fields"][rol]["emailAddress"] for rol in ("assignee", "reporter")
    }
    assert all(adres.endswith("@example.com") for adres in adresler)


# --- tohumlama ----------------------------------------------------------


def test_the_settings_point_holocron_at_the_fake_server(context):
    tohum.ayarlari_yaz(context, "http://127.0.0.1:8090")
    yapilandirma = context.settings.jira_config()
    assert yapilandirma.base_url == "http://127.0.0.1:8090"
    assert yapilandirma.secret == tohum.SAHTE_PAT
    # Kurumsal vekil demo sunucusunun onune gecmesin.
    assert yapilandirma.proxy_mode == "direct"
    # Acilis animasyonu demo ekraninda beklemesin.
    assert context.settings.get("ui.crawl_seen") == "1"


def test_four_fleets_two_of_them_filtered(context):
    filolar = tohum.filolari_kur(context)
    assert len(filolar) == 4
    turler = sorted(filo["kind"] for filo in filolar)
    assert turler == ["filter", "filter", "manual", "manual"]
    for filo in filolar:
        if filo["kind"] == "filter":
            assert filo["jql"]
        assert filo["columns"]


def test_seeding_the_fleets_twice_does_not_duplicate(context):
    tohum.filolari_kur(context)
    tohum.filolari_kur(context)
    assert len(tohum.filolari_kur(context)) == 4


def test_every_seeded_jql_is_understood_by_the_fake_server(sahte):
    for tanim in tohum.FILOLAR:
        if tanim["kind"] != "filter":
            continue
        sonuc = sahte.ara(tanim["jql"], 0, 500, ["*navigable"])
        assert sonuc["total"] > 0, tanim["name"]


def test_the_manual_fleets_get_their_keys(context, sahte):
    tohum.filolari_kur(context)
    havuz = sorted(sahte.anahtarlar())
    tohum.elle_uyeleri_ekle(context, havuz[:20])
    conn = context.connection()
    from app import repository

    elle = [
        grup
        for grup in repository.list_groups(conn)
        if grup["kind"] == repository.KIND_MANUAL
    ]
    assert [grup["count"] for grup in elle] == [9, 6]


def test_the_seeded_data_lands_in_holocrons_own_tables(context, sahte):
    """Tohumlama uctan uca: kayitlar yazilmis gibi geri kalani kurulur."""
    from app import gamify_repo, repository

    conn = context.connection()
    repository.upsert_issues(conn, sahte.kayitlar[:30])
    anahtarlar = [str(kayit["key"]) for kayit in sahte.kayitlar[:30]]
    tohum.geri_kalani_kur(context, anahtarlar, SIMDI)

    assert len(repository.list_local_fields(conn)) == len(tohum.YEREL_ALANLAR)
    gorevler = repository.list_tasks(conn)
    # 12 elle gorev + posta konusmalarindan gelenler.
    assert len(gorevler) == len(tohum.GOREVLER) + len(tohum.POSTA_KONUSMALARI)
    assert {gorev["status"] for gorev in gorevler} == {"todo", "doing", "done"}
    assert any(gorev["issue_key"] for gorev in gorevler)

    kisiler = repository.list_contacts(conn)
    assert len(kisiler) >= 15
    assert sum(1 for kisi in kisiler if kisi["kind"] == "list") == 2
    assert all(kisi["email"].endswith("@example.com") for kisi in kisiler)

    sefer = gamify_repo.active_campaign(conn)
    assert sefer is not None
    # Rutbe ilerlemesi gorunsun: hedefin en az yarisi kazanilmis olsun.
    assert gamify_repo.total_xp(conn, sefer["id"]) > sefer["target_xp"] // 2
    assert len(gamify_repo.earned_badges(conn, sefer["id"])) >= len(
        tohum.KAZANILAN_ROZETLER
    )
    assert gamify_repo.list_quests(conn, sefer["id"])


def test_seeding_is_idempotent(context, sahte):
    from app import repository

    conn = context.connection()
    repository.upsert_issues(conn, sahte.kayitlar[:30])
    anahtarlar = [str(kayit["key"]) for kayit in sahte.kayitlar[:30]]
    tohum.geri_kalani_kur(context, anahtarlar, SIMDI)
    once = len(repository.list_tasks(conn))
    tohum.geri_kalani_kur(context, anahtarlar, SIMDI)
    assert len(repository.list_tasks(conn)) == once


def test_the_mail_tasks_look_like_they_came_from_outlook(context, sahte):
    from app import repository

    conn = context.connection()
    repository.upsert_issues(conn, sahte.kayitlar[:30])
    tohum.posta_gorevleri_kur(context, SIMDI)
    postalilar = [
        gorev
        for gorev in repository.list_tasks(conn)
        if gorev["source"] == repository.TASK_SOURCE_MAIL
    ]
    assert len(postalilar) == len(tohum.POSTA_KONUSMALARI)
    for gorev in postalilar:
        assert gorev["mail_conversation_id"]
        assert gorev["mail_sender"].endswith("@example.com")
        assert repository.get_mail_conversation(conn, gorev["mail_conversation_id"])
