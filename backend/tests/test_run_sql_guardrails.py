from __future__ import annotations

import pytest

from agent.tools.run_sql import SQLRejeitado, validar_sql

TETO = 500


def test_select_sem_limit_recebe_o_teto():
    sql = validar_sql("SELECT regiao_id, sum(valor_total) FROM pedidos GROUP BY 1", TETO)

    assert sql.endswith("LIMIT 500")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO pedidos (id) VALUES (1)",
        "UPDATE pedidos SET status = 'pago'",
        "DELETE FROM pedidos",
        "DROP TABLE pedidos",
        "CREATE TABLE x (i int)",
        "ALTER TABLE pedidos ADD COLUMN x int",
        "TRUNCATE pedidos",
        "GRANT SELECT ON pedidos TO public",
        "VACUUM pedidos",
        "WITH d AS (DELETE FROM pedidos RETURNING *) SELECT * FROM d",
        "SELECT * INTO copia FROM pedidos",
    ],
)
def test_escrita_e_ddl_sao_rejeitadas(sql):
    with pytest.raises(SQLRejeitado):
        validar_sql(sql, TETO)


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1; DROP TABLE pedidos", "SELECT 1; SELECT 2", "", "   ;  "],
)
def test_exige_exatamente_uma_instrucao(sql):
    with pytest.raises(SQLRejeitado, match="uma instrução"):
        validar_sql(sql, TETO)


def test_ponto_e_virgula_em_string_e_comentario_nao_vira_segunda_instrucao():
    sql = validar_sql("SELECT 'a;b' AS x -- ; DROP TABLE pedidos", TETO)

    assert "DROP" not in sql.upper()


def test_select_for_update_e_rejeitado():
    with pytest.raises(SQLRejeitado):
        validar_sql("SELECT * FROM pedidos FOR UPDATE", TETO)


def test_sql_ilegivel_e_rejeitado_com_mensagem_legivel():
    with pytest.raises(SQLRejeitado, match="SQL inválido"):
        validar_sql("SELEC * FORM pedidos ((", TETO)


def test_limit_acima_do_teto_e_rebaixado():
    assert validar_sql("SELECT * FROM pedidos LIMIT 100000", TETO).endswith("LIMIT 500")


def test_limit_dentro_do_teto_e_mantido():
    assert validar_sql("SELECT * FROM pedidos LIMIT 10", TETO).endswith("LIMIT 10")


def test_limit_nao_literal_e_rejeitado():
    with pytest.raises(SQLRejeitado, match="LIMIT"):
        validar_sql("SELECT * FROM pedidos LIMIT (SELECT 10)", TETO)


def test_union_recebe_limit_no_resultado_final():
    sql = validar_sql("SELECT id FROM pedidos UNION SELECT id FROM clientes", TETO)

    assert sql.endswith("LIMIT 500")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM harness.runs",
        "SELECT * FROM public.qualquer",
        "SELECT * FROM pg_roles",
        "SELECT * FROM pg_catalog.pg_authid",
        "SELECT p.id FROM pedidos p JOIN harness.runs r ON r.id = p.id",
    ],
)
def test_so_o_schema_negocio_e_consultavel(sql):
    with pytest.raises(SQLRejeitado, match="negocio"):
        validar_sql(sql, TETO)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM negocio.pedidos",
        "WITH m AS (SELECT * FROM metas) SELECT * FROM m JOIN regioes r ON r.id = m.regiao_id",
        "SELECT date_trunc('month', data_pedido), sum(valor_total) FROM pedidos GROUP BY 1",
        "SELECT * FROM generate_series(1, 3)",
    ],
)
def test_consultas_legitimas_ao_negocio_passam(sql):
    assert validar_sql(sql, TETO).endswith("LIMIT 500")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT set_config('default_transaction_read_only', 'off', false)",
        "SELECT pg_sleep(60)",
        "SELECT pg_terminate_backend(1)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM dblink('host=x', 'DELETE FROM pedidos') AS t(i int)",
        "SELECT lo_import('/etc/passwd')",
    ],
)
def test_funcoes_com_efeito_colateral_sao_rejeitadas(sql):
    with pytest.raises(SQLRejeitado, match="função"):
        validar_sql(sql, TETO)


@pytest.mark.parametrize(
    "sql",
    [
        # SQL passado como texto escapa da checagem de schema: função desconhecida não passa.
        "SELECT query_to_xml('SELECT * FROM pg_catalog.pg_authid', true, false, '')",
        "SELECT table_to_xml('information_schema.tables', true, false, '')",
        "SELECT schema_to_xml('harness', true, false, '')",
        "SELECT * FROM ts_stat('SELECT 1')",
        "SELECT funcao_qualquer_desconhecida(1)",
        # nome qualificado/entre aspas resolve para o nome real
        "SELECT pg_catalog.set_config('default_transaction_read_only', 'off', false)",
        "SELECT \"set_config\"('default_transaction_read_only', 'off', false)",
    ],
)
def test_funcao_desconhecida_ou_disfarcada_e_rejeitada(sql):
    with pytest.raises(SQLRejeitado, match="função"):
        validar_sql(sql, TETO)


def test_funcoes_analiticas_usuais_passam():
    sql = (
        "SELECT date_trunc('month', data_pedido), extract(year FROM data_pedido),"
        " to_char(data_pedido, 'YYYY-MM'), sum(valor_total), avg(valor_total),"
        " round(stddev(valor_total), 2), coalesce(nullif(desconto, 0), 1),"
        " lag(sum(valor_total)) OVER (ORDER BY 1), age(max(data_pedido)),"
        " make_date(2024, 1, 1), percentile_cont(0.5) WITHIN GROUP (ORDER BY valor_total)"
        " FROM pedidos GROUP BY 1, 2, 3"
    )

    assert validar_sql(sql, TETO).endswith("LIMIT 500")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pedidos FETCH FIRST 100000 ROWS ONLY",
        "SELECT * FROM pedidos LIMIT ALL",
        "SELECT * FROM pedidos LIMIT NULL",
        "SELECT * FROM pedidos LIMIT '100000'",
    ],
)
def test_formas_alternativas_de_limit_sao_rejeitadas(sql):
    with pytest.raises(SQLRejeitado, match="LIMIT"):
        validar_sql(sql, TETO)


def test_limit_de_subconsulta_nao_escapa_do_teto_da_raiz():
    sql = validar_sql("SELECT * FROM (SELECT * FROM pedidos LIMIT 99999) s", TETO)

    assert sql.endswith("LIMIT 500")
