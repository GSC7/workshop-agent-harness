from __future__ import annotations

from fakes import MinioFake

from app.services.stores import garantir_bucket


async def test_bucket_existente_nao_e_recriado():
    minio = MinioFake(buckets={"relatorios"})

    await garantir_bucket(minio, "relatorios")  # type: ignore[arg-type]

    assert minio.criados == []


async def test_bucket_ausente_e_criado_uma_vez():
    minio = MinioFake()

    await garantir_bucket(minio, "relatorios")  # type: ignore[arg-type]
    await garantir_bucket(minio, "relatorios")  # type: ignore[arg-type]

    assert minio.criados == ["relatorios"]


async def test_minio_fora_nao_derruba_o_startup():
    minio = MinioFake(erro=ConnectionRefusedError())

    await garantir_bucket(minio, "relatorios")  # type: ignore[arg-type]

    assert minio.criados == []
