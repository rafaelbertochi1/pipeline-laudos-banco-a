"""Relatório de qualidade (checar_qualidade_dados.py): separação das
consultas do .sql e a tabela de texto do relatório. O banco não é aberto."""
import checar_qualidade_dados as cq


def test_le_as_consultas_do_sql_do_repositorio():
    """Cada bloco "-- @@ título" vira uma consulta com a nota "Esperado"."""
    consultas = cq.carregar_consultas(cq.ARQUIVO_SQL)
    assert len(consultas) >= 5
    titulo, nota, sql = consultas[0]
    assert titulo == "Panorama dos laudos: campos vazios por modelo"
    assert nota.startswith("Esperado:")
    assert sql.startswith("SELECT") and "laudos_banco_a" in sql
    for titulo, _nota, sql in consultas:
        assert titulo and not sql.startswith("--"), titulo


def test_cabecalho_do_arquivo_e_bloco_vazio_ficam_de_fora(tmp_path):
    arquivo = tmp_path / "consultas.sql"
    arquivo.write_text(
        "-- comentário geral do arquivo\n\n"
        "-- @@ Primeira\n-- Esperado: zero\n-- (segunda linha da nota)\nSELECT 1;\n\n"
        "-- @@ Só título\n-- sem SQL\n\n"
        "-- @@ Segunda\nSELECT 2;\n",
        encoding="utf-8")
    assert cq.carregar_consultas(str(arquivo)) == [
        ("Primeira", "Esperado: zero (segunda linha da nota)", "SELECT 1;"),
        ("Segunda", "", "SELECT 2;"),
    ]


def test_tabela_alinhada_no_estilo_do_psql():
    assert cq.formatar_tabela(["modelo", "total"], [("fisico", 120), ("eletronico", None)]) == (
        "  modelo     | total\n"
        "  -----------+------\n"
        "  fisico     | 120\n"
        "  eletronico | NULL"
    )


def test_celula_comprida_e_cortada():
    texto = "x" * 60
    saida = cq.formatar_tabela(["coluna"], [(texto,)])
    assert ("x" * (cq.LARGURA_MAXIMA_COLUNA - 1) + "…") in saida
    assert "x" * cq.LARGURA_MAXIMA_COLUNA not in saida


def test_sem_linhas():
    assert cq.formatar_tabela(["a"], []) == "  (nenhuma linha)"
