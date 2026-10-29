from __future__ import annotations

from fakes import VerificadorFake, VerificadorTravado

from app.config import settings


async def test_health_ok_quando_todos_os_stores_respondem(cliente, com_stores):
    com_stores(
        VerificadorFake("postgres", detalhe={"usuario": "agente_ro", "somente_leitura": True}),
        VerificadorFake("qdrant", detalhe={"colecoes_faltando": []}),
        VerificadorFake("minio", detalhe={"bucket": "relatorios"}),
    )

    resp = await cliente.get("/health")

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["status"] == "ok"
    assert set(corpo["stores"]) == {"postgres", "qdrant", "minio"}
    assert all(s["ok"] for s in corpo["stores"].values())
    assert corpo["stores"]["postgres"]["usuario"] == "agente_ro"


async def test_health_degradado_sem_vazar_erro_quando_postgres_cai(cliente, com_stores):
    com_stores(
        VerificadorFake("postgres", erro=ConnectionRefusedError("senha=segredo host=postgres")),
        VerificadorFake("qdrant", detalhe={"colecoes_faltando": []}),
        VerificadorFake("minio", detalhe={"bucket": "relatorios"}),
    )

    resp = await cliente.get("/health")

    assert resp.status_code == 503
    corpo = resp.json()
    assert corpo["status"] == "degradado"
    assert corpo["stores"]["postgres"] == {"ok": False, "erro": "ConnectionRefusedError"}
    assert corpo["stores"]["qdrant"]["ok"] is True
    assert "segredo" not in resp.text and "Traceback" not in resp.text


async def test_health_degradado_quando_store_reporta_problema(cliente, com_stores):
    com_stores(
        VerificadorFake("qdrant", ok=False, detalhe={"colecoes_faltando": ["prescricao"]}),
        VerificadorFake("minio", detalhe={"bucket": "relatorios"}),
    )

    resp = await cliente.get("/health")

    assert resp.status_code == 503
    assert resp.json()["stores"]["qdrant"] == {"ok": False, "colecoes_faltando": ["prescricao"]}


async def test_health_degradado_quando_store_estoura_timeout(cliente, com_stores, monkeypatch):
    monkeypatch.setattr(settings, "health_timeout_s", 0.01)
    com_stores(VerificadorTravado())

    resp = await cliente.get("/health")

    assert resp.status_code == 503
    assert resp.json()["stores"]["minio"] == {"ok": False, "erro": "TimeoutError"}


async def test_liveness_responde_mesmo_com_stores_fora(cliente, com_stores):
    # Healthcheck do container usa liveness: store fora não pode impedir o nginx de subir.
    com_stores(VerificadorFake("qdrant", erro=ConnectionRefusedError()))

    resp = await cliente.get("/health/live")

    assert resp.status_code == 200
    assert resp.json() == {"status": "vivo"}
